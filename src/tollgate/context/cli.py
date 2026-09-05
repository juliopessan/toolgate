"""Command-line interface.

Every subcommand takes ``--json`` so the tool composes into scripts and agent
harnesses as readily as it reads in a terminal. Exit codes are meaningful:
``0`` success, ``1`` nothing found, ``2`` bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .astx import extract_symbols, outline as build_outline, skeleton, slice_symbol
from .chunking import chunk_source
from .headroom import Headroom, Lane
from .pack import index_path, open_store, pack_files, pack_query
from .tokens import estimate_cost, measure, trim_to_budget

EXIT_OK, EXIT_EMPTY, EXIT_USAGE = 0, 1, 2


def _read_input(path: Optional[str]) -> str:
    """Read a file, or stdin when the path is missing or ``-``."""
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _claim(path: Optional[str], tokens_served: int, tool: str) -> Optional[int]:
    """Close the loop on a standing offer for ``path``, if there is one.

    The ledger's whole point is separating the saving that was offered from the
    saving that was taken. Asking every host to report back is a contract nobody
    implements; noticing that the narrow read just happened needs no contract.

    Silent by design: no store, no offer, or a path outside the workspace all
    mean nothing to record, and none of them is worth interrupting the output for.
    """
    if not path or path == "-":
        return None
    try:
        root = os.getcwd()
        store = open_store(root)
        if not store.available:
            return None
        absolute = os.path.abspath(path)
        display = os.path.relpath(absolute, root)
        if display.startswith(".."):
            return None
        claimed = store.claim_offer(display, tokens_served, tool=tool)
        store.close()
        return claimed
    except OSError:
        return None


def _emit(payload, as_json: bool, human: str) -> None:
    print(json.dumps(payload, indent=2, default=str) if as_json else human)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_count(args) -> int:
    text = _read_input(args.path)
    result = measure(text, args.profile)
    cost = estimate_cost(result.tokens, args.input_price, args.output_price)
    payload = {**result.to_dict(), "cost_usd": cost}
    human = (
        f"{result.tokens:,} tokens  ({result.chars:,} chars, {result.lines:,} lines, "
        f"{result.bytes:,} bytes, profile={result.profile})"
        + (f"  ~${cost:.4f}" if args.input_price else "")
    )
    _emit(payload, args.json, human)
    return EXIT_OK


def cmd_trim(args) -> int:
    text = _read_input(args.path)
    result = trim_to_budget(text, args.budget, keep=args.keep, hint=args.hint or "")
    if args.json:
        _emit(result.to_dict(), True, "")
    else:
        print(result.text)
    return EXIT_OK


def cmd_headroom(args) -> int:
    headroom = Headroom(
        window=args.window,
        reserve_output=args.reserve_output,
        reserve_system=args.reserve_system,
        safety_margin=args.safety_margin,
    )
    for entry in args.spend or []:
        name, _, raw = entry.partition("=")
        if not raw.isdigit():
            print(f"error: --spend expects NAME=TOKENS, got {entry!r}", file=sys.stderr)
            return EXIT_USAGE
        headroom.spend(name, int(raw))

    lanes: List[Lane] = []
    for entry in args.lane or []:
        # NAME:WEIGHT[:MIN[:MAX]]
        parts = entry.split(":")
        try:
            lanes.append(
                Lane(
                    name=parts[0],
                    weight=float(parts[1]) if len(parts) > 1 and parts[1] else 1.0,
                    minimum=int(parts[2]) if len(parts) > 2 and parts[2] else 0,
                    maximum=int(parts[3]) if len(parts) > 3 and parts[3] else None,
                )
            )
        except ValueError:
            print(f"error: --lane expects NAME:WEIGHT[:MIN[:MAX]], got {entry!r}", file=sys.stderr)
            return EXIT_USAGE

    snapshot = headroom.snapshot()
    human = headroom.render()
    if lanes:
        allocations = headroom.plan(lanes)
        snapshot["plan"] = [a.to_dict() for a in allocations]
        human += "\n\n  allocation of " + f"{headroom.available:,} free tokens:"
        for allocation in allocations:
            flag = f"  [{allocation.clamped}]" if allocation.clamped else ""
            human += f"\n    {allocation.lane:<24} {allocation.tokens:>9,}{flag}"
    _emit(snapshot, args.json, human)
    return EXIT_OK


def cmd_skeleton(args) -> int:
    source = _read_input(args.path)
    result = skeleton(source, language=args.language or "", path=args.path or "<stdin>", include_docs=not args.no_docs)
    if not result:
        print("no symbols found", file=sys.stderr)
        return EXIT_EMPTY
    claimed = _claim(args.path, measure(result).tokens, "skeleton")
    if args.json:
        _emit(
            {
                "skeleton": result,
                "claimed_tokens": claimed,
                "symbols": [s.to_dict() for s in extract_symbols(source, args.language or "", args.path or "")],
            },
            True, "",
        )
    else:
        original = measure(source).tokens
        reduced = measure(result).tokens
        print(result)
        if reduced < original:
            saved = (1 - reduced / original) * 100
            verdict = f"{saved:.0f}% smaller than the full file"
        elif original:
            # A skeleton can cost more than a tiny file: the signature is repeated
            # alongside its line-range annotation. Say so plainly rather than
            # printing a negative "smaller" percentage.
            verdict = f"{reduced - original:,} tokens MORE than the full file — read the file directly"
        else:
            verdict = "empty input"
        print(f"\n# {reduced:,} tokens vs {original:,} for the full file ({verdict})")
    return EXIT_OK


def cmd_symbol(args) -> int:
    source = _read_input(args.path)
    result = slice_symbol(source, args.name, language=args.language or "", path=args.path or "")
    if result is None:
        print(f"symbol not found: {args.name}", file=sys.stderr)
        return EXIT_EMPTY
    result["tokens"] = measure(result["text"]).tokens
    result["claimed_tokens"] = _claim(args.path, result["tokens"], "symbol")
    if args.json:
        _emit(result, True, "")
    else:
        print(f"# {args.path or '<stdin>'}:{result['start']}-{result['end']}  ({result['tokens']:,} tokens)")
        print(result["text"])
    return EXIT_OK


def cmd_outline(args) -> int:
    source = _read_input(args.path)
    tree = build_outline(source, language=args.language or "", path=args.path or "")
    if not tree:
        print("no symbols found", file=sys.stderr)
        return EXIT_EMPTY
    if args.json:
        _emit(tree, True, "")
        return EXIT_OK

    def walk(nodes, depth=0):
        for node in nodes:
            print(f"{'  ' * depth}{node['kind']:<10} {node['name']:<32} {node['start']}-{node['end']}")
            walk(node["children"], depth + 1)

    walk(tree)
    return EXIT_OK


def cmd_chunk(args) -> int:
    source = _read_input(args.path)
    chunks = chunk_source(source, path=args.path or "<stdin>", language=args.language or "", max_tokens=args.max_tokens)
    if args.json:
        _emit([c.to_dict() for c in chunks], True, "")
    else:
        for chunk in chunks:
            print(f"{chunk.kind:<10} {chunk.tokens:>6,}t  {chunk.citation()}")
        print(f"\n{len(chunks)} chunks, {sum(c.tokens for c in chunks):,} tokens total")
    return EXIT_OK


def cmd_search(args) -> int:
    store = open_store(args.root, enabled=not args.no_cache)
    index = index_path(args.root, max_tokens=args.max_tokens, store=store)
    if not len(index):
        print(f"nothing indexable under {args.root}", file=sys.stderr)
        return EXIT_EMPTY
    hits = index.search(args.query, k=args.k)
    if args.json:
        _emit([h.to_dict() for h in hits], True, "")
    else:
        for hit in hits:
            print(f"{hit.score:8.3f}  {hit.chunk.tokens:>6,}t  {hit.chunk.citation()}")
        if not hits:
            print("no matches", file=sys.stderr)
            return EXIT_EMPTY
    return EXIT_OK


def cmd_pack(args) -> int:
    headroom = Headroom(window=args.window, reserve_output=args.reserve_output)
    if args.used:
        headroom.spend("conversation", args.used)
    if args.files:
        context = pack_files(args.files, args.budget, headroom)
    else:
        store = open_store(args.root, enabled=not args.no_cache)
        index = index_path(args.root, max_tokens=args.max_tokens, store=store)
        if not len(index):
            print(f"nothing indexable under {args.root}", file=sys.stderr)
            return EXIT_EMPTY
        context = pack_query(args.query, index, budget=args.budget, headroom=headroom, per_section_cap=args.section_cap)
    if args.json:
        _emit(context.to_dict(), True, "")
    elif args.manifest:
        print(context.manifest())
    else:
        print(context.text)
        print(f"\n{context.manifest()}", file=sys.stderr)
    return EXIT_OK if context.sections else EXIT_EMPTY


def cmd_hook(args) -> int:
    """Agent adapter. Must never raise into the host.

    A hook that dies with a traceback is worse than a hook that does nothing:
    the host sees garbage on stdout and an agent loses a tool call. Every
    failure here degrades to a passthrough with the reason attached.
    """
    from .hooks import run_hook  # imported lazily: the hot path is the CLI, not this

    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError) as error:
        print(json.dumps({"action": "pass", "reason": f"unreadable stdin: {error}"}))
        return EXIT_OK

    try:
        payload = json.loads(raw or "{}")
    except ValueError as error:
        print(json.dumps({"action": "pass", "reason": f"malformed JSON payload: {error}"}))
        return EXIT_OK

    if not isinstance(payload, dict):
        print(json.dumps({"action": "pass", "reason": "payload is not a JSON object"}))
        return EXIT_OK

    try:
        result = run_hook(payload, root=args.root, store=open_store(args.root, enabled=not args.no_cache))
    except Exception as error:  # noqa: BLE001 - deliberate: never raise into the host
        result = {"action": "pass", "reason": f"hook error, passing through: {type(error).__name__}: {error}"}
    print(json.dumps(result))
    return EXIT_OK


def cmd_stats(args) -> int:
    """What the workspace store has actually learned."""
    store = open_store(args.root)
    summary = store.summary()
    rows = store.realized_savings(limit=args.limit)
    payload = {**summary, "by_file": [r.to_dict() for r in rows]}

    if args.json:
        _emit(payload, True, "")
        return EXIT_OK

    if not summary["available"]:
        print(f"no store at {summary['path']}")
        print(f"reason: {summary['reason']}")
        print("everything still works; persistence is optional.")
        return EXIT_EMPTY

    print(f"store   {summary['path']}  ({summary.get('size_bytes', 0) / 1e6:.1f} MB)")
    print(f"cache   {summary['files_cached']:,} files, {summary['chunks_cached']:,} chunks")
    print(f"ledger  {summary['events']:,} events")
    if summary["offers"]:
        print(
            f"        {summary['accepted']:,} of {summary['offers']:,} suggestions taken "
            f"({summary['acceptance_rate'] * 100:.0f}%), {summary['tokens_saved']:,} tokens saved"
        )
    else:
        print("        no suggestions recorded yet - realised savings need a host that reports back")
    pending = store.pending_offers()
    if pending:
        print(f"        {len(pending)} offer(s) still standing, unclaimed")
    if rows:
        print("\nrealised savings by file")
        for row in rows:
            print(f"  {row.tokens_saved:>9,}  {row.accepted}/{row.offers}  {row.path}")
    return EXIT_OK


def cmd_forget(args) -> int:
    """Erase what the store remembers. Privacy has to be one command."""
    store = open_store(args.root)
    if not store.available:
        print(f"no store to clear at {store.path}")
        return EXIT_EMPTY
    before = store.summary()
    store.purge(cache_only=args.cache_only)
    scope = "cache" if args.cache_only else "cache and ledger"
    print(
        f"cleared {scope}: {before['files_cached']:,} files, "
        f"{before['chunks_cached']:,} chunks"
        + ("" if args.cache_only else f", {before['events']:,} events")
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tollgate",
        description="Token accounting, headroom budgeting, AST slicing and semantic packing.",
    )
    parser.add_argument("--version", action="version", version=f"tollgate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target):
        target.add_argument("--json", action="store_true", help="emit machine-readable JSON")
        return target

    count = add_common(sub.add_parser("count", help="estimate the token cost of a file or stdin"))
    count.add_argument("path", nargs="?", help="file to measure (default: stdin)")
    count.add_argument("--profile", default=None, help="prose|markdown|mixed|code|json|base64 (default: auto)")
    count.add_argument("--input-price", type=float, default=0.0, metavar="USD_PER_MTOK")
    count.add_argument("--output-price", type=float, default=0.0, metavar="USD_PER_MTOK")
    count.set_defaults(func=cmd_count)

    trim = add_common(sub.add_parser("trim", help="cut text down to a token budget"))
    trim.add_argument("path", nargs="?")
    trim.add_argument("--budget", type=int, required=True)
    trim.add_argument("--keep", choices=["head", "tail", "both"], default="head")
    trim.add_argument("--hint", default="")
    trim.set_defaults(func=cmd_trim)

    head = add_common(sub.add_parser("headroom", help="show and allocate remaining context budget"))
    head.add_argument("--window", type=int, default=200_000)
    head.add_argument("--reserve-output", type=int, default=8_000)
    head.add_argument("--reserve-system", type=int, default=0)
    head.add_argument("--safety-margin", type=float, default=0.02)
    head.add_argument("--spend", action="append", metavar="NAME=TOKENS")
    head.add_argument("--lane", action="append", metavar="NAME:WEIGHT[:MIN[:MAX]]")
    head.set_defaults(func=cmd_headroom)

    # Reading from stdin leaves nothing to detect a language from beyond the
    # content itself, so these four accept an explicit override.
    def add_language(target):
        target.add_argument(
            "--language", default=None,
            help="override language detection (python, typescript, go, markdown, ...); "
                 "useful when reading from stdin",
        )
        return target

    skel = add_language(add_common(sub.add_parser("skeleton", help="signatures only, bodies elided")))
    skel.add_argument("path", nargs="?")
    skel.add_argument("--no-docs", action="store_true")
    skel.set_defaults(func=cmd_skeleton)

    sym = add_language(add_common(sub.add_parser("symbol", help="extract one symbol instead of the whole file")))
    sym.add_argument("path")
    sym.add_argument("name", help="Symbol or Parent.Symbol")
    sym.set_defaults(func=cmd_symbol)

    out = add_language(add_common(sub.add_parser("outline", help="nested symbol tree")))
    out.add_argument("path", nargs="?")
    out.set_defaults(func=cmd_outline)

    chunk = add_language(add_common(sub.add_parser("chunk", help="structure-aligned chunks")))
    chunk.add_argument("path", nargs="?")
    chunk.add_argument("--max-tokens", type=int, default=512)
    chunk.set_defaults(func=cmd_chunk)

    search = add_common(sub.add_parser("search", help="semantic search over a directory"))
    search.add_argument("query")
    search.add_argument("--root", default=".")
    search.add_argument("-k", type=int, default=10)
    search.add_argument("--max-tokens", type=int, default=512)
    search.add_argument("--no-cache", action="store_true", help="ignore the workspace store")
    search.set_defaults(func=cmd_search)

    pack = add_common(sub.add_parser("pack", help="assemble a budget-respecting, cited context"))
    pack.add_argument("query", nargs="?", default="")
    pack.add_argument("--root", default=".")
    pack.add_argument("--files", nargs="*", help="pack these files instead of searching")
    pack.add_argument("--budget", type=int, default=6_000)
    pack.add_argument("--window", type=int, default=200_000)
    pack.add_argument("--reserve-output", type=int, default=8_000)
    pack.add_argument("--used", type=int, default=0, help="tokens already consumed this session")
    pack.add_argument("--section-cap", type=int, default=None)
    pack.add_argument("--max-tokens", type=int, default=512)
    pack.add_argument("--manifest", action="store_true", help="print the manifest instead of the context")
    pack.add_argument("--no-cache", action="store_true", help="ignore the workspace store")
    pack.set_defaults(func=cmd_pack)

    hook = sub.add_parser("hook", help="agent hook adapter: JSON on stdin, JSON on stdout")
    hook.add_argument("--root", default=".")
    hook.add_argument("--no-cache", action="store_true", help="ignore the workspace store")
    hook.set_defaults(func=cmd_hook, json=True)

    stats = add_common(sub.add_parser("stats", help="what the workspace store has learned"))
    stats.add_argument("--root", default=".")
    stats.add_argument("--limit", type=int, default=15)
    stats.set_defaults(func=cmd_stats)

    forget = add_common(sub.add_parser("forget", help="erase the workspace store"))
    forget.add_argument("--root", default=".")
    forget.add_argument("--cache-only", action="store_true", help="keep the event ledger")
    forget.set_defaults(func=cmd_forget)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # pragma: no cover - `| head` closes the pipe
        os._exit(EXIT_OK)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except FileNotFoundError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
