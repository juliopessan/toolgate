import unittest

from context_ledger.context.tokens import (
    detect_profile,
    estimate_cost,
    estimate_tokens,
    measure,
    strip_ansi,
    trim_to_budget,
)


class TestEstimation(unittest.TestCase):
    def test_empty_input_costs_nothing(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None or ""), 0)

    def test_ansi_codes_are_not_billed(self):
        plain = "build failed"
        coloured = "\x1b[31mbuild failed\x1b[0m"
        self.assertEqual(strip_ansi(coloured), plain)
        self.assertEqual(estimate_tokens(coloured), estimate_tokens(plain))

    def test_denser_profiles_cost_more_per_character(self):
        text = "x" * 1000
        self.assertGreater(estimate_tokens(text, "json"), estimate_tokens(text, "prose"))
        self.assertGreater(estimate_tokens(text, "code"), estimate_tokens(text, "prose"))

    def test_estimate_never_undercounts_a_conservative_baseline(self):
        # One token per four characters is the floor every profile must clear.
        text = "the quick brown fox jumps over the lazy dog " * 20
        self.assertGreaterEqual(estimate_tokens(text), len(text) // 4)

    def test_profile_detection(self):
        self.assertEqual(detect_profile('{"a": 1, "b": [2, 3], "c": {"d": 4}}'), "json")
        self.assertEqual(detect_profile("function f(a) { return a && a.b; }\nif (x) { y(); }"), "code")
        self.assertEqual(detect_profile("# Title\n\nSome ordinary prose here."), "markdown")
        self.assertEqual(
            detect_profile("Plain sentences with no punctuation of note and nothing structural"),
            "prose",
        )

    def test_measure_reports_its_reasoning(self):
        result = measure("def f():\n    return {1: 2}")
        self.assertEqual(result.lines, 2)
        self.assertEqual(result.profile, "code")
        self.assertGreater(result.tokens, 0)
        self.assertIn("chars_per_token", result.to_dict())

    def test_line_count_ignores_the_trailing_newline(self):
        # Regression: a file ending in a newline was reported with one line more
        # than it has, so `count` and `skeleton` disagreed about the same file.
        self.assertEqual(measure("a\nb\n").lines, 2)
        self.assertEqual(measure("a\nb").lines, 2)
        self.assertEqual(measure("").lines, 0)

    def test_cost(self):
        self.assertEqual(estimate_cost(1_000_000, input_per_mtok=3.0), 3.0)
        self.assertEqual(estimate_cost(0, 3.0, 15.0, output_tokens=1_000_000), 15.0)


class TestTrim(unittest.TestCase):
    def setUp(self):
        self.text = "\n".join(f"line {i}" for i in range(500))

    def test_text_within_budget_is_untouched(self):
        result = trim_to_budget("short", 1000)
        self.assertFalse(result.truncated)
        self.assertEqual(result.text, "short")

    def test_every_mode_respects_the_budget(self):
        for keep in ("head", "tail", "both"):
            with self.subTest(keep=keep):
                result = trim_to_budget(self.text, 200, keep=keep)
                self.assertTrue(result.truncated)
                self.assertLessEqual(result.tokens, 200, f"{keep} overran its budget")

    def test_head_keeps_the_beginning_and_tail_the_end(self):
        head = trim_to_budget(self.text, 200, keep="head")
        tail = trim_to_budget(self.text, 200, keep="tail")
        self.assertIn("line 0", head.text)
        self.assertNotIn("line 499", head.text)
        self.assertIn("line 499", tail.text)
        self.assertNotIn("line 0\n", tail.text)

    def test_both_keeps_each_end_and_says_what_it_dropped(self):
        result = trim_to_budget(self.text, 300, keep="both")
        self.assertIn("line 0", result.text)
        self.assertIn("line 499", result.text)
        self.assertIn("lines elided", result.text)

    def test_marker_is_always_present_and_carries_the_hint(self):
        result = trim_to_budget(self.text, 200, hint="Use --grep to narrow.")
        self.assertIn("[tools-tokens] truncated", result.text)
        self.assertIn("Use --grep to narrow.", result.text)

    def test_single_line_longer_than_the_budget_still_terminates(self):
        result = trim_to_budget("x" * 100_000, 100)
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.tokens, 100)


if __name__ == "__main__":
    unittest.main()
