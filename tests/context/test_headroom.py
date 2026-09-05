import unittest

from tollgate.context.headroom import Headroom, Lane, policy_for


class TestAccounting(unittest.TestCase):
    def test_reservations_come_off_the_top(self):
        headroom = Headroom(window=100_000, reserve_output=10_000, reserve_system=5_000, safety_margin=0.0)
        self.assertEqual(headroom.effective, 85_000)
        self.assertEqual(headroom.available, 85_000)

    def test_safety_margin_shrinks_the_spendable_pool(self):
        strict = Headroom(window=100_000, reserve_output=0, safety_margin=0.10)
        self.assertEqual(strict.effective, 90_000)

    def test_spending_accumulates_and_releases(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("docs", 1_000)
        headroom.spend("docs", 500)
        self.assertEqual(headroom.used, 1_500)
        self.assertEqual(headroom.release("docs"), 1_500)
        self.assertEqual(headroom.used, 0)

    def test_spend_text_charges_the_estimate(self):
        headroom = Headroom(window=100_000, reserve_output=0)
        booked = headroom.spend_text("body", "hello world " * 100)
        self.assertGreater(booked, 0)
        self.assertEqual(headroom.used, booked)

    def test_pressure_bands_escalate(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        self.assertEqual(headroom.pressure, "cool")
        headroom.spend("a", 60_000)
        self.assertEqual(headroom.pressure, "warm")
        headroom.spend("b", 20_000)
        self.assertEqual(headroom.pressure, "hot")
        headroom.spend("c", 15_000)
        self.assertEqual(headroom.pressure, "critical")

    def test_available_never_goes_negative(self):
        headroom = Headroom(window=1_000, reserve_output=0)
        headroom.spend("overrun", 50_000)
        self.assertEqual(headroom.available, 0)
        self.assertEqual(headroom.ratio, 1.0)
        self.assertFalse(headroom.fits(1))

    def test_policy_tightens_with_pressure(self):
        self.assertFalse(policy_for("cool")["elide_bodies"])
        self.assertTrue(policy_for("critical")["elide_bodies"])
        self.assertLess(policy_for("critical")["semantic_k"], policy_for("cool")["semantic_k"])
        # An unknown band must fail safe, not fail open.
        self.assertEqual(policy_for("nonsense"), policy_for("critical"))

    def test_invalid_construction_is_rejected(self):
        with self.assertRaises(ValueError):
            Headroom(window=0)
        with self.assertRaises(ValueError):
            Headroom(window=100, safety_margin=1.5)
        with self.assertRaises(ValueError):
            Headroom(window=100).spend("bad", -1)


class TestAllocation(unittest.TestCase):
    def setUp(self):
        self.headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        self.headroom.spend("conversation", 90_000)  # 10,000 free

    def test_allocation_is_exact_and_never_overspends(self):
        plan = self.headroom.plan([Lane("a", 1), Lane("b", 3)])
        self.assertEqual(sum(a.tokens for a in plan), 10_000)

    def test_weights_set_the_shares(self):
        plan = {a.lane: a.tokens for a in self.headroom.plan([Lane("a", 1), Lane("b", 3)])}
        self.assertEqual(plan["b"], 3 * plan["a"])

    def test_ceilings_hold_and_surplus_is_redistributed(self):
        plan = {a.lane: a.tokens for a in self.headroom.plan([Lane("capped", 3, maximum=1_000), Lane("open", 1)])}
        self.assertEqual(plan["capped"], 1_000)
        self.assertEqual(plan["open"], 9_000)  # the whole surplus moved, none was lost

    def test_floors_are_honoured(self):
        plan = {a.lane: a.tokens for a in self.headroom.plan([Lane("tiny", 0.01, minimum=2_000), Lane("big", 100)])}
        self.assertGreaterEqual(plan["tiny"], 2_000)
        self.assertEqual(sum(plan.values()), 10_000)

    def test_lowest_priority_lanes_are_dropped_when_floors_cannot_all_be_paid(self):
        plan = {
            a.lane: a for a in self.headroom.plan(
                [Lane("critical", 1, minimum=8_000, priority=10), Lane("optional", 1, minimum=8_000, priority=1)]
            )
        }
        # The surviving lane gets its floor and then absorbs the rest of the pool,
        # because it is the only lane left to apportion to.
        self.assertGreaterEqual(plan["critical"].tokens, 8_000)
        self.assertEqual(plan["optional"].clamped, "dropped")
        self.assertEqual(plan["optional"].tokens, 0)
        self.assertEqual(plan["critical"].tokens + plan["optional"].tokens, 10_000)

    def test_all_lanes_capped_leaves_the_remainder_unspent_rather_than_looping(self):
        plan = self.headroom.plan([Lane("a", 1, maximum=100), Lane("b", 1, maximum=100)])
        self.assertEqual(sum(a.tokens for a in plan), 200)

    def test_zero_budget_and_no_lanes_are_handled(self):
        self.assertEqual(self.headroom.plan([]), [])
        self.assertEqual(sum(a.tokens for a in self.headroom.plan([Lane("a", 1)], budget=0)), 0)

    def test_snapshot_and_render_report_the_state(self):
        snapshot = self.headroom.snapshot()
        self.assertEqual(snapshot["used"], 90_000)
        self.assertEqual(snapshot["pressure"], "critical")
        self.assertIn("conversation", snapshot["lanes"])
        self.assertIn("CRITICAL", self.headroom.render())


if __name__ == "__main__":
    unittest.main()
