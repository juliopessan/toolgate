import json
import os
import sys
import tempfile
import unittest

from tollgate.context.cli import main
from tollgate.context.headroom import Headroom
from tollgate.context.hooks import run_hook, suggest_for_read, suggest_for_search


class HookTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.big = os.path.join(self.root, "big.py")
        with open(self.big, "w") as handle:
            for i in range(80):
                handle.write(
                    f"def handler_{i}(request):\n"
                    f'    """Handle request {i}."""\n'
                    + "".join(f"    step_{j} = request.get({j})\n" for j in range(15))
                    + "    return step_0\n\n"
                )
        self.small = os.path.join(self.root, "small.py")
        with open(self.small, "w") as handle:
            handle.write("def tiny():\n    return 1\n")
        # Above the "too small to bother" floor but well under the "large enough
        # to be worth a skeleton regardless of pressure" ceiling.
        self.medium = os.path.join(self.root, "medium.py")
        with open(self.medium, "w") as handle:
            for i in range(24):
                handle.write(
                    f"def helper_{i}(value):\n"
                    + "".join(f"    part_{j} = value + {j}\n" for j in range(12))
                    + "    return part_0\n\n"
                )

    def tearDown(self):
        self.tmp.cleanup()


class TestReadSuggestions(HookTestBase):
    def test_a_hot_context_gets_a_skeleton_that_actually_saves(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 90_000)
        result = suggest_for_read("big.py", root=self.root, headroom=headroom)
        self.assertEqual(result["action"], "suggest")
        self.assertGreater(result["savings_tokens"], 0)
        self.assertLess(result["hint_tokens"], result["full_tokens"])
        self.assertIn("handler_0", result["hint"])

    def test_a_small_file_is_left_alone(self):
        result = suggest_for_read("small.py", root=self.root)
        self.assertEqual(result["action"], "pass")
        self.assertIn("full read is cheapest", result["reason"])

    def test_a_cool_context_leaves_a_moderate_file_alone(self):
        headroom = Headroom(window=1_000_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 1_000)
        result = suggest_for_read("medium.py", root=self.root, headroom=headroom)
        self.assertEqual(result["action"], "pass")
        self.assertIn("context is cool", result["reason"])

    def test_a_very_large_file_is_worth_a_skeleton_even_when_cool(self):
        # The saving is large and unconditional; waiting for pressure to build
        # would mean paying for the full read exactly once, needlessly.
        headroom = Headroom(window=1_000_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 1_000)
        result = suggest_for_read("big.py", root=self.root, headroom=headroom)
        self.assertEqual(result["action"], "suggest")
        self.assertEqual(result["pressure"], "cool")

    def test_it_never_blocks_only_advises(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 99_000)
        for path in ("big.py", "small.py", "missing.py", "../escape.py"):
            with self.subTest(path=path):
                action = suggest_for_read(path, root=self.root, headroom=headroom)["action"]
                self.assertIn(action, {"pass", "suggest"})

    def test_path_traversal_out_of_the_workspace_is_refused(self):
        result = suggest_for_read("../../etc/passwd", root=self.root)
        self.assertEqual(result["action"], "pass")
        self.assertIn("within the workspace root", result["reason"])

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(suggest_for_read("nope.py", root=self.root)["action"], "pass")

    def test_savings_floor_is_respected(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 95_000)
        result = suggest_for_read("big.py", root=self.root, headroom=headroom, min_savings=10**9)
        self.assertEqual(result["action"], "pass")
        self.assertIn("not worth the detour", result["reason"])


class TestSearchSuggestions(HookTestBase):
    def test_a_query_returns_ranked_citations(self):
        result = suggest_for_search("handle request", root=self.root)
        self.assertEqual(result["action"], "suggest")
        self.assertTrue(result["hits"])
        self.assertIn(":", result["hits"][0]["citation"])

    def test_an_empty_query_passes_through(self):
        self.assertEqual(suggest_for_search("", root=self.root)["action"], "pass")


class TestRouting(HookTestBase):
    def test_read_tools_route_to_the_read_handler(self):
        payload = {"tool": "Read", "input": {"file_path": "big.py"}, "context_used": 190_000}
        self.assertEqual(run_hook(payload, root=self.root)["action"], "suggest")

    def test_alternate_host_key_names_are_accepted(self):
        payload = {"tool_name": "read_file", "tool_input": {"path": "big.py"}, "context_used": 190_000}
        self.assertEqual(run_hook(payload, root=self.root)["action"], "suggest")

    def test_search_tools_route_to_the_search_handler(self):
        payload = {"tool": "Grep", "input": {"pattern": "handler"}}
        self.assertEqual(run_hook(payload, root=self.root)["action"], "suggest")

    def test_unknown_tools_pass_through_untouched(self):
        result = run_hook({"tool": "Bash", "input": {"command": "ls"}}, root=self.root)
        self.assertEqual(result["action"], "pass")
        self.assertIn("no handler", result["reason"])

    def test_an_empty_payload_does_not_raise(self):
        self.assertEqual(run_hook({}, root=self.root)["action"], "pass")


class TestCli(HookTestBase):
    """CLI smoke tests. Output is swallowed: a test suite that prints its
    fixtures' contents drowns the one line that matters when something fails."""

    def setUp(self):
        super().setUp()
        import contextlib
        import io

        self._silence = contextlib.redirect_stdout(io.StringIO())
        self._silence.__enter__()

    def tearDown(self):
        self._silence.__exit__(None, None, None)
        super().tearDown()

    def test_count_and_skeleton_and_symbol_exit_zero(self):
        self.assertEqual(main(["count", self.big, "--json"]), 0)
        self.assertEqual(main(["skeleton", self.big, "--json"]), 0)
        self.assertEqual(main(["symbol", self.big, "handler_3", "--json"]), 0)
        self.assertEqual(main(["outline", self.big, "--json"]), 0)
        self.assertEqual(main(["chunk", self.big, "--json"]), 0)

    def test_a_missing_symbol_exits_one(self):
        self.assertEqual(main(["symbol", self.big, "does_not_exist"]), 1)

    def test_headroom_rejects_malformed_flags(self):
        self.assertEqual(main(["headroom", "--spend", "bad-format"]), 2)
        self.assertEqual(main(["headroom", "--lane", "name:notanumber"]), 2)

    def test_headroom_plans_lanes(self):
        self.assertEqual(main(["headroom", "--spend", "a=1000", "--lane", "b:1:100:500", "--json"]), 0)

    def test_search_and_pack_run_over_a_real_directory(self):
        self.assertEqual(main(["search", "handle request", "--root", self.root, "--json"]), 0)
        self.assertEqual(main(["pack", "handle request", "--root", self.root, "--budget", "800", "--json"]), 0)

    def test_a_missing_file_exits_with_a_usage_code(self):
        self.assertEqual(main(["count", os.path.join(self.root, "absent.py")]), 2)


if __name__ == "__main__":
    unittest.main()


class TestHookCliNeverRaises(HookTestBase):
    """Regression: the hook adapter must never put a traceback in front of a host.

    A hook that dies with a stack trace is worse than one that does nothing --
    the host gets garbage on stdout and the agent loses a tool call.
    """

    def _run(self, payload_text):
        import contextlib
        import io

        stdin, stdout = sys.stdin, sys.stdout
        captured = io.StringIO()
        try:
            sys.stdin = io.StringIO(payload_text)
            with contextlib.redirect_stdout(captured):
                code = main(["hook", "--root", self.root])
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        return code, captured.getvalue().strip()

    def test_malformed_json_passes_through_instead_of_crashing(self):
        code, out = self._run('{"tool":"Read"')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "pass")
        self.assertIn("malformed JSON", payload["reason"])

    def test_empty_stdin_is_handled(self):
        code, out = self._run("")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "pass")

    def test_a_json_array_is_not_a_payload(self):
        code, out = self._run("[1, 2, 3]")
        self.assertEqual(code, 0)
        self.assertIn("not a JSON object", json.loads(out)["reason"])

    def test_a_valid_payload_still_works(self):
        code, out = self._run(json.dumps({"tool": "Read", "input": {"file_path": "big.py"}, "context_used": 190_000}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "suggest")


class TestStoreBackedHook(HookTestBase):
    """The ledger only records what actually happened."""

    def test_an_offer_is_recorded_as_an_offer_not_a_saving(self):
        from tollgate.context.headroom import Headroom
        from tollgate.context.hooks import suggest_for_read
        from tollgate.context.pack import open_store

        store = open_store(self.root)
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 95_000)
        result = suggest_for_read("big.py", root=self.root, headroom=headroom, store=store)
        self.assertEqual(result["action"], "suggest")

        summary = store.summary()
        self.assertEqual(summary["offers"], 1)
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(summary["tokens_saved"], 0, "an untaken offer saved nothing")
        store.close()

    def test_a_host_reporting_acceptance_turns_it_into_a_saving(self):
        from tollgate.context.hooks import run_hook
        from tollgate.context.pack import open_store

        store = open_store(self.root)
        run_hook(
            {"tool": "tools-tokens/accepted",
             "input": {"path": "big.py", "tokens_full": 9000, "tokens_served": 900}},
            root=self.root, store=store,
        )
        self.assertEqual(store.summary()["tokens_saved"], 8100)
        store.close()

    def test_the_hook_works_with_no_store_at_all(self):
        from tollgate.context.hooks import run_hook

        result = run_hook({"tool": "Read", "input": {"file_path": "big.py"}, "context_used": 190_000},
                          root=self.root, store=None)
        self.assertEqual(result["action"], "suggest")
