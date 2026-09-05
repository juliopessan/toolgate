"""Agent adapter - JSON in, JSON out.

This is the plug-and-play surface. An agent harness (Claude Code hooks, an MCP
shim, a LangGraph node, a shell wrapper) pipes a tool call in and gets back
either nothing or a cheaper way to do the same thing.

The contract is deliberately minimal and host-agnostic::

    {"tool": "Read", "input": {"file_path": "src/auth.py"}, "context_used": 120000}
        ->
    {"action": "suggest", "reason": "...", "hint": "<skeleton>", "savings_tokens": 8100}

Three rules govern every decision here, and they are what keep an intervening
hook from becoming a liability:

1. **Never block.** The worst outcome is an agent that cannot read a file. Every
   response is advisory; ``action`` is ``"pass"`` or ``"suggest"``, never "deny".
2. **Only intervene when the saving is real.** Below ``min_savings`` the hint
   costs more attention than it saves, so we stay quiet.
3. **Escalate with pressure.** At ``cool`` a full read is fine and we say
   nothing. The hint appears as the window fills, which is when it matters.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, TYPE_CHECKING

from .astx import extract_symbols, skeleton
from .headroom import Headroom
from .tokens import estimate_tokens

if TYPE_CHECKING:  # pragma: no cover - import kept out of the hot path
    from .store import Store

#: Below this, a full read is cheaper than the round trip of a narrowing hint.
DEFAULT_MIN_FILE_TOKENS = 1_200

#: A hint must save at least this much to be worth the agent's attention.
DEFAULT_MIN_SAVINGS = 600

READ_TOOLS = {"Read", "read_file", "view", "cat", "open_file"}
SEARCH_TOOLS = {"Grep", "grep", "search", "rg", "ripgrep"}

#: Hosts report back through this pseudo-tool when their agent acted on a
#: suggestion, which is what turns an offer into a realised saving.
ACCEPT_TOOLS = {"tools-tokens/accepted", "ToolsTokensAccepted"}

#: Reported by a host that served the narrow read itself. Optional: the CLI
#: claims its own offers, so the ledger works with no host cooperation.
NARROW_READ_TOOLS = {"tools-tokens/narrow-read", "ToolsTokensNarrowRead"}


def _passthrough(reason: str = "") -> Dict[str, Any]:
    return {"action": "pass", "reason": reason}


def _resolve(path: str, root: str) -> Optional[str]:
    """Resolve a tool-supplied path against ``root``, refusing to escape it.

    Tool input is attacker-reachable in any agent that reads untrusted content,
    so a traversal out of the workspace is rejected rather than followed.
    """
    if not path:
        return None
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    candidate = os.path.realpath(candidate)
    root_real = os.path.realpath(root)
    if not (candidate == root_real or candidate.startswith(root_real + os.sep)):
        return None
    return candidate if os.path.isfile(candidate) else None


def suggest_for_read(
    path: str,
    root: str = ".",
    headroom: Optional[Headroom] = None,
    min_file_tokens: int = DEFAULT_MIN_FILE_TOKENS,
    min_savings: int = DEFAULT_MIN_SAVINGS,
    store: Optional["Store"] = None,
) -> Dict[str, Any]:
    """Offer a skeleton in place of a full-file read, when that is a real win."""
    resolved = _resolve(path, root)
    if resolved is None:
        return _passthrough("path not readable within the workspace root")
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()
    except OSError as error:
        return _passthrough(f"unreadable: {error.strerror}")

    full_tokens = estimate_tokens(source)
    if full_tokens < min_file_tokens:
        return _passthrough(f"file is only {full_tokens} tokens; a full read is cheapest")

    pressure = headroom.pressure if headroom else "cool"
    if pressure == "cool" and full_tokens < min_file_tokens * 4:
        return _passthrough("context is cool and the file is moderate; full read is fine")

    outline_text = skeleton(source, path=path)
    if not outline_text:
        return _passthrough("no symbols extracted; nothing better to offer than the file")

    outline_tokens = estimate_tokens(outline_text)
    savings = full_tokens - outline_tokens
    if savings < min_savings:
        return _passthrough(f"skeleton saves only {savings} tokens; not worth the detour")

    symbols = extract_symbols(source, path=path)
    if store is not None:
        # Record the offer, not an assumed acceptance. An offer nobody took
        # saved nothing, and the ledger has to be able to say so.
        store.record("suggested", tool="Read", path=path,
                     tokens_full=full_tokens, tokens_served=outline_tokens, pressure=pressure)
    return {
        "action": "suggest",
        "reason": (
            f"{path} costs {full_tokens:,} tokens to read in full. Its skeleton is "
            f"{outline_tokens:,} ({savings * 100 // max(1, full_tokens)}% smaller) and lists all "
            f"{len(symbols)} symbols with line ranges. Read one symbol from it, or the "
            f"line range you need, instead of the whole file."
        ),
        "hint": outline_text,
        "savings_tokens": savings,
        "full_tokens": full_tokens,
        "hint_tokens": outline_tokens,
        "pressure": pressure,
        "next": [
            f"tools-tokens symbol {path} <SymbolName>",
            f"tools-tokens skeleton {path}",
        ],
    }


def suggest_for_search(
    query: str, root: str = ".", k: int = 5, store: Optional["Store"] = None
) -> Dict[str, Any]:
    """Answer a directory-wide grep with ranked, cited chunks instead.

    A recursive grep returns every line that matched; this returns the handful of
    places that are actually about the query, each already addressable by symbol.
    """
    if not query:
        return _passthrough("empty query")
    from .pack import index_path  # deferred: indexing is expensive, hints are not

    index = index_path(root, store=store)
    if not len(index):
        return _passthrough("nothing indexable under root")
    hits = index.search(query, k=k)
    if not hits:
        return _passthrough("no semantic matches; a literal search may still be right")
    return {
        "action": "suggest",
        "reason": f"{len(hits)} ranked matches for {query!r}, already narrowed to symbols.",
        "hits": [
            {"citation": h.chunk.citation(), "score": round(h.score, 3), "tokens": h.chunk.tokens}
            for h in hits
        ],
        "next": [f"tools-tokens symbol {h.chunk.path} {h.chunk.symbol}" for h in hits if h.chunk.symbol][:3],
    }


def run_hook(
    payload: Dict[str, Any], root: str = ".", store: Optional["Store"] = None
) -> Dict[str, Any]:
    """Route one host payload to the right suggestion.

    Unknown tools pass through untouched - an adapter that guesses at tools it
    does not understand is an adapter that breaks the host it is installed in.
    """
    tool = str(payload.get("tool") or payload.get("tool_name") or "")
    tool_input = payload.get("input") or payload.get("tool_input") or {}

    headroom = None
    used = payload.get("context_used")
    if isinstance(used, int) and used > 0:
        headroom = Headroom(
            window=int(payload.get("context_window") or 200_000),
            reserve_output=int(payload.get("reserve_output") or 8_000),
        )
        headroom.spend("session", used)

    if tool in READ_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("filename") or ""
        return suggest_for_read(str(path), root=root, headroom=headroom, store=store)

    if tool in SEARCH_TOOLS:
        query = tool_input.get("pattern") or tool_input.get("query") or ""
        return suggest_for_search(str(query), root=root, store=store)

    if tool in NARROW_READ_TOOLS:
        # A host that can report the narrow read directly; the same claim the CLI
        # makes for itself when it serves a skeleton or a symbol.
        if store is not None:
            claimed = store.claim_offer(
                str(tool_input.get("path") or tool_input.get("file_path") or ""),
                int(tool_input.get("tokens_served") or 0),
                tool="host-report",
            )
            if claimed is not None:
                return {"action": "pass", "reason": f"recorded, {claimed} tokens saved"}
        return {"action": "pass", "reason": "no standing offer to claim"}

    if tool in ACCEPT_TOOLS:
        # The host telling us its agent took the narrow path. This is the only
        # place a saving becomes *realised* rather than merely offered.
        if store is not None:
            store.record(
                "accepted",
                tool=str(tool_input.get("origin") or "Read"),
                path=str(tool_input.get("path") or tool_input.get("file_path") or ""),
                tokens_full=int(tool_input.get("tokens_full") or 0),
                tokens_served=int(tool_input.get("tokens_served") or 0),
            )
        return {"action": "pass", "reason": "recorded"}

    return _passthrough(f"no handler for tool {tool!r}")
