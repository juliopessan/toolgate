"""Layer 7 - persistence (ADR-001).

Two purposes, and deliberately not a general data layer:

1. **Index cache.** Re-chunking and re-indexing a tree on every invocation is the
   one scenario where this toolkit costs more than it saves. A hook that fires on
   every tool call cannot pay seconds of cold indexing per call. Chunks are cached
   against a content fingerprint, so a warm run reads rows instead of re-parsing.

2. **Event ledger.** Which suggestions were offered, which were taken, and what
   they actually saved. Without it we only ever measure the saving that *was
   available*, never the saving that was *realised*.

Three rules govern this module:

* **Zero new dependencies.** ``sqlite3`` ships with Python. That is the whole
  reason this decision is cheap; a persistence layer that cost a native
  extension would not be worth the same trade.
* **Optional, always.** A read-only filesystem, a sandbox, a full disk - any of
  these means no database, and every method becomes a no-op. Persistence must
  never be the reason a read fails.
* **Local, never shared.** The ledger records file paths and query text. It
  lives under the workspace, is meant to be gitignored, and nothing in this
  module opens a socket.

Invalidation is by content, never by time. A stale cache is worse than no cache:
it returns line ranges that no longer exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .chunking import Chunk
from .semantic import Hit, tokenize

#: Bumped whenever the cache tables change shape. On mismatch the cache is
#: rebuilt from scratch - it is derived data, so throwing it away is always safe.
#: The ledger is preserved across schema changes, because it is not derivable.
SCHEMA_VERSION = 3

#: Directory created inside the workspace. Add it to .gitignore.
STORE_DIRNAME = ".tools-tokens"
STORE_FILENAME = "index.db"

#: Hooks fire in bursts and several agents may share one workspace. WAL gives
#: many concurrent readers alongside a single writer, which is exactly this
#: access pattern; the timeout absorbs the brief overlap between two writers.
_BUSY_TIMEOUT_MS = 4000

#: How long an offer stays claimable. An agent that acts on a suggestion does so
#: within seconds; crediting a read to an offer made an hour ago would inflate
#: the ledger with coincidences.
OFFER_WINDOW_SECONDS = 900

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    language    TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id       TEXT PRIMARY KEY,
    path     TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    ordinal  INTEGER NOT NULL,
    start    INTEGER NOT NULL,
    end      INTEGER NOT NULL,
    symbol   TEXT,
    kind     TEXT,
    tokens   INTEGER NOT NULL,
    language TEXT,
    meta     TEXT,
    text     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks(path, ordinal);

-- Full-text ranking without giving up code-aware tokenisation.
--
-- FTS5 cannot take a custom tokeniser from Python, and its built-in ones do not
-- split camelCase -- which is exactly the property that lets "refresh token"
-- find refreshAccessToken. The way through is to tokenise with our own splitter
-- and store the resulting TERM STREAM here, under a trivial tokeniser. SQLite's
-- compiled bm25() then ranks over our tokens rather than its own.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunk_id UNINDEXED,
    terms,
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    tool          TEXT,
    path          TEXT,
    tokens_full   INTEGER NOT NULL DEFAULT 0,
    tokens_served INTEGER NOT NULL DEFAULT 0,
    pressure      TEXT,
    -- Set once an offer has been matched to an actual narrow read, so the same
    -- offer cannot be credited twice.
    claimed       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS events_by_ts ON events(ts);
"""

#: Realised saving, derived from the ledger rather than written by hand. An
#: offer that was never taken saved nothing, and this view says so.
_SAVINGS_VIEW = """
CREATE VIEW IF NOT EXISTS realized_savings AS
SELECT
    path,
    sum(kind = 'suggested') AS offers,
    sum(kind = 'accepted')  AS accepted,
    sum(CASE WHEN kind = 'accepted' THEN tokens_full - tokens_served ELSE 0 END) AS tokens_saved
FROM events
WHERE path IS NOT NULL
GROUP BY path;
"""


class StoreIndex:
    """A searchable index backed by the persisted FTS5 table.

    Interface-compatible with the in-memory index, so callers do not branch. The
    difference is what happens on a warm run: instead of re-reading every chunk
    into memory and rebuilding BM25 postings, the ranking already exists on disk
    and SQLite answers the query directly.

    A ``vector`` index may be supplied for hybrid retrieval; without one this is
    lexical only, which is the zero-dependency default.
    """

    def __init__(self, store: "Store", vector=None) -> None:
        self.store = store
        self.vector = vector

    def __len__(self) -> int:
        return self.store.indexed_chunk_count()

    @property
    def chunks(self) -> List[Chunk]:
        """Every cached chunk. Materialises the whole index - avoid on hot paths."""
        return self.store.all_chunks()

    def search(self, query: str, k: int = 10, over_fetch: int = 3) -> List[Hit]:
        depth = max(k * over_fetch, k)
        lexical = [
            Hit(chunk=chunk, score=score, source="lexical", rank=position + 1)
            for position, (chunk, score) in enumerate(self.store.search(query, depth))
        ]
        if self.vector is None:
            return lexical[:k]
        from .semantic import reciprocal_rank_fusion

        return reciprocal_rank_fusion([lexical, self.vector.search(query, depth)])[:k]


def fingerprint(source: str, path: str = "") -> str:
    """Content fingerprint used to decide whether a cached entry is still valid.

    The hash alone would be enough for correctness; size and mtime are folded in
    so two files that collide on neither are distinguished without reading the
    hash, and so a touched-but-unchanged file is still recognised as unchanged.
    """
    digest = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()[:32]
    size = len(source)
    return f"{size}:{digest}"


#: Symbol names are repeated in the term stream so that naming a symbol in the
#: query is a strong signal rather than one term among hundreds -- the same boost
#: the in-memory index applies, kept in sync deliberately.
_SYMBOL_BOOST = 2


def _term_stream(chunk: Chunk) -> str:
    """The code-aware token stream FTS5 will index for one chunk."""
    boost = ((chunk.symbol + " ") * _SYMBOL_BOOST) if chunk.symbol else ""
    return " ".join(tokenize(f"{chunk.path} {boost}{chunk.text}"))


def _match_expression(query: str) -> str:
    """Turn a natural-language query into an FTS5 MATCH expression.

    Terms are double-quoted so that a token which happens to collide with FTS5
    syntax (``AND``, ``NOT``, ``*``) is matched literally instead of parsed as an
    operator -- otherwise a query containing the word "not" changes its own
    meaning.
    """
    terms = tokenize(query)
    if not terms:
        return ""
    return " OR ".join('"%s"' % term.replace('"', '""') for term in dict.fromkeys(terms))


def default_store_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), STORE_DIRNAME, STORE_FILENAME)


@dataclass
class SavingsRow:
    path: str
    offers: int
    accepted: int
    tokens_saved: int

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "offers": self.offers,
            "accepted": self.accepted,
            "tokens_saved": self.tokens_saved,
            "acceptance_rate": round(self.accepted / self.offers, 3) if self.offers else 0.0,
        }


class Store:
    """A local SQLite store that degrades to a no-op when it cannot open.

    Check :attr:`available` if you want to know whether persistence is live;
    every method is safe to call either way.

    >>> store = Store(root="/nonexistent/read-only")
    >>> store.available
    False
    >>> store.cached_chunks("a.py", "deadbeef") is None
    True
    """

    def __init__(self, root: str = ".", path: Optional[str] = None, enabled: bool = True) -> None:
        self.root = os.path.abspath(root)
        self.path = path or default_store_path(self.root)
        self._conn: Optional[sqlite3.Connection] = None
        self.unavailable_reason: Optional[str] = None
        if not enabled:
            self.unavailable_reason = "disabled by caller"
            return
        self._open()

    # ---- lifecycle -------------------------------------------------------

    def _open(self) -> None:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            # Durability is not worth a fsync per write here: everything cached is
            # rebuildable from the source tree, and a lost ledger row costs a
            # statistic, not correctness.
            conn.execute("PRAGMA synchronous = NORMAL")

            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.executescript(_SCHEMA)
            # The ledger is not derivable, so it is migrated in place rather than
            # dropped with the cache.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "claimed" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0")
            conn.executescript(_SAVINGS_VIEW)
            if version != SCHEMA_VERSION:
                # Cache is derived data: drop it rather than migrate. The ledger
                # is not derivable, so it survives untouched.
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM chunk_fts")
                conn.execute("DELETE FROM files")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            self._conn = conn
        except (sqlite3.Error, OSError) as error:
            self._conn = None
            self.unavailable_reason = f"{type(error).__name__}: {error}"

    @property
    def available(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- index cache -----------------------------------------------------

    def cached_chunks(self, path: str, content_fingerprint: str) -> Optional[List[Chunk]]:
        """Chunks for ``path`` if the cache holds them at this exact fingerprint.

        Returns ``None`` on a miss, on a stale entry, or when the store is
        unavailable - callers re-parse and move on.
        """
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT fingerprint FROM files WHERE path = ?", (path,)
            ).fetchone()
            if row is None or row["fingerprint"] != content_fingerprint:
                return None
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE path = ? ORDER BY ordinal", (path,)
            ).fetchall()
        except sqlite3.Error:
            return None

        chunks: List[Chunk] = []
        for row in rows:
            try:
                meta = json.loads(row["meta"]) if row["meta"] else {}
            except ValueError:
                meta = {}
            chunks.append(
                Chunk(
                    id=row["id"],
                    text=row["text"],
                    path=row["path"],
                    start=row["start"],
                    end=row["end"],
                    tokens=row["tokens"],
                    language=row["language"] or "",
                    symbol=row["symbol"],
                    kind=row["kind"] or "window",
                    meta=meta,
                )
            )
        return chunks

    def put_chunks(
        self, path: str, content_fingerprint: str, language: str, chunks: Sequence[Chunk]
    ) -> bool:
        """Replace the cached entry for ``path``. Returns False if not stored."""
        if self._conn is None:
            return False
        try:
            with self._conn:
                stale = [r[0] for r in self._conn.execute(
                    "SELECT id FROM chunks WHERE path = ?", (path,)
                ).fetchall()]
                if stale:
                    self._conn.executemany(
                        "DELETE FROM chunk_fts WHERE chunk_id = ?", [(cid,) for cid in stale]
                    )
                self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
                self._conn.execute(
                    "INSERT INTO files(path, fingerprint, language, chunk_count, indexed_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint, language=excluded.language, "
                    "chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
                    (path, content_fingerprint, language, len(chunks), int(time.time())),
                )
                self._conn.executemany(
                    "INSERT OR REPLACE INTO chunks"
                    "(id, path, ordinal, start, end, symbol, kind, tokens, language, meta, text) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            chunk.id, path, ordinal, chunk.start, chunk.end, chunk.symbol,
                            chunk.kind, chunk.tokens, chunk.language,
                            json.dumps(chunk.meta) if chunk.meta else None, chunk.text,
                        )
                        for ordinal, chunk in enumerate(chunks)
                    ],
                )
                self._conn.executemany(
                    "INSERT INTO chunk_fts(chunk_id, terms) VALUES(?,?)",
                    [(chunk.id, _term_stream(chunk)) for chunk in chunks],
                )
        except sqlite3.Error:
            return False
        return True

    def forget_missing(self, known_paths: Iterable[str]) -> int:
        """Drop cache entries for files that no longer exist in the tree."""
        if self._conn is None:
            return 0
        keep = set(known_paths)
        try:
            rows = self._conn.execute("SELECT path FROM files").fetchall()
            gone = [r["path"] for r in rows if r["path"] not in keep]
            if not gone:
                return 0
            with self._conn:
                stale = [
                    r[0] for r in self._conn.execute(
                        "SELECT id FROM chunks WHERE path IN (%s)" % ",".join("?" * len(gone)),
                        gone,
                    ).fetchall()
                ]
                if stale:
                    self._conn.executemany(
                        "DELETE FROM chunk_fts WHERE chunk_id = ?", [(cid,) for cid in stale]
                    )
                self._conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in gone])
            return len(gone)
        except sqlite3.Error:
            return 0

    # ---- search ----------------------------------------------------------

    def search(self, query: str, k: int = 10) -> List[tuple]:
        """Rank cached chunks with SQLite's compiled BM25.

        Returns ``[(Chunk, score)]``, best first, with scores negated so that
        higher is better -- SQLite's ``bm25()`` returns smaller-is-better values,
        which would silently invert any caller that sorts descending.

        Returns ``[]`` when the store is unavailable, the query has no usable
        terms, or nothing matches; callers fall back to the in-memory index.
        """
        if self._conn is None:
            return []
        expression = _match_expression(query)
        if not expression:
            return []
        try:
            rows = self._conn.execute(
                "SELECT c.*, -bm25(chunk_fts) AS score "
                "FROM chunk_fts JOIN chunks c ON c.id = chunk_fts.chunk_id "
                "WHERE chunk_fts MATCH ? ORDER BY score DESC LIMIT ?",
                (expression, k),
            ).fetchall()
        except sqlite3.Error:
            return []

        results: List[tuple] = []
        for row in rows:
            try:
                meta = json.loads(row["meta"]) if row["meta"] else {}
            except ValueError:
                meta = {}
            results.append((
                Chunk(
                    id=row["id"], text=row["text"], path=row["path"],
                    start=row["start"], end=row["end"], tokens=row["tokens"],
                    language=row["language"] or "", symbol=row["symbol"],
                    kind=row["kind"] or "window", meta=meta,
                ),
                float(row["score"]),
            ))
        return results

    def is_fresh(self, path: str, content_fingerprint: str) -> bool:
        """True when the cache already holds this exact content.

        Deliberately cheaper than :meth:`cached_chunks`: it answers the freshness
        question without materialising a single row, which is what lets a warm
        sync skip untouched files entirely.
        """
        if self._conn is None:
            return False
        try:
            row = self._conn.execute(
                "SELECT 1 FROM files WHERE path = ? AND fingerprint = ?",
                (path, content_fingerprint),
            ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def all_chunks(self) -> List[Chunk]:
        """Every cached chunk, in path order."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute("SELECT * FROM chunks ORDER BY path, ordinal").fetchall()
        except sqlite3.Error:
            return []
        out: List[Chunk] = []
        for row in rows:
            try:
                meta = json.loads(row["meta"]) if row["meta"] else {}
            except ValueError:
                meta = {}
            out.append(Chunk(
                id=row["id"], text=row["text"], path=row["path"], start=row["start"],
                end=row["end"], tokens=row["tokens"], language=row["language"] or "",
                symbol=row["symbol"], kind=row["kind"] or "window", meta=meta,
            ))
        return out

    def indexed_chunk_count(self) -> int:
        """How many chunks are searchable right now."""
        if self._conn is None:
            return 0
        try:
            return self._conn.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]
        except sqlite3.Error:
            return 0

    # ---- ledger ----------------------------------------------------------

    def record(
        self,
        kind: str,
        tool: Optional[str] = None,
        path: Optional[str] = None,
        tokens_full: int = 0,
        tokens_served: int = 0,
        pressure: Optional[str] = None,
    ) -> bool:
        """Append one event. ``kind`` is one of suggested | accepted | ignored | packed."""
        if self._conn is None:
            return False
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO events(ts, kind, tool, path, tokens_full, tokens_served, pressure) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (int(time.time()), kind, tool, path, int(tokens_full), int(tokens_served), pressure),
                )
        except sqlite3.Error:
            return False
        return True

    def claim_offer(
        self, path: str, tokens_served: int, tool: str = "narrow-read",
        window_seconds: int = OFFER_WINDOW_SECONDS,
    ) -> Optional[int]:
        """Turn a standing offer for ``path`` into a realised saving.

        This is what makes the ledger work without host cooperation. Asking every
        host to report back is a contract nobody implements; observing that the
        narrow read *actually happened* needs no contract at all. A skeleton or
        symbol read on a path with a standing offer is the acceptance.

        An offer is claimable once, and only within ``window_seconds`` -- crediting
        a read to an offer made yesterday would turn the ledger into fiction.

        Returns the tokens saved, or ``None`` when there was nothing to claim.
        """
        if self._conn is None:
            return None
        cutoff = int(time.time()) - window_seconds
        try:
            row = self._conn.execute(
                "SELECT id, tokens_full FROM events "
                "WHERE kind = 'suggested' AND path = ? AND ts >= ? AND claimed = 0 "
                "ORDER BY ts DESC LIMIT 1",
                (path, cutoff),
            ).fetchone()
            if row is None:
                return None
            with self._conn:
                self._conn.execute("UPDATE events SET claimed = 1 WHERE id = ?", (row["id"],))
                self._conn.execute(
                    "INSERT INTO events(ts, kind, tool, path, tokens_full, tokens_served, claimed) "
                    "VALUES(?,'accepted',?,?,?,?,1)",
                    (int(time.time()), tool, path, row["tokens_full"], int(tokens_served)),
                )
        except sqlite3.Error:
            return None
        return max(0, row["tokens_full"] - int(tokens_served))

    def pending_offers(self, window_seconds: int = OFFER_WINDOW_SECONDS) -> List[str]:
        """Paths with a standing, unclaimed offer inside the window."""
        if self._conn is None:
            return []
        cutoff = int(time.time()) - window_seconds
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT path FROM events "
                "WHERE kind = 'suggested' AND claimed = 0 AND ts >= ? AND path IS NOT NULL",
                (cutoff,),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [r["path"] for r in rows]

    def realized_savings(self, limit: int = 20) -> List[SavingsRow]:
        """Per-file realised saving, best first. Empty when nothing was recorded."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT path, offers, accepted, tokens_saved FROM realized_savings "
                "ORDER BY tokens_saved DESC, path LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            SavingsRow(r["path"], r["offers"] or 0, r["accepted"] or 0, r["tokens_saved"] or 0)
            for r in rows
        ]

    def summary(self) -> Dict[str, Any]:
        """Everything the ``stats`` command needs, in one round trip."""
        base: Dict[str, Any] = {
            "available": self.available,
            "path": self.path,
            "reason": self.unavailable_reason,
            "files_cached": 0,
            "chunks_cached": 0,
            "events": 0,
            "offers": 0,
            "accepted": 0,
            "tokens_saved": 0,
            "acceptance_rate": 0.0,
        }
        if self._conn is None:
            return base
        try:
            base["files_cached"] = self._conn.execute("SELECT count(*) FROM files").fetchone()[0]
            base["chunks_cached"] = self._conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            base["events"] = self._conn.execute("SELECT count(*) FROM events").fetchone()[0]
            row = self._conn.execute(
                "SELECT sum(offers) o, sum(accepted) a, sum(tokens_saved) s FROM realized_savings"
            ).fetchone()
            base["offers"] = row["o"] or 0
            base["accepted"] = row["a"] or 0
            base["tokens_saved"] = row["s"] or 0
            if base["offers"]:
                base["acceptance_rate"] = round(base["accepted"] / base["offers"], 3)
            try:
                base["size_bytes"] = os.path.getsize(self.path)
            except OSError:
                base["size_bytes"] = 0
        except sqlite3.Error as error:
            base["reason"] = f"read failed: {error}"
        return base

    # ---- privacy ---------------------------------------------------------

    def purge(self, cache_only: bool = False) -> bool:
        """Delete stored data. ``cache_only`` keeps the ledger.

        The ledger holds file paths and, indirectly, what someone was working on.
        Deleting it must be one obvious command, not a documented SQL snippet.
        """
        if self._conn is None:
            return False
        try:
            with self._conn:
                self._conn.execute("DELETE FROM chunks")
                self._conn.execute("DELETE FROM chunk_fts")
                self._conn.execute("DELETE FROM files")
                if not cache_only:
                    self._conn.execute("DELETE FROM events")
            self._conn.execute("VACUUM")
        except sqlite3.Error:
            return False
        return True
