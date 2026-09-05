import unittest

from context_ledger.context.chunking import chunk_source
from context_ledger.context.semantic import (
    HybridIndex,
    LexicalIndex,
    VectorIndex,
    build_index,
    cosine,
    reciprocal_rank_fusion,
    tokenize,
)

SOURCE = '''\
def refresh_access_token(client, retries=3):
    """Exchange the refresh token for a new access token."""
    for attempt in range(retries):
        try:
            return client.post("/oauth/token")
        except TimeoutError:
            continue
    raise RuntimeError("token refresh failed")

def render_invoice_pdf(invoice):
    """Draw an invoice onto a PDF page."""
    page = new_page()
    page.draw_text(invoice.customer_name)
    return page.render()

def compute_shipping_cost(weight_kg, zone):
    """Shipping price for a parcel."""
    base = 4.5
    return base + weight_kg * zone.multiplier
'''


class StubEmbedder:
    """A deterministic fake embedder: no model, no network, exact assertions.

    Vectors are term-count projections onto a fixed vocabulary, which is enough
    to exercise batching, cosine ranking and fusion without a real model.
    """

    VOCAB = ["token", "refresh", "pdf", "invoice", "shipping", "cost"]

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([float(lowered.count(term)) for term in self.VOCAB])
        return vectors


class TestTokenize(unittest.TestCase):
    def test_splits_camel_case_and_keeps_the_compound(self):
        terms = tokenize("getUserToken")
        self.assertIn("getusertoken", terms)
        self.assertIn("user", terms)
        self.assertIn("token", terms)

    def test_splits_snake_case(self):
        self.assertIn("refresh", tokenize("refresh_access_token"))

    def test_drops_stopwords_and_single_characters(self):
        terms = tokenize("the a x return value")
        self.assertNotIn("the", terms)
        self.assertNotIn("return", terms)
        self.assertNotIn("x", terms)
        self.assertIn("value", terms)


class TestLexicalIndex(unittest.TestCase):
    def setUp(self):
        self.chunks = chunk_source(SOURCE, path="svc.py")
        self.index = LexicalIndex().add(self.chunks)

    def test_empty_index_and_empty_query_return_nothing(self):
        self.assertEqual(LexicalIndex().search("anything"), [])
        self.assertEqual(self.index.search(""), [])

    def test_retrieves_the_right_symbol(self):
        top = self.index.search("refresh the access token", k=1)
        self.assertEqual(len(top), 1)
        self.assertIn("refresh_access_token", top[0].chunk.symbol)

    def test_ranks_by_relevance_not_position(self):
        hits = self.index.search("shipping parcel weight", k=3)
        self.assertIn("compute_shipping_cost", hits[0].chunk.symbol)

    def test_identifier_query_matches_across_naming_styles(self):
        hits = self.index.search("renderInvoicePdf", k=1)
        self.assertIn("render_invoice_pdf", hits[0].chunk.symbol)

    def test_k_bounds_the_result_set(self):
        self.assertLessEqual(len(self.index.search("token", k=2)), 2)

    def test_scores_are_positive_and_descending(self):
        hits = self.index.search("token refresh", k=3)
        self.assertTrue(all(h.score > 0 for h in hits))
        self.assertEqual([h.score for h in hits], sorted((h.score for h in hits), reverse=True))


class TestVectorIndex(unittest.TestCase):
    def test_cosine_bounds(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine([0, 0], [1, 1]), 0.0)  # no division by zero

    def test_embeds_in_batches_rather_than_per_chunk(self):
        embedder = StubEmbedder()
        chunks = chunk_source(SOURCE, path="svc.py")
        VectorIndex(embedder, batch_size=100).add(chunks)
        self.assertEqual(embedder.calls, 1)

    def test_retrieves_by_similarity(self):
        index = VectorIndex(StubEmbedder()).add(chunk_source(SOURCE, path="svc.py"))
        hits = index.search("invoice pdf", k=1)
        self.assertIn("render_invoice_pdf", hits[0].chunk.symbol)


class TestFusion(unittest.TestCase):
    def test_rrf_rewards_agreement_between_rankings(self):
        chunks = chunk_source(SOURCE, path="svc.py")
        lexical = LexicalIndex().add(chunks).search("token", k=5)
        vector = VectorIndex(StubEmbedder()).add(chunks).search("token", k=5)
        fused = reciprocal_rank_fusion([lexical, vector])
        self.assertTrue(fused)
        self.assertEqual([h.rank for h in fused], list(range(1, len(fused) + 1)))
        self.assertTrue(all(h.source == "hybrid" for h in fused))

    def test_fusion_deduplicates_chunks_seen_in_both_rankings(self):
        chunks = chunk_source(SOURCE, path="svc.py")
        ranking = LexicalIndex().add(chunks).search("token", k=5)
        fused = reciprocal_rank_fusion([ranking, ranking])
        self.assertEqual(len({h.chunk.id for h in fused}), len(fused))


class TestHybridIndex(unittest.TestCase):
    def test_without_an_embedder_it_is_purely_lexical(self):
        index = build_index(chunk_source(SOURCE, path="svc.py"))
        self.assertIsNone(index.vector)
        hits = index.search("refresh token", k=2)
        self.assertTrue(hits)
        self.assertTrue(all(h.source == "lexical" for h in hits))

    def test_with_an_embedder_results_are_fused(self):
        index = HybridIndex(StubEmbedder()).add(chunk_source(SOURCE, path="svc.py"))
        hits = index.search("refresh token", k=2)
        self.assertTrue(all(h.source == "hybrid" for h in hits))
        self.assertLessEqual(len(hits), 2)

    def test_length_reflects_indexed_chunks(self):
        chunks = chunk_source(SOURCE, path="svc.py")
        self.assertEqual(len(build_index(chunks)), len(chunks))


if __name__ == "__main__":
    unittest.main()
