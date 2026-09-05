"""Layer 5 - semantic retrieval.

The design constraint here is the whole point of the package: it must work with
nothing installed. A retrieval layer that needs a 400 MB model download before it
returns its first result is not plug-and-play, and in practice it means the tool
never gets adopted.

So there are two retrievers behind one interface:

* :class:`LexicalIndex` - BM25 with code-aware tokenisation. Splits camelCase and
  snake_case, so a query for "refresh token" matches ``refreshAccessToken``.
  Pure stdlib, instant, deterministic, and genuinely good on identifier-heavy
  text where dense vectors are often *worse* than exact term matching.
* :class:`VectorIndex` - cosine similarity over any embedder you hand it. There
  is no built-in model and no network call: you inject an object exposing
  ``embed(texts) -> list[list[float]]``. Bring sentence-transformers, an API
  client, or your own.

:class:`HybridIndex` runs both and fuses the rankings with Reciprocal Rank
Fusion, which needs no score normalisation between two scales that are not
comparable - the reason naive score-averaging of BM25 and cosine tends to
degrade to whichever scale happens to be larger.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from .chunking import Chunk

# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

#: Stopwords that carry no retrieval signal but appear in every source file.
#: Deliberately short: over-pruning a code corpus removes real identifiers.
STOPWORDS = frozenset(
    """a an the and or of to in for on at by is are be was were this that these those
    it its as with from if else return not no yes do does did can will would should
    self cls def class import export const let var function new""".split()
)


def tokenize(text: str, split_identifiers: bool = True) -> List[str]:
    """Lowercase terms, with identifiers additionally split into their parts.

    ``getUserToken`` yields ``getusertoken``, ``get``, ``user``, ``token`` - the
    compound is kept because an exact match on it should outrank the parts.
    """
    terms: List[str] = []
    for raw in _WORD_RE.findall(text or ""):
        lowered = raw.lower()
        if lowered not in STOPWORDS and len(lowered) > 1:
            terms.append(lowered)
        if not split_identifiers:
            continue
        parts = _CAMEL_RE.findall(raw.replace("_", " "))
        if len(parts) > 1:
            for part in parts:
                lowered_part = part.lower()
                if len(lowered_part) > 1 and lowered_part not in STOPWORDS:
                    terms.append(lowered_part)
    return terms


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass
class Hit:
    chunk: Chunk
    score: float
    source: str = "lexical"  # lexical | vector | hybrid
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 6),
            "source": self.source,
            "rank": self.rank,
            "citation": self.chunk.citation(),
            "chunk": self.chunk.to_dict(),
        }


class Embedder(Protocol):
    """Anything that turns text into vectors. Injected, never imported."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


# --------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------


class LexicalIndex:
    """BM25 over chunks, with code-aware tokenisation.

    ``k1`` controls term-frequency saturation and ``b`` length normalisation;
    the defaults are the standard ones and are fine for mixed code and prose.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: List[Chunk] = []
        self._terms: List[Counter] = []
        self._lengths: List[int] = []
        self._df: Counter = Counter()
        self._postings: Dict[str, List[int]] = defaultdict(list)
        self._avg_len = 0.0

    def __len__(self) -> int:
        return len(self.chunks)

    def add(self, chunks: Iterable[Chunk]) -> "LexicalIndex":
        """Index chunks. The symbol name is repeated so that naming a symbol in
        the query is a strong signal, not one term among hundreds."""
        for chunk in chunks:
            boost = f" {chunk.symbol} {chunk.symbol} " if chunk.symbol else ""
            terms = Counter(tokenize(f"{chunk.path} {boost}{chunk.text}"))
            index = len(self.chunks)
            self.chunks.append(chunk)
            self._terms.append(terms)
            self._lengths.append(max(1, sum(terms.values())))
            for term in terms:
                self._df[term] += 1
                self._postings[term].append(index)
        self._avg_len = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        return self

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        # BM25+ style flooring: the classic formula goes negative for terms in
        # more than half the corpus, which silently penalises common identifiers.
        return max(0.0, math.log(1.0 + (n - df + 0.5) / (df + 0.5)))

    def search(self, query: str, k: int = 10) -> List[Hit]:
        if not self.chunks:
            return []
        query_terms = tokenize(query)
        if not query_terms:
            return []
        scores: Dict[int, float] = defaultdict(float)
        for term in set(query_terms):
            idf = self._idf(term)
            if idf <= 0:
                continue
            for doc in self._postings.get(term, ()):
                freq = self._terms[doc][term]
                length = self._lengths[doc]
                denom = freq + self.k1 * (1 - self.b + self.b * length / (self._avg_len or 1))
                scores[doc] += idf * (freq * (self.k1 + 1)) / (denom or 1)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [
            Hit(chunk=self.chunks[doc], score=score, source="lexical", rank=position + 1)
            for position, (doc, score) in enumerate(ranked)
        ]


# --------------------------------------------------------------------------
# Vectors
# --------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


class VectorIndex:
    """Cosine similarity over an injected embedder.

    Embeddings are computed in one batched call at ``add`` time, because per-chunk
    embedding calls are the difference between an index that builds in seconds
    and one that builds in minutes.
    """

    def __init__(self, embedder: Embedder, batch_size: int = 64) -> None:
        self.embedder = embedder
        self.batch_size = max(1, batch_size)
        self.chunks: List[Chunk] = []
        self.vectors: List[Sequence[float]] = []

    def __len__(self) -> int:
        return len(self.chunks)

    def add(self, chunks: Iterable[Chunk]) -> "VectorIndex":
        pending = list(chunks)
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            vectors = self.embedder.embed([c.text for c in batch])
            self.chunks.extend(batch)
            self.vectors.extend(vectors)
        return self

    def search(self, query: str, k: int = 10) -> List[Hit]:
        if not self.chunks:
            return []
        query_vector = self.embedder.embed([query])[0]
        scored = [(cosine(query_vector, vector), idx) for idx, vector in enumerate(self.vectors)]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            Hit(chunk=self.chunks[idx], score=score, source="vector", rank=position + 1)
            for position, (score, idx) in enumerate(scored[:k])
        ]


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------


def reciprocal_rank_fusion(rankings: Sequence[Sequence[Hit]], k_constant: int = 60) -> List[Hit]:
    """Fuse ranked lists by rank, not by score.

    RRF needs no score normalisation, which is exactly why it is used here: BM25
    scores are unbounded and cosine scores live in [-1, 1], so averaging them
    lets whichever scale is larger quietly decide every result.
    """
    fused: Dict[str, float] = defaultdict(float)
    seen: Dict[str, Hit] = {}
    for ranking in rankings:
        for position, hit in enumerate(ranking):
            key = hit.chunk.id
            fused[key] += 1.0 / (k_constant + position + 1)
            seen.setdefault(key, hit)
    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        Hit(chunk=seen[key].chunk, score=score, source="hybrid", rank=position + 1)
        for position, (key, score) in enumerate(ordered)
    ]


class HybridIndex:
    """Lexical always, vector when an embedder is supplied, fused by RRF.

    With no embedder this is exactly ``LexicalIndex`` - which is the point. The
    tool works on install, and gets better when you give it a model.
    """

    def __init__(self, embedder: Optional[Embedder] = None, k1: float = 1.5, b: float = 0.75) -> None:
        self.lexical = LexicalIndex(k1=k1, b=b)
        self.vector = VectorIndex(embedder) if embedder is not None else None

    def __len__(self) -> int:
        return len(self.lexical)

    @property
    def chunks(self) -> List[Chunk]:
        return self.lexical.chunks

    def add(self, chunks: Iterable[Chunk]) -> "HybridIndex":
        materialised = list(chunks)
        self.lexical.add(materialised)
        if self.vector is not None:
            self.vector.add(materialised)
        return self

    def search(self, query: str, k: int = 10, over_fetch: int = 3) -> List[Hit]:
        """Retrieve the top ``k``.

        Each retriever over-fetches before fusion: a chunk ranked 12th lexically
        and 2nd by vector should surface, and it cannot if the lexical list was
        already cut at 10.
        """
        depth = max(k * over_fetch, k)
        lexical_hits = self.lexical.search(query, depth)
        if self.vector is None:
            return lexical_hits[:k]
        vector_hits = self.vector.search(query, depth)
        return reciprocal_rank_fusion([lexical_hits, vector_hits])[:k]


def build_index(
    chunks: Iterable[Chunk], embedder: Optional[Embedder] = None
) -> HybridIndex:
    """Convenience constructor: chunks in, searchable index out."""
    return HybridIndex(embedder).add(chunks)
