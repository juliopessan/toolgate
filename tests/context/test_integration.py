"""End-to-end: the six layers composing into one budget-respecting answer.

These are the guarantees the package makes to a caller. If one of these breaks,
the tool is not safe to plug into an agent, whatever the unit tests say.
"""

import os
import tempfile
import unittest

from tollgate.context import (
    Headroom,
    Lane,
    estimate_tokens,
    index_path,
    pack_query,
    skeleton,
    slice_symbol,
)

MODULE = '''\
"""Payment processing."""

DEFAULT_TIMEOUT = 30

class PaymentGateway:
    """Talks to the payment provider."""

    def charge(self, amount, currency="BRL"):
        """Charge a card, retrying on timeout."""
        for attempt in range(3):
            try:
                return self._post("/charge", amount, currency)
            except TimeoutError:
                continue
        raise RuntimeError("charge failed")

    def refund(self, transaction_id):
        """Reverse a settled charge."""
        return self._post("/refund", transaction_id)

def format_receipt(transaction):
    """Render a receipt for a completed transaction."""
    return f"{transaction.id}: {transaction.amount}"
'''


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        with open(os.path.join(self.root, "payments.py"), "w") as handle:
            handle.write(MODULE)
        with open(os.path.join(self.root, "shipping.py"), "w") as handle:
            handle.write('def estimate_delivery(zone):\n    """Days to deliver."""\n    return zone.days\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_full_pipeline_answers_a_question_within_budget(self):
        headroom = Headroom(window=50_000, reserve_output=2_000, safety_margin=0.0)
        headroom.spend("conversation", 30_000)
        index = index_path(self.root)
        budget = 400

        context = pack_query("how are failed charges retried?", index, budget=budget, headroom=headroom)

        self.assertLessEqual(context.tokens, budget, "the packer must never overrun its budget")
        self.assertIn("payments.py", context.text, "it must find the file the question is about")
        self.assertTrue(context.sections, "it must return something to work with")
        for section in context.sections:
            self.assertIn(f"--- {section.citation} ---", context.text, "every section must be cited")

    def test_a_tighter_budget_yields_less_but_never_more(self):
        index = index_path(self.root)
        wide = pack_query("retry a failed charge", index, budget=2_000)
        narrow = pack_query("retry a failed charge", index, budget=200)
        self.assertLessEqual(narrow.tokens, 200)
        self.assertLessEqual(narrow.tokens, wide.tokens)
        self.assertLessEqual(len(narrow.sections), len(wide.sections))

    def test_rising_pressure_tightens_retrieval_without_caller_involvement(self):
        index = index_path(self.root)
        cool = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        cool.spend("session", 10_000)
        hot = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        hot.spend("session", 95_000)
        self.assertGreater(
            cool.policy["semantic_k"],
            hot.policy["semantic_k"],
            "the same call site must retrieve less as the window fills",
        )

    def test_the_narrow_read_is_dramatically_cheaper_than_the_full_read(self):
        full = estimate_tokens(MODULE)
        one_symbol = estimate_tokens(slice_symbol(MODULE, "PaymentGateway.charge", path="p.py")["text"])
        self.assertLess(one_symbol, full / 2)

    def test_a_skeleton_lists_every_symbol_it_elides(self):
        result = skeleton(MODULE, path="payments.py")
        for name in ("PaymentGateway", "charge", "refund", "format_receipt", "DEFAULT_TIMEOUT"):
            self.assertIn(name, result)

    def test_lane_budgets_and_packing_agree(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("conversation", 90_000)
        allocations = {a.lane: a.tokens for a in headroom.plan([Lane("semantic", 1, maximum=500)])}
        index = index_path(self.root)
        context = pack_query("charge", index, budget=allocations["semantic"], headroom=headroom)
        self.assertLessEqual(context.tokens, allocations["semantic"])

    def test_an_empty_corpus_degrades_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as empty:
            index = index_path(empty)
            self.assertEqual(len(index), 0)
            context = pack_query("anything", index, budget=1_000)
            self.assertEqual(context.text, "")


if __name__ == "__main__":
    unittest.main()
