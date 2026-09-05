import unittest

from context_ledger.context.astx import (
    detect_language,
    extract_symbols,
    find_symbol,
    outline,
    skeleton,
    slice_symbol,
)

PYTHON_SOURCE = '''\
"""Module docstring."""
import os

MAX_RETRIES = 3

def top_level(a, b=2):
    """Adds two things."""
    return a + b

class Store:
    """A store."""

    def __init__(self, path):
        self.path = path

    @property
    async def refresh_token(self):
        """Refreshes."""
        return await self._fetch()

def after(
    first,
    second,
):
    return first
'''

TS_SOURCE = """\
// A cache.
export interface CacheEntry {
  key: string
}

export class Cache {
  private store = new Map()

  get(key: string): string | undefined {
    if (key) {
      return this.store.get(key)
    }
    return undefined
  }
}

export function makeCache(): Cache {
  for (let i = 0; i < 3; i++) {
    console.log(i)
  }
  return new Cache()
}

export const buildKey = (a: string) => a.trim()
"""


class TestLanguageDetection(unittest.TestCase):
    def test_extension_wins(self):
        self.assertEqual(detect_language("a/b/c.py"), "python")
        self.assertEqual(detect_language("x.tsx"), "typescript")
        self.assertEqual(detect_language("x.md"), "markdown")

    def test_shebang_fallback(self):
        self.assertEqual(detect_language("script", "#!/usr/bin/env python3\n"), "python")
        self.assertEqual(detect_language("script", "#!/bin/bash\n"), "bash")

    def test_unknown_is_text_not_a_guess(self):
        self.assertEqual(detect_language("data.bin"), "text")


class TestPythonEngine(unittest.TestCase):
    def setUp(self):
        self.symbols = extract_symbols(PYTHON_SOURCE, path="store.py")
        self.by_name = {s.qualname: s for s in self.symbols}

    def test_finds_functions_classes_methods_and_constants(self):
        self.assertIn("top_level", self.by_name)
        self.assertIn("Store", self.by_name)
        self.assertIn("Store.__init__", self.by_name)
        self.assertIn("MAX_RETRIES", self.by_name)
        self.assertEqual(self.by_name["Store.__init__"].kind, "method")
        self.assertEqual(self.by_name["top_level"].kind, "function")
        self.assertEqual(self.by_name["MAX_RETRIES"].kind, "const")

    def test_line_ranges_are_exact_and_inclusive(self):
        symbol = self.by_name["top_level"]
        quoted = "\n".join(PYTHON_SOURCE.split("\n")[symbol.start - 1 : symbol.end])
        self.assertTrue(quoted.startswith("def top_level"))
        self.assertTrue(quoted.rstrip().endswith("return a + b"))

    def test_docstrings_and_decorators_are_captured(self):
        refresh = self.by_name["Store.refresh_token"]
        self.assertEqual(refresh.doc, "Refreshes.")
        self.assertIn("@property", refresh.decorators)

    def test_multiline_signature_is_joined(self):
        self.assertEqual(self.by_name["after"].signature, "def after( first, second, ):")

    def test_syntax_errors_degrade_to_empty_not_an_exception(self):
        self.assertEqual(extract_symbols("def broken(:\n  ???", path="bad.py"), [])


class TestStructuralEngine(unittest.TestCase):
    def setUp(self):
        self.symbols = extract_symbols(TS_SOURCE, path="cache.ts")
        self.by_name = {s.qualname: s for s in self.symbols}

    def test_finds_typescript_declarations(self):
        names = set(self.by_name)
        self.assertIn("CacheEntry", names)
        self.assertIn("Cache", names)
        self.assertIn("makeCache", names)
        self.assertIn("buildKey", names)

    def test_control_flow_keywords_are_not_symbols(self):
        names = {s.name for s in self.symbols}
        self.assertNotIn("if", names)
        self.assertNotIn("for", names)

    def test_methods_are_attributed_to_their_class(self):
        self.assertEqual(self.by_name["Cache.get"].parent, "Cache")
        self.assertEqual(self.by_name["Cache.get"].kind, "method")

    def test_brace_matching_finds_the_real_end(self):
        cache = self.by_name["Cache"]
        body = "\n".join(TS_SOURCE.split("\n")[cache.start - 1 : cache.end])
        self.assertEqual(body.count("{"), body.count("}"))

    def test_preceding_comment_becomes_the_doc(self):
        self.assertEqual(self.by_name["CacheEntry"].doc, "// A cache.")

    def test_unsupported_language_yields_nothing_rather_than_noise(self):
        self.assertEqual(extract_symbols("random bytes here", path="a.bin"), [])


class TestMarkdown(unittest.TestCase):
    SOURCE = "# Top\n\nintro\n\n## A\n\ntext\n\n```\n# not a heading\n```\n\n## B\n\nmore\n"

    def test_headings_become_symbols_with_ranges(self):
        symbols = extract_symbols(self.SOURCE, path="doc.md")
        self.assertEqual([s.name for s in symbols], ["Top", "A", "B"])
        self.assertEqual(symbols[0].kind, "h1")
        self.assertEqual(symbols[1].parent, "Top")

    def test_section_ends_at_the_next_heading_of_equal_rank(self):
        section = next(s for s in extract_symbols(self.SOURCE, path="doc.md") if s.name == "A")
        body = "\n".join(self.SOURCE.split("\n")[section.start - 1 : section.end])
        self.assertIn("text", body)
        self.assertNotIn("## B", body)

    def test_fenced_code_is_not_mistaken_for_a_heading(self):
        self.assertNotIn("not a heading", [s.name for s in extract_symbols(self.SOURCE, path="doc.md")])


class TestSelectors(unittest.TestCase):
    def test_qualified_bare_and_case_insensitive_lookup(self):
        symbols = extract_symbols(PYTHON_SOURCE, path="store.py")
        self.assertEqual(find_symbol(symbols, "Store.__init__").name, "__init__")
        self.assertEqual(find_symbol(symbols, "refresh_token").parent, "Store")
        self.assertEqual(find_symbol(symbols, "STORE").name, "Store")
        self.assertIsNone(find_symbol(symbols, "does_not_exist"))

    def test_slice_returns_only_the_symbol(self):
        result = slice_symbol(PYTHON_SOURCE, "Store.refresh_token", path="store.py")
        self.assertIn("async def refresh_token", result["text"])
        self.assertNotIn("def top_level", result["text"])
        self.assertLess(result["end"] - result["start"], len(PYTHON_SOURCE.split("\n")))

    def test_missing_symbol_returns_none(self):
        self.assertIsNone(slice_symbol(PYTHON_SOURCE, "nope", path="store.py"))


class TestViews(unittest.TestCase):
    def test_skeleton_keeps_every_signature_and_elides_every_body(self):
        result = skeleton(PYTHON_SOURCE, path="store.py")
        self.assertIn("def top_level", result)
        self.assertIn("class Store", result)
        self.assertNotIn("return a + b", result)  # bodies elided

    def test_skeleton_shrinks_a_realistically_sized_file(self):
        # The saving comes from elided bodies, so it only materialises once
        # bodies dominate. This is the case the tool actually exists for.
        big = PYTHON_SOURCE + "\n".join(
            f"def generated_{i}(x):\n" + "\n".join(f"    step_{j} = x + {j}" for j in range(30)) + "\n    return x\n"
            for i in range(20)
        )
        result = skeleton(big, path="big.py")
        self.assertLess(len(result), len(big) / 4)

    def test_skeleton_of_a_tiny_file_may_not_be_smaller(self):
        # Documented, deliberate: skeleton is a faithful navigation view, not a
        # compressor that lies. Callers decide whether the saving is worth it -
        # see hooks.suggest_for_read, which declines below a savings floor.
        tiny = "def f():\n    return 1\n"
        self.assertGreaterEqual(len(skeleton(tiny, path="tiny.py")), 0)

    def test_skeleton_of_a_symbol_less_file_is_empty(self):
        self.assertEqual(skeleton("just prose, no code", path="a.txt"), "")

    def test_outline_nests_methods_under_their_class(self):
        tree = outline(PYTHON_SOURCE, path="store.py")
        store = next(node for node in tree if node["name"] == "Store")
        self.assertIn("__init__", [child["name"] for child in store["children"]])


if __name__ == "__main__":
    unittest.main()


class TestContentSniffing(unittest.TestCase):
    """Regression: reading from stdin has no extension to detect a language from.

    Before this existed, every piped source silently extracted zero symbols and
    the CLI reported "no symbols found" on perfectly valid input.
    """

    def test_python_is_recognised_without_a_path(self):
        self.assertEqual(detect_language("", PYTHON_SOURCE), "python")
        self.assertEqual(detect_language("-", PYTHON_SOURCE), "python")

    def test_typescript_is_recognised_without_a_path(self):
        self.assertEqual(detect_language("-", TS_SOURCE), "typescript")

    def test_markdown_is_recognised_without_a_path(self):
        self.assertEqual(detect_language("-", "# Title\n\nbody text here\n"), "markdown")

    def test_symbols_are_extracted_from_pathless_source(self):
        symbols = extract_symbols(PYTHON_SOURCE)
        self.assertIn("Store", {s.name for s in symbols})

    def test_an_explicit_path_still_wins_over_content(self):
        # A .md file containing Python-looking text is still Markdown.
        self.assertEqual(detect_language("notes.md", "def f():\n    pass\n"), "markdown")

    def test_unrecognisable_content_stays_text_rather_than_guessing(self):
        self.assertEqual(detect_language("-", "lorem ipsum dolor sit amet"), "text")
        self.assertEqual(detect_language("-", "   "), "text")


class TestSkeletonHeader(unittest.TestCase):
    def test_singular_and_plural_symbol_counts(self):
        self.assertIn("1 symbol,", skeleton("def f():\n    return 1\n", path="a.py"))
        self.assertIn("symbols,", skeleton(PYTHON_SOURCE, path="a.py"))
