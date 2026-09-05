"""Layer 4 - chunking.

Retrieval quality is decided here, before a single vector is computed. A chunk
that starts mid-function and ends mid-loop will match a query and then be
useless to the model that receives it.

So chunks follow structure, not character offsets: one symbol is one chunk where
a symbol fits the budget, one heading section is one chunk in Markdown, and only
when neither applies do we fall back to overlapping line windows. Every chunk
carries its file, its line range and its enclosing symbol, so anything retrieved
can be cited back to the exact place it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

from .astx import Symbol, detect_language, extract_symbols
from .tokens import estimate_tokens


@dataclass
class Chunk:
    """A retrievable, quotable unit of source."""

    id: str
    text: str
    path: str
    start: int
    end: int
    tokens: int
    language: str = ""
    symbol: Optional[str] = None
    kind: str = "window"
    meta: Dict[str, str] = field(default_factory=dict)

    def citation(self) -> str:
        """How this chunk should be referred to in assembled context."""
        where = f"{self.path}:{self.start}-{self.end}"
        return f"{where} ({self.symbol})" if self.symbol else where

    def to_dict(self) -> dict:
        return asdict(self)


def _window_chunks(
    lines: List[str],
    path: str,
    language: str,
    max_tokens: int,
    overlap_lines: int,
    offset: int = 0,
    symbol: Optional[str] = None,
    kind: str = "window",
    id_prefix: str = "",
) -> List[Chunk]:
    """Split a line range into overlapping token-bounded windows.

    The overlap exists so a match that straddles a boundary is still found whole
    by at least one chunk.
    """
    chunks: List[Chunk] = []
    index = 0
    cursor = 0
    total = len(lines)
    while cursor < total:
        taken: List[str] = []
        used = 0
        probe = cursor
        while probe < total:
            cost = estimate_tokens(lines[probe], language and "code") + 1
            if taken and used + cost > max_tokens:
                break
            taken.append(lines[probe])
            used += cost
            probe += 1
        if not taken:
            break
        start = offset + cursor + 1
        end = offset + probe
        text = "\n".join(taken)
        chunks.append(
            Chunk(
                id=f"{id_prefix or path}#{start}-{end}",
                text=text,
                path=path,
                start=start,
                end=end,
                tokens=estimate_tokens(text),
                language=language,
                symbol=symbol,
                kind=kind,
            )
        )
        index += 1
        if probe >= total:
            break
        cursor = max(probe - overlap_lines, cursor + 1)
    return chunks


def chunk_source(
    source: str,
    path: str = "",
    language: str = "",
    max_tokens: int = 512,
    min_tokens: int = 24,
    overlap_lines: int = 4,
) -> List[Chunk]:
    """Split ``source`` into structure-aligned chunks.

    Symbols that fit the budget become one chunk each. Symbols that overrun it
    are windowed internally but keep their symbol attribution, so an oversized
    function still cites the function it came from. Lines outside any symbol -
    imports, module-level glue - are windowed and kept, because that is where
    configuration and wiring live.
    """
    if not source.strip():
        return []
    lang = language or detect_language(path, source)
    lines = source.split("\n")
    symbols = extract_symbols(source, lang, path)

    # Only top-level symbols become chunks; nested methods are covered by their
    # class, which keeps a retrieved hit self-contained instead of orphaned.
    top: List[Symbol] = []
    for symbol in symbols:
        if any(other.start <= symbol.start and symbol.end <= other.end and other is not symbol for other in symbols):
            continue
        top.append(symbol)
    top.sort(key=lambda s: s.start)

    if not top:
        # No structure to align to - an unsupported language, a data file, or a
        # source file that failed to parse. Fall straight through to windowing
        # so the `window` kind stays meaningful: it marks "we had nothing to go
        # on", as distinct from `region`, which marks material *between* symbols.
        return _window_chunks(lines, path, lang, max_tokens, overlap_lines)

    chunks: List[Chunk] = []
    cursor = 1  # 1-based line cursor over uncovered regions

    def emit_gap(upto: int) -> None:
        nonlocal cursor
        if upto <= cursor:
            return
        gap = lines[cursor - 1 : upto - 1]
        if not any(line.strip() for line in gap):
            cursor = upto
            return
        text = "\n".join(gap)
        if estimate_tokens(text) < min_tokens:
            cursor = upto
            return
        chunks.extend(
            _window_chunks(gap, path, lang, max_tokens, overlap_lines, offset=cursor - 1, kind="region")
        )
        cursor = upto

    for symbol in top:
        emit_gap(symbol.start)
        body = "\n".join(lines[symbol.start - 1 : symbol.end])
        tokens = estimate_tokens(body)
        if tokens <= max_tokens:
            chunks.append(
                Chunk(
                    # The line range is part of the id, not decoration: one file
                    # can hold two symbols with the same qualified name (a
                    # redefinition, or two Markdown headings with the same title
                    # under different parents). Without the range those chunks
                    # collide, and anything keyed on the id -- rank fusion, the
                    # packer's dedupe, the persisted cache -- silently loses one.
                    id=f"{path}#{symbol.qualname}@{symbol.start}-{symbol.end}",
                    text=body,
                    path=path,
                    start=symbol.start,
                    end=symbol.end,
                    tokens=tokens,
                    language=lang,
                    symbol=symbol.qualname,
                    kind=symbol.kind,
                    meta={"signature": symbol.signature},
                )
            )
        else:
            chunks.extend(
                _window_chunks(
                    lines[symbol.start - 1 : symbol.end],
                    path,
                    lang,
                    max_tokens,
                    overlap_lines,
                    offset=symbol.start - 1,
                    symbol=symbol.qualname,
                    kind=symbol.kind,
                    id_prefix=f"{path}#{symbol.qualname}",  # windows already append their own range
                )
            )
        cursor = max(cursor, symbol.end + 1)

    emit_gap(len(lines) + 1)
    return chunks
