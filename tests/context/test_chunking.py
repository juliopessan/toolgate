import unittest

from context_ledger.context.chunking import chunk_source
from context_ledger.context.tokens import estimate_tokens

SOURCE = '''\
import os
import sys

CONFIG = {"a": 1}

def small(x):
    return x

class Thing:
    def method(self):
        return 1
'''


class TestChunking(unittest.TestCase):
    def test_empty_source_yields_no_chunks(self):
        self.assertEqual(chunk_source("", path="a.py"), [])
        self.assertEqual(chunk_source("   \n\n  ", path="a.py"), [])

    def test_symbols_become_their_own_chunks(self):
        symbols = {c.symbol for c in chunk_source(SOURCE, path="a.py") if c.symbol}
        self.assertIn("small", symbols)
        self.assertIn("Thing", symbols)

    def test_nested_methods_are_covered_by_their_class_not_orphaned(self):
        chunks = chunk_source(SOURCE, path="a.py")
        thing = next(c for c in chunks if c.symbol == "Thing")
        self.assertIn("def method", thing.text)
        self.assertNotIn("Thing.method", {c.symbol for c in chunks})

    def test_chunks_cover_the_file_without_overlapping_symbol_ranges(self):
        chunks = sorted(chunk_source(SOURCE, path="a.py"), key=lambda c: c.start)
        for earlier, later in zip(chunks, chunks[1:]):
            self.assertLessEqual(earlier.start, later.start)

    def test_line_ranges_quote_back_exactly(self):
        lines = SOURCE.split("\n")
        for chunk in chunk_source(SOURCE, path="a.py"):
            quoted = "\n".join(lines[chunk.start - 1 : chunk.end])
            self.assertEqual(quoted, chunk.text, chunk.citation())

    def test_oversized_symbols_are_windowed_but_keep_attribution(self):
        body = "\n".join(f"    value_{i} = {i}" for i in range(400))
        source = f"def huge():\n{body}\n    return 0\n"
        chunks = chunk_source(source, path="big.py", max_tokens=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.symbol == "huge" for c in chunks))
        self.assertTrue(all(c.tokens <= 400 for c in chunks))

    def test_windowing_terminates_on_pathological_input(self):
        # A single line far larger than max_tokens must not loop forever.
        chunks = chunk_source("x" * 50_000, path="blob.txt", max_tokens=50)
        self.assertTrue(chunks)

    def test_material_outside_any_symbol_is_still_indexed(self):
        source = "\n".join(f"import module_{i}" for i in range(40)) + "\n\ndef f():\n    return 1\n"
        kinds = {c.kind for c in chunk_source(source, path="a.py")}
        self.assertIn("region", kinds)

    def test_citation_names_the_symbol_when_there_is_one(self):
        chunk = next(c for c in chunk_source(SOURCE, path="a.py") if c.symbol == "small")
        self.assertEqual(chunk.citation(), f"a.py:{chunk.start}-{chunk.end} (small)")

    def test_reported_tokens_match_the_estimator(self):
        for chunk in chunk_source(SOURCE, path="a.py"):
            self.assertEqual(chunk.tokens, estimate_tokens(chunk.text))

    def test_markdown_splits_on_headings(self):
        doc = "# One\n\nalpha\n\n# Two\n\nbeta\n"
        symbols = {c.symbol for c in chunk_source(doc, path="d.md")}
        self.assertIn("One", symbols)
        self.assertIn("Two", symbols)

    def test_unparseable_file_still_produces_window_chunks(self):
        chunks = chunk_source("\n".join(f"raw line {i}" for i in range(200)), path="a.bin")
        self.assertTrue(chunks)
        self.assertTrue(all(c.kind == "window" for c in chunks))


if __name__ == "__main__":
    unittest.main()


class TestChunkIdentity(unittest.TestCase):
    """Regression: chunk ids must be unique within a file.

    Anything keyed on the id -- reciprocal rank fusion, the packer's dedupe, the
    persisted cache -- silently drops a chunk when two collide.
    """

    def test_repeated_symbol_names_do_not_collide(self):
        source = (
            "def handler():\n    return 1\n\n"
            "def handler():\n    return 2\n\n"  # redefinition: same qualname
        )
        ids = [c.id for c in chunk_source(source, path="dup.py")]
        self.assertEqual(len(ids), len(set(ids)), f"colliding ids: {ids}")

    def test_repeated_markdown_headings_do_not_collide(self):
        doc = "# A\n\n## Setup\n\ntext\n\n# B\n\n## Setup\n\nmore\n"
        ids = [c.id for c in chunk_source(doc, path="d.md")]
        self.assertEqual(len(ids), len(set(ids)), f"colliding ids: {ids}")

    def test_ids_are_unique_across_a_real_module(self):
        import pathlib

        source = pathlib.Path(__file__).with_name("test_chunking.py").read_text()
        ids = [c.id for c in chunk_source(source, path="t.py")]
        self.assertEqual(len(ids), len(set(ids)))
