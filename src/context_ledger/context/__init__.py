"""tools-tokens - a plug-and-play context toolkit.

Six layers, each usable on its own, that compose into one answer to the question
every agent has to answer on every turn: *what goes in the window?*

    tokens     what it costs
    headroom   what you can afford
    astx       what is worth quoting          (real AST for Python)
    chunking   how to cut it without breaking it
    semantic   what is relevant               (BM25 now, vectors when you have them)
    pack       what actually gets sent, cited

Quick start::

    from context_ledger.context import Headroom, index_path, pack_query

    headroom = Headroom(window=200_000, reserve_output=8_000)
    headroom.spend("conversation", 120_000)

    index = index_path("./src")
    context = pack_query("where is the retry backoff configured?",
                         index, budget=6_000, headroom=headroom)
    print(context.text)      # cited, budget-respecting context
    print(context.manifest())  # and the account of how it got there

Zero runtime dependencies. Install an embedder only if you want dense retrieval.
"""

from .tokens import (
    TOKEN_PROFILES,
    Measurement,
    TrimResult,
    detect_profile,
    estimate_cost,
    estimate_tokens,
    measure,
    strip_ansi,
    trim_to_budget,
)
from .headroom import (
    PRESSURE_POLICY,
    Allocation,
    Headroom,
    Lane,
    policy_for,
)
from .astx import (
    Symbol,
    detect_language,
    extract_symbols,
    find_symbol,
    outline,
    skeleton,
    slice_symbol,
)
from .chunking import Chunk, chunk_source
from .semantic import (
    Embedder,
    Hit,
    HybridIndex,
    LexicalIndex,
    VectorIndex,
    build_index,
    reciprocal_rank_fusion,
    tokenize,
)
from .store import SavingsRow, Store, fingerprint
from .pack import (
    PackedContext,
    open_store,
    PackedSection,
    index_path,
    load_chunks,
    iter_source_files,
    pack_files,
    pack_hits,
    pack_query,
    plan_lanes,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # tokens
    "TOKEN_PROFILES", "Measurement", "TrimResult", "detect_profile", "estimate_cost",
    "estimate_tokens", "measure", "strip_ansi", "trim_to_budget",
    # headroom
    "PRESSURE_POLICY", "Allocation", "Headroom", "Lane", "policy_for",
    # structure
    "Symbol", "detect_language", "extract_symbols", "find_symbol", "outline",
    "skeleton", "slice_symbol",
    # chunking
    "Chunk", "chunk_source",
    # semantic
    "Embedder", "Hit", "HybridIndex", "LexicalIndex", "VectorIndex", "build_index",
    "reciprocal_rank_fusion", "tokenize",
    # packing
    "PackedContext", "PackedSection", "index_path", "load_chunks", "iter_source_files",
    "pack_files", "pack_hits", "pack_query", "plan_lanes",
    # persistence
    "Store", "SavingsRow", "fingerprint", "open_store",
]
