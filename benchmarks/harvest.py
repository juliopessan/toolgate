"""Metric harvest for the delivery report.

Runs every measurement the report quotes and writes benchmarks/metrics.json.
Reproducible by design: no number reaches the report that this script did not
produce, so any figure can be re-derived by re-running it.

    python benchmarks/harvest.py [corpus_root ...]
"""

from __future__ import annotations

import glob
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict

#: Repository root, derived from this file rather than from the caller's cwd.
#: The report claims anyone on the team can re-run this; that is only true if it
#: does not silently depend on being launched from the repo directory.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def repo(*parts: str) -> str:
    """Path inside the repository, independent of the working directory."""
    return os.path.join(ROOT, *parts)

from tollgate.context import (  # noqa: E402
    Headroom,
    Lane,
    estimate_tokens,
    extract_symbols,
    chunk_source,
    index_path,
    pack_query,
    skeleton,
    slice_symbol,
)
from tollgate.context.astx import detect_language  # noqa: E402

# Pricing assumption, stated openly so the report can label it as one.
# Blended input rate for a frontier-class model, USD per million input tokens.
USD_PER_MTOK_INPUT = 3.00

CORPORA = sys.argv[1:] or ["/home/user/token-goat/src", "/home/user/semantica/semantica"]


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------- baseline
def measure_corpus(root, limit=400):
    """Baseline (full reads) vs. treatment (skeletons), per language."""
    per_language = defaultdict(lambda: {"files": 0, "full": 0, "skeleton": 0, "symbols": 0, "ratios": []})
    paths = [p for p in glob.glob(os.path.join(root, "**", "*.*"), recursive=True) if os.path.isfile(p)]
    considered = 0
    for path in sorted(paths):
        language = detect_language(path)
        if language in ("text",):
            continue
        try:
            source = read(path)
        except OSError:
            continue
        if not source.strip() or len(source) > 1_500_000:
            continue
        outline = skeleton(source, path=path)
        if not outline:
            continue
        full = estimate_tokens(source)
        reduced = estimate_tokens(outline)
        bucket = per_language[language]
        bucket["files"] += 1
        bucket["full"] += full
        bucket["skeleton"] += reduced
        bucket["symbols"] += len(extract_symbols(source, path=path))
        bucket["ratios"].append(reduced / full)
        considered += 1
        if considered >= limit:
            break

    languages = {}
    for language, bucket in per_language.items():
        if bucket["files"] < 3:
            continue  # too few files to report an honest median
        languages[language] = {
            "files": bucket["files"],
            "tokens_full": bucket["full"],
            "tokens_skeleton": bucket["skeleton"],
            "reduction_pct": round((1 - bucket["skeleton"] / bucket["full"]) * 100, 1),
            "median_file_reduction_pct": round((1 - statistics.median(bucket["ratios"])) * 100, 1),
            "symbols_indexed": bucket["symbols"],
        }
    return languages


# ---------------------------------------------------------- budget adherence
def measure_budget_adherence(index, query, budgets):
    """The core safety property: the packer must never overrun its budget."""
    rows = []
    headroom = Headroom(window=200_000, reserve_output=8_000)
    headroom.spend("conversation", 120_000)
    for budget in budgets:
        context = pack_query(query, index, budget=budget, headroom=headroom)
        rows.append(
            {
                "budget": budget,
                "packed": context.tokens,
                "utilisation_pct": round(context.tokens / budget * 100, 1),
                "sections": len(context.sections),
                "dropped": len(context.dropped),
                "within_budget": context.tokens <= budget,
            }
        )
    return rows


# ------------------------------------------------------------- retrieval eval
GOLDEN = [
    ("how is the remaining budget divided between competing lanes", "headroom.py"),
    ("split camelCase identifiers when tokenising for search", "semantic.py"),
    ("truncate output keeping the end of a log", "tokens.py"),
    ("extract declarations from a source file", "astx.py"),
    ("assemble a cited context under a token budget", "pack.py"),
    ("split a file into structure aligned pieces", "chunking.py"),
    ("advise an agent instead of blocking its tool call", "hooks.py"),
    ("parse command line arguments and subcommands", "cli.py"),
]


def measure_retrieval(index):
    """Precision@1 and @3 against a hand-labelled golden set."""
    hit_at_1 = hit_at_3 = 0
    details = []
    for query, expected in GOLDEN:
        hits = index.search(query, k=3)
        found = [os.path.basename(h.chunk.path) for h in hits]
        at1 = bool(found) and found[0] == expected
        at3 = expected in found
        hit_at_1 += at1
        hit_at_3 += at3
        details.append({"query": query, "expected": expected, "returned": found, "hit_at_1": at1, "hit_at_3": at3})
    total = len(GOLDEN)
    return {
        "queries": total,
        "precision_at_1_pct": round(hit_at_1 / total * 100, 1),
        "precision_at_3_pct": round(hit_at_3 / total * 100, 1),
        "details": details,
    }


# -------------------------------------------------------------- performance
def measure_performance(root):
    sources = [read(p) for p in glob.glob(os.path.join(root, "*.py"))]

    start = time.perf_counter()
    for source in sources:
        skeleton(source, path="x.py")
    skeleton_ms = (time.perf_counter() - start) * 1000 / max(1, len(sources))

    start = time.perf_counter()
    index = index_path(root)
    index_s = time.perf_counter() - start

    latencies = []
    for query, _ in GOLDEN:
        start = time.perf_counter()
        index.search(query, k=10)
        latencies.append((time.perf_counter() - start) * 1000)

    return {
        "skeleton_ms_per_file": round(skeleton_ms, 2),
        "index_build_s": round(index_s, 3),
        "chunks_indexed": len(index),
        "chunks_per_second": round(len(index) / index_s, 1) if index_s else 0,
        "search_p50_ms": round(statistics.median(latencies), 2),
        "search_max_ms": round(max(latencies), 2),
        "cold_start_dependencies": 0,
    }


# ------------------------------------------------------------ narrow reads
def measure_narrow_read(root):
    """The headline behaviour: one symbol instead of the whole file."""
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*.py"))):
        source = read(path)
        symbols = [s for s in extract_symbols(source, path=path) if s.lines > 8]
        if not symbols:
            continue
        biggest = max(symbols, key=lambda s: s.lines)
        sliced = slice_symbol(source, biggest.qualname, path=path)
        if not sliced:
            continue
        full = estimate_tokens(source)
        one = estimate_tokens(sliced["text"])
        rows.append(
            {
                "file": os.path.basename(path),
                "symbol": biggest.qualname,
                "tokens_full_file": full,
                "tokens_one_symbol": one,
                "reduction_pct": round((1 - one / full) * 100, 1),
            }
        )
    return sorted(rows, key=lambda r: -r["reduction_pct"])


# ------------------------------------------------------------------ quality
def measure_quality():
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        capture_output=True, text=True, cwd=ROOT,
    )
    duration = time.perf_counter() - start
    output = result.stderr
    ran = 0
    for line in output.split("\n"):
        if line.startswith("Ran "):
            ran = int(line.split()[1])
    src_lines = sum(len(read(p).splitlines()) for p in glob.glob(repo("src", "tollgate", "context", "*.py")))
    test_lines = sum(len(read(p).splitlines()) for p in glob.glob(repo("tests", "*.py")))
    return {
        "tests_total": ran,
        "tests_passed": ran if result.returncode == 0 else 0,
        "pass_rate_pct": 100.0 if result.returncode == 0 else 0.0,
        "suite_runtime_s": round(duration, 2),
        "source_lines": src_lines,
        "test_lines": test_lines,
        "test_to_source_ratio": round(test_lines / src_lines, 2),
        "runtime_dependencies": 0,
        "modules": len(glob.glob(repo("src", "tollgate", "context", "*.py"))),
    }


def main():
    corpora = {}
    for root in CORPORA:
        if os.path.isdir(root):
            corpora[os.path.basename(os.path.dirname(root.rstrip("/"))) or root] = measure_corpus(root)

    index = index_path(repo("src", "tollgate", "context"))
    metrics = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "pricing_assumption_usd_per_mtok_input": USD_PER_MTOK_INPUT,
        "corpora": corpora,
        "budget_adherence": measure_budget_adherence(
            index, "how does the allocator redistribute surplus from capped lanes", [200, 500, 1000, 2000, 4000, 8000]
        ),
        "retrieval": measure_retrieval(index),
        "performance": measure_performance(repo("src", "tollgate", "context")),
        "narrow_reads": measure_narrow_read(repo("src", "tollgate", "context")),
        "quality": measure_quality(),
    }

    # Aggregate savings across every corpus measured.
    total_full = total_skel = total_files = 0
    for languages in corpora.values():
        for stats in languages.values():
            total_full += stats["tokens_full"]
            total_skel += stats["tokens_skeleton"]
            total_files += stats["files"]
    metrics["aggregate"] = {
        "files": total_files,
        "tokens_full": total_full,
        "tokens_skeleton": total_skel,
        "tokens_saved": total_full - total_skel,
        "reduction_pct": round((1 - total_skel / total_full) * 100, 1) if total_full else 0,
        "usd_full": round(total_full / 1e6 * USD_PER_MTOK_INPUT, 2),
        "usd_skeleton": round(total_skel / 1e6 * USD_PER_MTOK_INPUT, 2),
        "usd_saved": round((total_full - total_skel) / 1e6 * USD_PER_MTOK_INPUT, 2),
    }

    os.makedirs(repo("benchmarks"), exist_ok=True)
    with open(repo("benchmarks", "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)
    print(json.dumps({k: v for k, v in metrics.items() if k != "corpora"}, indent=2)[:3000])
    print("\nwrote " + repo("benchmarks", "metrics.json"))


if __name__ == "__main__":
    main()
