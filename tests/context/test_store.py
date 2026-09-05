import os
import sqlite3
import tempfile
import unittest

from tollgate.context.chunking import chunk_source
from tollgate.context.pack import index_path, iter_source_files, load_chunks, open_store
from tollgate.context.store import Store, default_store_path, fingerprint

SOURCE = '''\
"""Payments."""

RETRIES = 3

class Gateway:
    """Talks to the provider."""

    def charge(self, amount):
        """Charge a card."""
        for attempt in range(RETRIES):
            try:
                return self._post(amount)
            except TimeoutError:
                continue
        raise RuntimeError("failed")

def format_receipt(tx):
    """Render a receipt."""
    return tx.id
'''


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        self.file = os.path.join(self.root, "src", "payments.py")
        with open(self.file, "w") as handle:
            handle.write(SOURCE)
        with open(os.path.join(self.root, "src", "other.py"), "w") as handle:
            handle.write("def unrelated():\n    return 1\n")
        self.store = open_store(self.root)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class TestFingerprint(unittest.TestCase):
    def test_same_content_same_fingerprint(self):
        self.assertEqual(fingerprint("abc"), fingerprint("abc"))

    def test_changed_content_changes_the_fingerprint(self):
        self.assertNotEqual(fingerprint("abc"), fingerprint("abd"))

    def test_whitespace_only_change_is_still_a_change(self):
        self.assertNotEqual(fingerprint("a\nb"), fingerprint("a\n b"))


class TestLifecycle(StoreTestBase):
    def test_the_store_opens_inside_the_workspace(self):
        self.assertTrue(self.store.available)
        self.assertEqual(self.store.path, default_store_path(self.root))
        self.assertTrue(os.path.exists(self.store.path))

    def test_wal_is_enabled_for_concurrent_readers(self):
        mode = sqlite3.connect(self.store.path).execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_disabling_it_is_a_supported_state_not_an_error(self):
        store = open_store(self.root, enabled=False)
        self.assertFalse(store.available)
        self.assertIn("disabled", store.unavailable_reason)

    def test_an_unopenable_path_degrades_instead_of_raising(self):
        # A path that cannot be a directory: the store must decline, not explode.
        store = Store(root=self.root, path=os.path.join(self.file, "nested", "index.db"))
        self.assertFalse(store.available)
        self.assertIsNotNone(store.unavailable_reason)

    def test_every_method_is_safe_on_an_unavailable_store(self):
        store = open_store(self.root, enabled=False)
        self.assertIsNone(store.cached_chunks("a.py", "fp"))
        self.assertFalse(store.put_chunks("a.py", "fp", "python", []))
        self.assertFalse(store.record("suggested", path="a.py"))
        self.assertEqual(store.realized_savings(), [])
        self.assertEqual(store.forget_missing(["a.py"]), 0)
        self.assertFalse(store.purge())
        self.assertFalse(store.summary()["available"])

    def test_it_can_be_used_as_a_context_manager(self):
        with open_store(self.root) as store:
            self.assertTrue(store.available)


class TestIndexCache(StoreTestBase):
    def test_a_roundtrip_returns_chunks_faithfully(self):
        chunks = chunk_source(SOURCE, path="src/payments.py")
        mark = fingerprint(SOURCE)
        self.assertTrue(self.store.put_chunks("src/payments.py", mark, "python", chunks))

        cached = self.store.cached_chunks("src/payments.py", mark)
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), len(chunks))
        for original, restored in zip(chunks, cached):
            self.assertEqual(restored.id, original.id)
            self.assertEqual(restored.text, original.text)
            self.assertEqual((restored.start, restored.end), (original.start, original.end))
            self.assertEqual(restored.symbol, original.symbol)
            self.assertEqual(restored.kind, original.kind)
            self.assertEqual(restored.tokens, original.tokens)
            self.assertEqual(restored.meta, original.meta)

    def test_a_changed_file_misses_the_cache(self):
        chunks = chunk_source(SOURCE, path="src/payments.py")
        self.store.put_chunks("src/payments.py", fingerprint(SOURCE), "python", chunks)
        # Invalidation is by content, never by time: a stale hit would hand back
        # line ranges that no longer exist.
        self.assertIsNone(self.store.cached_chunks("src/payments.py", fingerprint(SOURCE + "\n# edit")))

    def test_an_unknown_file_misses_the_cache(self):
        self.assertIsNone(self.store.cached_chunks("never/seen.py", "fp"))

    def test_reindexing_replaces_rather_than_accumulates(self):
        path = "src/payments.py"
        self.store.put_chunks(path, "fp1", "python", chunk_source(SOURCE, path=path))
        smaller = chunk_source("def only():\n    return 1\n", path=path)
        self.store.put_chunks(path, "fp2", "python", smaller)
        self.assertEqual(len(self.store.cached_chunks(path, "fp2")), len(smaller))

    def test_deleted_files_are_dropped_from_the_cache(self):
        self.store.put_chunks("gone.py", "fp", "python", chunk_source(SOURCE, path="gone.py"))
        self.store.put_chunks("kept.py", "fp", "python", chunk_source(SOURCE, path="kept.py"))
        self.assertEqual(self.store.forget_missing(["kept.py"]), 1)
        self.assertIsNone(self.store.cached_chunks("gone.py", "fp"))
        self.assertIsNotNone(self.store.cached_chunks("kept.py", "fp"))

    def test_a_cached_load_matches_an_uncached_load_exactly(self):
        files = iter_source_files(self.root)
        fresh = load_chunks(files, self.root)
        load_chunks(files, self.root, store=self.store)          # warm
        cached = load_chunks(files, self.root, store=self.store)  # served from cache

        self.assertEqual(len(cached), len(fresh))
        self.assertEqual({c.id for c in cached}, {c.id for c in fresh})
        by_id = {c.id: c for c in fresh}
        for chunk in cached:
            self.assertEqual(chunk.text, by_id[chunk.id].text)
            self.assertEqual(chunk.start, by_id[chunk.id].start)

    def test_an_edited_file_is_re_read_not_served_stale(self):
        files = iter_source_files(self.root)
        load_chunks(files, self.root, store=self.store)
        with open(self.file, "a") as handle:
            handle.write("\ndef added_later():\n    return 2\n")
        chunks = load_chunks(iter_source_files(self.root), self.root, store=self.store)
        self.assertIn("added_later", {c.symbol for c in chunks})

    def test_index_path_produces_the_same_index_with_and_without_a_store(self):
        warm = index_path(self.root, store=self.store)
        warm = index_path(self.root, store=self.store)
        cold = index_path(self.root)
        self.assertEqual(len(warm), len(cold))
        self.assertEqual(
            [h.chunk.id for h in warm.search("charge a card", k=3)],
            [h.chunk.id for h in cold.search("charge a card", k=3)],
        )


class TestLedger(StoreTestBase):
    def test_an_offer_alone_is_not_a_saving(self):
        self.store.record("suggested", tool="Read", path="a.py", tokens_full=1000, tokens_served=100)
        rows = {r.path: r for r in self.store.realized_savings()}
        self.assertEqual(rows["a.py"].offers, 1)
        self.assertEqual(rows["a.py"].accepted, 0)
        self.assertEqual(rows["a.py"].tokens_saved, 0)

    def test_an_accepted_offer_becomes_a_realised_saving(self):
        self.store.record("suggested", path="a.py", tokens_full=1000, tokens_served=100)
        self.store.record("accepted", path="a.py", tokens_full=1000, tokens_served=100)
        rows = {r.path: r for r in self.store.realized_savings()}
        self.assertEqual(rows["a.py"].tokens_saved, 900)
        self.assertEqual(rows["a.py"].to_dict()["acceptance_rate"], 1.0)

    def test_savings_rank_by_size(self):
        self.store.record("accepted", path="small.py", tokens_full=200, tokens_served=100)
        self.store.record("accepted", path="big.py", tokens_full=9000, tokens_served=500)
        self.assertEqual(self.store.realized_savings()[0].path, "big.py")

    def test_summary_aggregates_the_ledger(self):
        self.store.record("suggested", path="a.py", tokens_full=1000, tokens_served=100)
        self.store.record("accepted", path="a.py", tokens_full=1000, tokens_served=100)
        self.store.record("suggested", path="b.py", tokens_full=500, tokens_served=50)
        summary = self.store.summary()
        self.assertEqual(summary["offers"], 2)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["tokens_saved"], 900)
        self.assertEqual(summary["acceptance_rate"], 0.5)


class TestPrivacy(StoreTestBase):
    def test_purge_erases_everything(self):
        self.store.put_chunks("a.py", "fp", "python", chunk_source(SOURCE, path="a.py"))
        self.store.record("accepted", path="a.py", tokens_full=100, tokens_served=10)
        self.assertTrue(self.store.purge())
        summary = self.store.summary()
        self.assertEqual(summary["files_cached"], 0)
        self.assertEqual(summary["chunks_cached"], 0)
        self.assertEqual(summary["events"], 0)

    def test_cache_only_purge_keeps_the_ledger(self):
        self.store.put_chunks("a.py", "fp", "python", chunk_source(SOURCE, path="a.py"))
        self.store.record("accepted", path="a.py", tokens_full=100, tokens_served=10)
        self.store.purge(cache_only=True)
        summary = self.store.summary()
        self.assertEqual(summary["chunks_cached"], 0)
        self.assertEqual(summary["events"], 1)

    def test_the_store_lives_inside_the_workspace(self):
        self.assertTrue(os.path.realpath(self.store.path).startswith(os.path.realpath(self.root)))


class TestSchemaVersioning(StoreTestBase):
    def test_a_stale_schema_drops_the_cache_but_keeps_the_ledger(self):
        self.store.put_chunks("a.py", "fp", "python", chunk_source(SOURCE, path="a.py"))
        self.store.record("accepted", path="a.py", tokens_full=100, tokens_served=10)
        self.store.close()

        conn = sqlite3.connect(default_store_path(self.root))
        conn.execute("PRAGMA user_version = 999")
        conn.commit()
        conn.close()

        reopened = open_store(self.root)
        summary = reopened.summary()
        self.assertEqual(summary["chunks_cached"], 0, "derived cache should be rebuilt")
        self.assertEqual(summary["events"], 1, "the ledger is not derivable and must survive")
        reopened.close()


if __name__ == "__main__":
    unittest.main()


class TestPersistedSearch(StoreTestBase):
    """SQLite's compiled BM25, ranking over our own code-aware token stream."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.root, "src", "auth.py"), "w") as handle:
            handle.write(
                "def refreshAccessToken(client):\n"
                '    """Exchange the refresh token."""\n'
                "    return client.post('/oauth/token')\n"
            )
        from tollgate.context.pack import iter_source_files, sync_index

        sync_index(iter_source_files(self.root), self.root, self.store)

    def test_it_ranks_and_returns_chunks(self):
        results = self.store.search("charge a card", k=3)
        self.assertTrue(results)
        chunk, score = results[0]
        self.assertIn("Gateway", chunk.symbol or "")
        self.assertGreater(score, 0, "scores must be higher-is-better")

    def test_scores_descend(self):
        scores = [s for _, s in self.store.search("charge card provider", k=5)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_camel_case_survives_the_round_trip(self):
        # The whole reason for pre-tokenising: FTS5's own tokenisers do not split
        # identifiers, and this query has no literal overlap with the source.
        results = self.store.search("refresh token", k=3)
        self.assertTrue(results)
        self.assertIn("auth.py", results[0][0].path)

    def test_an_empty_or_stopword_query_returns_nothing(self):
        self.assertEqual(self.store.search(""), [])
        self.assertEqual(self.store.search("the a of"), [])

    def test_fts5_operators_in_a_query_are_matched_literally(self):
        # "not" and "and" are FTS5 syntax; unquoted they would change the query.
        self.assertIsInstance(self.store.search("not and or charge", k=3), list)

    def test_reindexing_a_file_does_not_leave_stale_rows(self):
        from tollgate.context.pack import iter_source_files, sync_index

        before = self.store.indexed_chunk_count()
        with open(self.file, "w") as handle:
            handle.write("def only_one():\n    return 1\n")
        sync_index(iter_source_files(self.root), self.root, self.store)
        self.assertLess(self.store.indexed_chunk_count(), before)
        self.assertEqual(self.store.search("charge a card", k=3), [])

    def test_deleting_a_file_removes_it_from_search(self):
        from tollgate.context.pack import iter_source_files, sync_index

        os.remove(os.path.join(self.root, "src", "auth.py"))
        sync_index(iter_source_files(self.root), self.root, self.store)
        self.assertFalse(any("auth.py" in c.path for c, _ in self.store.search("refresh token", k=5)))


class TestStoreIndexParity(StoreTestBase):
    def test_the_persisted_index_answers_like_the_in_memory_one(self):
        from tollgate.context.pack import index_path

        memory = index_path(self.root)
        persisted = index_path(self.root, store=self.store)
        self.assertEqual(len(persisted), len(memory))
        self.assertEqual(
            persisted.search("charge a card", k=1)[0].chunk.id,
            memory.search("charge a card", k=1)[0].chunk.id,
        )

    def test_a_warm_run_reparses_nothing(self):
        from tollgate.context.pack import iter_source_files, sync_index

        files = iter_source_files(self.root)
        self.assertGreater(sync_index(files, self.root, self.store), 0)
        self.assertEqual(sync_index(files, self.root, self.store), 0, "unchanged tree must be a no-op")

    def test_an_edited_file_is_the_only_thing_reparsed(self):
        from tollgate.context.pack import iter_source_files, sync_index

        files = iter_source_files(self.root)
        sync_index(files, self.root, self.store)
        with open(self.file, "a") as handle:
            handle.write("\ndef added():\n    return 1\n")
        self.assertEqual(sync_index(files, self.root, self.store), 1)


class TestOfferClaiming(StoreTestBase):
    """An offer becomes a saving when the narrow read actually happens."""

    def test_an_offer_is_claimed_by_the_read_that_follows_it(self):
        self.store.record("suggested", path="a.py", tokens_full=5000, tokens_served=600)
        self.assertEqual(self.store.claim_offer("a.py", 600), 4400)
        summary = self.store.summary()
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["tokens_saved"], 4400)

    def test_the_same_offer_cannot_be_claimed_twice(self):
        self.store.record("suggested", path="a.py", tokens_full=5000, tokens_served=600)
        self.assertIsNotNone(self.store.claim_offer("a.py", 600))
        self.assertIsNone(self.store.claim_offer("a.py", 600), "double-crediting inflates the ledger")

    def test_a_read_with_no_standing_offer_claims_nothing(self):
        self.assertIsNone(self.store.claim_offer("never-offered.py", 100))
        self.assertEqual(self.store.summary()["accepted"], 0)

    def test_an_expired_offer_is_not_claimable(self):
        # A read an hour after the offer is a coincidence, not an acceptance.
        # Backdated directly, because the ledger stores whole seconds and a
        # zero-second window would be testing sub-second behaviour it cannot express.
        import time as _time

        self.store.record("suggested", path="a.py", tokens_full=5000, tokens_served=600)
        self.store._conn.execute(
            "UPDATE events SET ts = ? WHERE path = 'a.py'", (int(_time.time()) - 3600,)
        )
        self.store._conn.commit()

        self.assertIsNone(self.store.claim_offer("a.py", 600, window_seconds=900))
        self.assertEqual(self.store.summary()["accepted"], 0)
        # Still visible as a standing offer only inside its own window.
        self.assertEqual(self.store.pending_offers(window_seconds=900), [])
        self.assertEqual(self.store.pending_offers(window_seconds=7200), ["a.py"])

    def test_pending_offers_are_reported_until_claimed(self):
        self.store.record("suggested", path="a.py", tokens_full=5000, tokens_served=600)
        self.assertEqual(self.store.pending_offers(), ["a.py"])
        self.store.claim_offer("a.py", 600)
        self.assertEqual(self.store.pending_offers(), [])

    def test_claiming_is_safe_without_a_store(self):
        store = open_store(self.root, enabled=False)
        self.assertIsNone(store.claim_offer("a.py", 100))
        self.assertEqual(store.pending_offers(), [])
