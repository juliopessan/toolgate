"""End-to-end walkthrough of all six layers, runnable against this repository.

    python examples/quickstart.py [path]
"""

import os
import sys

# Anchor to the repository, not to the caller's working directory, so the
# walkthrough runs from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from tollgate.context import (
    Headroom,
    Lane,
    chunk_source,
    estimate_tokens,
    index_path,
    pack_query,
    skeleton,
    slice_symbol,
)

TARGET = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "src", "tollgate", "context")


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


rule("1. Tokens - what does it cost?")
with open(os.path.join(ROOT, "src", "tollgate", "context", "headroom.py"), encoding="utf-8") as handle:
    source = handle.read()
print(f"headroom.py is {estimate_tokens(source):,} tokens to read in full")

rule("2. Structure - what is worth quoting?")
outline_text = skeleton(source, path="headroom.py")
print(outline_text[:600])
print(
    f"\n-> skeleton: {estimate_tokens(outline_text):,} tokens "
    f"({(1 - estimate_tokens(outline_text) / estimate_tokens(source)) * 100:.0f}% smaller)"
)

rule("3. One symbol instead of the whole file")
sliced = slice_symbol(source, "Headroom.plan", path="headroom.py")
print(f"lines {sliced['start']}-{sliced['end']}, {estimate_tokens(sliced['text']):,} tokens")
print("\n".join(sliced["text"].split("\n")[:6]) + "\n    ...")

rule("4. Headroom - what can I afford?")
headroom = Headroom(window=200_000, reserve_output=8_000)
headroom.spend("conversation", 150_000)
print(headroom.render())
plan = headroom.plan(
    [
        Lane("structure", weight=1, minimum=200, maximum=4_000),
        Lane("semantic", weight=3, minimum=500),
        Lane("diagnostics", weight=1, maximum=2_000),
    ]
)
print("\nlane allocation:")
for allocation in plan:
    flag = f"  [{allocation.clamped}]" if allocation.clamped else ""
    print(f"  {allocation.lane:<16} {allocation.tokens:>8,}{flag}")

rule("5. Chunking + semantic retrieval")
print(f"{len(chunk_source(source, path='headroom.py'))} structure-aligned chunks from headroom.py")
index = index_path(TARGET)
print(f"indexed {len(index)} chunks from {os.path.relpath(TARGET, ROOT)}\n")
query = "how is the remaining budget divided between competing lanes?"
for hit in index.search(query, k=5):
    print(f"  {hit.score:7.3f}  {hit.chunk.tokens:>5,}t  {hit.chunk.citation()}")

rule("6. Pack - what actually gets sent")
context = pack_query(query, index, budget=plan[1].tokens // 10, headroom=headroom)
print(context.manifest())
print(f"\nassembled context: {context.tokens:,} tokens, every section cited.")
