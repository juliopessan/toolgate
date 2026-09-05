import os
import tempfile
import unittest

from tollgate.context.chunking import Chunk
from tollgate.context.headroom import Headroom
from tollgate.context.pack import (
    index_path,
    iter_source_files,
    pack_files,
    pack_hits,
    pack_query,
    plan_lanes,
)
from tollgate.context.semantic import Hit
from tollgate.context.tokens import estimate_tokens


def make_hit(chunk_id, tokens, score, path="a.py", symbol=None, start=1):
    text = "\n".join(f"body line {i}" for i in range(max(1, tokens // 5)))
    return Hit(
        chunk=Chunk(
            id=chunk_id,
            text=text,
            path=path,
            start=start,
            end=start + 10,
            tokens=estimate_tokens(text),
            symbol=symbol,
        ),
        score=score,
    )


class TestPackHits(unittest.TestCase):
    def test_no_hits_packs_to_nothing(self):
        context = pack_hits([], budget=1000)
        self.assertEqual(context.text, "")
        self.assertEqual(context.sections, [])

    def test_the_assembled_text_never_exceeds_the_budget(self):
        hits = [make_hit(f"c{i}", 200, score=10 - i, start=i * 20 + 1) for i in range(30)]
        for budget in (100, 500, 2_000, 10_000):
            with self.subTest(budget=budget):
                context = pack_hits(hits, budget=budget)
                self.assertLessEqual(context.tokens, budget)

    def test_section_framing_is_charged_not_discovered_later(self):
        # Many small sections make the `--- citation ---` headers material.
        hits = [make_hit(f"c{i}", 30, score=5, start=i * 20 + 1) for i in range(40)]
        context = pack_hits(hits, budget=800)
        self.assertLessEqual(context.tokens, 800)

    def test_selection_prefers_value_per_token(self):
        # Lower absolute score but far cheaper: at a tight budget it is the
        # better buy, and a score-ordered packer would get this wrong.
        cheap = make_hit("cheap", 50, score=4.0, start=1)
        expensive = make_hit("expensive", 2000, score=9.0, start=500)
        context = pack_hits([expensive, cheap], budget=300)
        selected = {s.citation for s in context.sections}
        self.assertEqual(len(selected), 1)
        self.assertTrue(context.sections[0].tokens < 200)
        self.assertEqual(context.dropped[0]["reason"], "over budget")

    def test_a_chunk_already_covered_by_a_selection_is_dropped(self):
        wide = make_hit("wide", 100, score=9.0, start=1)
        wide.chunk.end = 100
        inner = make_hit("inner", 400, score=8.0, start=10)
        inner.chunk.end = 60  # entirely inside `wide`, and dearer per token
        context = pack_hits([wide, inner], budget=5_000)
        self.assertEqual([s.citation for s in context.sections], [wide.chunk.citation()])
        self.assertIn("already covered", context.dropped[0]["reason"])

    def test_a_partial_overlap_below_the_threshold_is_kept(self):
        first = make_hit("first", 100, score=9.0, start=1)
        first.chunk.end = 100
        neighbour = make_hit("neighbour", 100, score=8.0, start=90)
        neighbour.chunk.end = 200  # ~10% overlap: the rest is new context
        context = pack_hits([first, neighbour], budget=5_000)
        self.assertEqual(len(context.sections), 2)

    def test_everything_emitted_is_cited(self):
        hits = [make_hit(f"c{i}", 100, score=5, symbol=f"sym{i}", start=i * 20 + 1) for i in range(5)]
        context = pack_hits(hits, budget=5_000)
        for section in context.sections:
            self.assertIn(":", section.citation)
            self.assertIn(f"--- {section.citation} ---", context.text)

    def test_sections_are_emitted_in_reading_order(self):
        hits = [make_hit(f"c{i}", 100, score=i, start=i * 20 + 1) for i in range(5)]
        context = pack_hits(hits, budget=10_000)
        citations = [s.citation for s in context.sections]
        self.assertEqual(citations, sorted(citations))

    def test_under_pressure_oversized_hits_degrade_to_stubs_not_silence(self):
        headroom = Headroom(window=10_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("session", 9_500)  # critical
        big = make_hit("big", 5_000, score=9.0, symbol="BigThing")
        small = make_hit("small", 40, score=8.0, symbol="Small", start=500)
        context = pack_hits([small, big], budget=300, headroom=headroom)
        self.assertEqual(context.pressure, "critical")
        self.assertIn("stub", [s.kind for s in context.sections])

    def test_manifest_accounts_for_both_kept_and_dropped(self):
        hits = [make_hit(f"c{i}", 400, score=9 - i, start=i * 20 + 1) for i in range(10)]
        context = pack_hits(hits, budget=600)
        manifest = context.manifest()
        self.assertIn("packed", manifest)
        self.assertTrue(context.dropped)
        self.assertIn("dropped", manifest)


class TestPackOverACorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "node_modules", "junk"), exist_ok=True)
        with open(os.path.join(root, "node_modules", "junk", "vendor.py"), "w") as handle:
            handle.write("def vendored_retry_backoff():\n    return 1\n")
        with open(os.path.join(root, "retry.py"), "w") as handle:
            handle.write(
                'def retry_with_backoff(attempts=3):\n'
                '    """Retry with exponential backoff."""\n'
                '    for i in range(attempts):\n'
                '        sleep(2 ** i)\n'
            )
        with open(os.path.join(root, "unrelated.py"), "w") as handle:
            handle.write('def render_report(rows):\n    """Draw a table."""\n    return rows\n')
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_vendored_directories_are_excluded_from_the_corpus(self):
        files = iter_source_files(self.root)
        self.assertTrue(any(f.endswith("retry.py") for f in files))
        self.assertFalse(any("node_modules" in f for f in files))

    def test_index_and_query_finds_the_right_file(self):
        index = index_path(self.root)
        self.assertGreater(len(index), 0)
        context = pack_query("exponential backoff on retry", index, budget=2_000)
        self.assertIn("retry.py", context.text)

    def test_pack_query_respects_its_budget(self):
        index = index_path(self.root)
        context = pack_query("retry", index, budget=120)
        self.assertLessEqual(context.tokens, 120)

    def _write_big_file(self):
        big = os.path.join(self.root, "big.py")
        with open(big, "w") as handle:
            for i in range(60):
                handle.write(f"def generated_{i}(x):\n" + "".join(f"    v{j} = {j}\n" for j in range(20)))
        return big

    def test_pack_files_degrades_to_a_skeleton_when_the_file_will_not_fit(self):
        big = self._write_big_file()
        with open(big) as handle:
            full = estimate_tokens(handle.read())
        context = pack_files([big], budget=full // 2, root=self.root)
        self.assertTrue(context.sections)
        self.assertEqual(context.sections[0].kind, "skeleton")
        self.assertLessEqual(context.tokens, full // 2)

    def test_pack_files_drops_and_says_so_when_even_the_skeleton_will_not_fit(self):
        big = self._write_big_file()
        context = pack_files([big], budget=50, root=self.root)
        self.assertEqual(context.sections, [])
        self.assertIn("over budget even as skeleton", context.dropped[0]["reason"])

    def test_pack_files_includes_whole_files_when_they_fit(self):
        context = pack_files([os.path.join(self.root, "retry.py")], budget=5_000, root=self.root)
        self.assertEqual(context.sections[0].kind, "file")

    def test_unreadable_paths_are_skipped_not_fatal(self):
        context = pack_files([os.path.join(self.root, "missing.py")], budget=1_000)
        self.assertEqual(context.sections, [])


class TestLanePlanning(unittest.TestCase):
    def test_default_lanes_split_the_available_budget(self):
        headroom = Headroom(window=100_000, reserve_output=0, safety_margin=0.0)
        headroom.spend("conversation", 80_000)
        plan = plan_lanes(headroom)
        self.assertEqual(set(plan), {"structure", "semantic", "diagnostics"})
        self.assertLessEqual(sum(plan.values()), headroom.available)


if __name__ == "__main__":
    unittest.main()
