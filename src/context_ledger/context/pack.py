"""Layer 6 - the packer.

Every layer below produces a piece of the answer. This one decides what actually
gets sent, and it is where the four ideas meet:

    retrieval says what is *relevant*      (semantic)
    structure says what is *quotable*      (astx / chunking)
    accounting says what it *costs*        (tokens)
    headroom says what you can *afford*    (headroom)

The selection is a greedy knapsack over score density (score per token), which
is the right objective: given a fixed budget, two strong small chunks beat one
strong large one. Under pressure the packer degrades gracefully rather than
truncating - it drops to skeletons before it drops to half-quoted functions,
because a signature the model can act on beats a fragment it cannot.

Everything emitted is cited. A packed context whose provenance you cannot trace
is a context you cannot debug.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Sequence

from .astx import detect_language, skeleton
from .chunking import Chunk, chunk_source
from .headroom import Headroom, Lane
from .semantic import Embedder, Hit, HybridIndex, build_index
from .store import Store, StoreIndex, fingerprint
from .tokens import estimate_tokens, trim_to_budget

#: Files that are never worth indexing. Vendored trees dominate any corpus they
#: are in, and they push the code you actually own out of the results.
DEFAULT_EXCLUDES = (
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor",
    ".next", ".nuxt", "coverage", ".tox", "site-packages",
)

#: Extensions worth reading as source. Anything else is skipped rather than
#: guessed at - a 4 MB binary that decodes as UTF-8 is still not context.
DEFAULT_INCLUDE_EXTENSIONS = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".cs", ".rb", ".php", ".swift", ".scala", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".sh", ".bash", ".sql", ".md", ".yml", ".yaml",
    ".json", ".toml", ".tf",
)

MAX_FILE_BYTES = 1_500_000


#: A candidate this heavily covered by what is already selected is paying for
#: lines the model has. Chunking deliberately overlaps its windows, so without
#: this the overlap is billed once per window. Below the threshold a partial
#: overlap is kept: a wider range that merely contains a selected slice still
#: adds the surrounding context that made it rank.
OVERLAP_DROP_RATIO = 0.70


def _overlap_ratio(start: int, end: int, claimed_ranges: Sequence[tuple]) -> float:
    """Fraction of lines ``start..end`` already covered by ``claimed_ranges``."""
    span = max(1, end - start + 1)
    covered = set()
    for claimed_start, claimed_end in claimed_ranges:
        low = max(start, claimed_start)
        high = min(end, claimed_end)
        if low <= high:
            covered.update(range(low, high + 1))
    return len(covered) / span


def _section_overhead(citation: str) -> int:
    """Token cost of the framing one packed section adds to the assembled text.

    ``--- <citation> ---`` plus the newline that opens the body and the blank
    line that separates sections. Small per section, but a fifty-section pack
    that ignores it overruns the budget it promised to respect.
    """
    return estimate_tokens(f"--- {citation} ---") + 2


@dataclass
class PackedSection:
    """One block of the assembled context, with its provenance intact."""

    citation: str
    text: str
    tokens: int
    score: float
    kind: str
    path: str
    symbol: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PackedContext:
    """The deliverable: text to send, plus a full account of how it was built."""

    text: str
    tokens: int
    budget: int
    sections: List[PackedSection] = field(default_factory=list)
    dropped: List[dict] = field(default_factory=list)
    pressure: str = "cool"
    strategy: str = "full"
    stats: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "tokens": self.tokens,
            "budget": self.budget,
            "pressure": self.pressure,
            "strategy": self.strategy,
            "stats": self.stats,
            "sections": [s.to_dict() for s in self.sections],
            "dropped": self.dropped,
        }

    def manifest(self) -> str:
        """A compact, human-readable account of what made the cut and why."""
        lines = [
            f"packed {self.tokens:,}/{self.budget:,} tokens "
            f"({len(self.sections)} sections, {len(self.dropped)} dropped, "
            f"strategy={self.strategy}, pressure={self.pressure})"
        ]
        for section in self.sections:
            lines.append(f"  + {section.citation:<52} {section.tokens:>6,}t  score {section.score:.3f}")
        for item in self.dropped[:10]:
            lines.append(f"  - {item['citation']:<52} {item['tokens']:>6,}t  {item['reason']}")
        if len(self.dropped) > 10:
            lines.append(f"  - ... and {len(self.dropped) - 10} more dropped")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------


def iter_source_files(
    root: str,
    include_extensions: Sequence[str] = DEFAULT_INCLUDE_EXTENSIONS,
    excludes: Sequence[str] = DEFAULT_EXCLUDES,
    max_files: int = 5000,
) -> List[str]:
    """Walk ``root`` for indexable source files, skipping vendored trees."""
    found: List[str] = []
    exclude_set = set(excludes)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_set and not d.startswith(".")]
        for filename in sorted(filenames):
            if not filename.lower().endswith(tuple(include_extensions)):
                continue
            full = os.path.join(dirpath, filename)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(full)
            if len(found) >= max_files:
                return found
    return found


def load_chunks(
    paths: Iterable[str],
    root: str = "",
    max_tokens: int = 512,
    store: Optional["Store"] = None,
) -> List[Chunk]:
    """Read and chunk files, reporting paths relative to ``root`` when given.

    With a ``store``, a file whose content fingerprint matches its cached entry
    is served from the cache instead of being re-parsed. That is the difference
    between an indexing pass measured in seconds and one measured in
    milliseconds, and it is what makes per-tool-call hooks viable at all.

    Unreadable files are skipped, not fatal: one binary blob in a tree must not
    take down an indexing run over thousands of good files.
    """
    chunks: List[Chunk] = []
    seen: List[str] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        if "\x00" in source[:4096]:
            continue
        display = os.path.relpath(path, root) if root else path
        seen.append(display)

        if store is not None and store.available:
            mark = fingerprint(source, display)
            cached = store.cached_chunks(display, mark)
            if cached is not None:
                chunks.extend(cached)
                continue
            parsed = chunk_source(source, path=display, max_tokens=max_tokens)
            store.put_chunks(display, mark, detect_language(display, source), parsed)
            chunks.extend(parsed)
            continue

        chunks.extend(chunk_source(source, path=display, max_tokens=max_tokens))

    if store is not None and store.available and seen:
        # A deleted file must not keep answering queries from the cache.
        store.forget_missing(seen)
    return chunks


def sync_index(
    paths: Sequence[str],
    root: str,
    store: "Store",
    max_tokens: int = 512,
) -> int:
    """Bring the persisted index up to date with the source tree.

    Only files whose content changed are re-parsed; untouched files are not even
    materialised out of the database. That asymmetry is the point: an unchanged
    tree costs one read and one hash per file, not a full re-index.

    Returns the number of files re-parsed.
    """
    reindexed = 0
    seen: List[str] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        if "\x00" in source[:4096]:
            continue
        display = os.path.relpath(path, root) if root else path
        seen.append(display)

        mark = fingerprint(source, display)
        if store.is_fresh(display, mark):
            continue
        chunks = chunk_source(source, path=display, max_tokens=max_tokens)
        store.put_chunks(display, mark, detect_language(display, source), chunks)
        reindexed += 1

    store.forget_missing(seen)
    return reindexed


def index_path(
    root: str,
    embedder: Optional[Embedder] = None,
    max_tokens: int = 512,
    include_extensions: Sequence[str] = DEFAULT_INCLUDE_EXTENSIONS,
    excludes: Sequence[str] = DEFAULT_EXCLUDES,
    store: Optional["Store"] = None,
) -> HybridIndex:
    """One call from a directory to a searchable index.

    Pass a :class:`~context_ledger.context.store.Store` to reuse work across invocations;
    without one this stays a pure function that touches nothing but the source
    tree.
    """
    if os.path.isfile(root):
        files = [root]
        base = os.path.dirname(root)
    else:
        files = iter_source_files(root, include_extensions, excludes)
        base = root

    if store is not None and store.available:
        # Warm path: sync only what changed, then let SQLite's compiled BM25 rank
        # from disk. Nothing is re-parsed and no postings are rebuilt.
        sync_index(files, base, store, max_tokens)
        vector = None
        if embedder is not None:
            from .semantic import VectorIndex

            vector = VectorIndex(embedder).add(store.all_chunks())
        return StoreIndex(store, vector=vector)

    return build_index(load_chunks(files, base, max_tokens), embedder)


def open_store(root: str = ".", enabled: bool = True) -> "Store":
    """Open the workspace store. Never raises - check ``.available``."""
    from .store import Store

    return Store(root=root, enabled=enabled)


# --------------------------------------------------------------------------
# Packing
# --------------------------------------------------------------------------


def pack_hits(
    hits: Sequence[Hit],
    budget: int,
    headroom: Optional[Headroom] = None,
    per_section_cap: Optional[int] = None,
    dedupe: bool = True,
) -> PackedContext:
    """Fit ranked hits into ``budget`` tokens, best value per token first.

    Selection is by score density rather than raw score. A chunk scoring 9.0 at
    1200 tokens and three chunks scoring 4.0 at 150 tokens each are not close:
    the budget buys far more answer from the second group.

    Overlapping ranges from the same file are merged rather than double-billed -
    windowed chunks of one big function otherwise pay for their overlap twice.
    """
    pressure = headroom.pressure if headroom else "cool"
    policy = headroom.policy if headroom else {"detail": "full", "elide_bodies": False}

    ordered = sorted(
        hits,
        key=lambda h: (-(h.score / max(1, h.chunk.tokens)), -h.score, h.chunk.id),
    )

    selected: List[PackedSection] = []
    dropped: List[dict] = []
    used = 0
    claimed: Dict[str, List[tuple]] = {}

    for hit in ordered:
        chunk = hit.chunk
        if dedupe:
            ranges = claimed.setdefault(chunk.path, [])
            overlap = _overlap_ratio(chunk.start, chunk.end, ranges)
            if overlap >= OVERLAP_DROP_RATIO:
                dropped.append(
                    {
                        "citation": chunk.citation(),
                        "tokens": chunk.tokens,
                        "reason": f"{overlap * 100:.0f}% already covered by selected ranges",
                    }
                )
                continue

        text = chunk.text
        tokens = chunk.tokens
        cap = per_section_cap
        if cap and tokens > cap:
            trimmed = trim_to_budget(text, cap, keep="head", hint=f"Full range: {chunk.citation()}")
            text, tokens = trimmed.text, trimmed.tokens
        # Assembly wraps every section in a `--- citation ---` header and joins
        # with a blank line. That framing is real context the caller pays for, so
        # it is charged here rather than discovered as an overrun after the fact.
        overhead = _section_overhead(chunk.citation())
        if used + tokens + overhead > budget:
            # Under pressure, fall back to a signature-level stand-in rather than
            # dropping the hit outright: knowing a symbol exists is most of the value.
            remaining = budget - used - overhead
            if policy.get("elide_bodies") and remaining > 40 and chunk.symbol:
                stub = f"# {chunk.citation()} — elided ({chunk.tokens} tokens). {chunk.meta.get('signature', '')}".strip()
                stub_tokens = estimate_tokens(stub)
                if stub_tokens <= remaining:
                    selected.append(
                        PackedSection(chunk.citation(), stub, stub_tokens, hit.score, "stub", chunk.path, chunk.symbol)
                    )
                    used += stub_tokens + overhead
                    continue
            dropped.append({"citation": chunk.citation(), "tokens": tokens, "reason": "over budget"})
            continue

        selected.append(
            PackedSection(chunk.citation(), text, tokens, hit.score, chunk.kind, chunk.path, chunk.symbol)
        )
        used += tokens + overhead
        if dedupe:
            claimed.setdefault(chunk.path, []).append((chunk.start, chunk.end))

    # Emit in reading order (file, then line) rather than score order: a model
    # reading top-to-bottom should see a file's pieces adjacent and in sequence.
    selected.sort(key=lambda s: (s.path, s.symbol or "", s.citation))

    body_parts = []
    for section in selected:
        body_parts.append(f"--- {section.citation} ---\n{section.text}")
    text = "\n\n".join(body_parts)

    return PackedContext(
        text=text,
        tokens=estimate_tokens(text),
        budget=budget,
        sections=selected,
        dropped=dropped,
        pressure=pressure,
        strategy=policy.get("detail", "full"),
        stats={
            "candidates": len(hits),
            "selected": len(selected),
            "dropped": len(dropped),
            "utilisation": round(used / budget, 4) if budget else 0.0,
        },
    )


def pack_query(
    query: str,
    index: HybridIndex,
    budget: int,
    headroom: Optional[Headroom] = None,
    k: Optional[int] = None,
    per_section_cap: Optional[int] = None,
) -> PackedContext:
    """Search, then pack. The one call most callers need.

    ``k`` defaults to whatever the current pressure band allows, so the same call
    site retrieves less as the window fills without the caller managing it.
    """
    if headroom is not None and k is None:
        k = int(headroom.policy.get("semantic_k", 8))
    resolved_k = k or 8
    # Over-fetch: the packer is choosing by density, so it needs more candidates
    # than it will keep, or the cheapest good chunks are never on the table.
    hits = index.search(query, k=resolved_k * 3)
    return pack_hits(hits, budget, headroom, per_section_cap)


def pack_files(
    paths: Sequence[str],
    budget: int,
    headroom: Optional[Headroom] = None,
    root: str = "",
) -> PackedContext:
    """Fit whole files into a budget, degrading each to a skeleton if needed.

    Files are packed smallest-first so that a budget yields the greatest number
    of *complete* files, then anything still unplaced is offered as a skeleton.
    """
    entries = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        display = os.path.relpath(path, root) if root else path
        entries.append((display, source, estimate_tokens(source)))
    entries.sort(key=lambda item: item[2])

    sections: List[PackedSection] = []
    dropped: List[dict] = []
    used = 0
    for display, source, tokens in entries:
        overhead = _section_overhead(display)
        if used + tokens + overhead <= budget:
            sections.append(PackedSection(display, source, tokens, 1.0, "file", display))
            used += tokens + overhead
            continue
        outline_text = skeleton(source, path=display)
        outline_tokens = estimate_tokens(outline_text)
        if outline_text and used + outline_tokens + overhead <= budget:
            sections.append(
                PackedSection(f"{display} (skeleton)", outline_text, outline_tokens, 0.5, "skeleton", display)
            )
            used += outline_tokens + overhead
        else:
            dropped.append({"citation": display, "tokens": tokens, "reason": "over budget even as skeleton"})

    text = "\n\n".join(f"--- {s.citation} ---\n{s.text}" for s in sections)
    return PackedContext(
        text=text,
        tokens=estimate_tokens(text),
        budget=budget,
        sections=sections,
        dropped=dropped,
        pressure=headroom.pressure if headroom else "cool",
        strategy="files+skeletons",
        stats={"files": len(entries), "selected": len(sections), "dropped": len(dropped)},
    )


def plan_lanes(headroom: Headroom, lanes: Optional[Sequence[Lane]] = None) -> Dict[str, int]:
    """Convenience: allocate the standard context lanes and return name -> budget."""
    default_lanes = lanes or [
        Lane("structure", weight=1.0, minimum=200, maximum=4000, priority=2),
        Lane("semantic", weight=3.0, minimum=500, priority=3),
        Lane("diagnostics", weight=1.0, maximum=3000, priority=1),
    ]
    return {a.lane: a.tokens for a in headroom.plan(list(default_lanes))}
