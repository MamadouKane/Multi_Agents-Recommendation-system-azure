"""Retrieval logic, with a fake search backend: no network, deterministic."""

import pytest

from src.api.core.search import (
    VECTOR_CANDIDATES,
    Retriever,
    Strategy,
    build_filter,
    build_search_kwargs,
    odata_literal,
    to_hit,
)


def result(doc_id, score=0.5, reranker=None, **extra):
    return {
        "id": doc_id,
        "doc_type": "product",
        "title": doc_id,
        "content": f"content of {doc_id}",
        "@search.score": score,
        "@search.reranker_score": reranker,
        **extra,
    }


class FakeBackend:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, search_text, **kwargs):
        self.calls.append((search_text, kwargs))
        return iter(self.results)


def fake_embed(text):
    return [0.1] * 1536


class TestFilter:
    def test_no_filter(self):
        assert build_filter() is None

    def test_doc_types_use_search_in(self):
        assert build_filter(["product", "faq"]) == "search.in(doc_type, 'product,faq', ',')"

    def test_category_and_doc_type_are_combined(self):
        assert build_filter(["menu"], "Bakery") == (
            "search.in(doc_type, 'menu', ',') and category eq 'Bakery'"
        )

    def test_quotes_are_escaped(self):
        assert odata_literal("Merry's") == "'Merry''s'"

    def test_injection_through_the_category_is_rejected(self):
        # A category comes from an agent, hence indirectly from a user message.
        with pytest.raises(ValueError, match="unknown category"):
            build_filter(category="Bakery' or 1 eq 1 or category eq '")

    def test_unknown_doc_type_is_rejected(self):
        with pytest.raises(ValueError, match="unknown doc_type"):
            build_filter(["secrets"])


class TestSearchKwargs:
    def test_vector_sends_no_text_and_asks_only_top_k_neighbours(self):
        text, kwargs = build_search_kwargs(Strategy.VECTOR, "latte", [0.0] * 1536, 5, None)
        assert text is None
        assert kwargs["vector_queries"][0].k_nearest_neighbors == 5
        assert "query_type" not in kwargs

    def test_hybrid_sends_text_and_more_vector_candidates_for_the_fusion(self):
        text, kwargs = build_search_kwargs(Strategy.HYBRID, "latte", [0.0] * 1536, 5, None)
        assert text == "latte"
        assert kwargs["vector_queries"][0].k_nearest_neighbors == VECTOR_CANDIDATES
        assert kwargs["top"] == 5
        assert "query_type" not in kwargs

    def test_semantic_adds_the_reranker(self):
        text, kwargs = build_search_kwargs(
            Strategy.HYBRID_SEMANTIC, "latte", [0.0] * 1536, 5, "category eq 'Coffee'"
        )
        assert text == "latte"
        assert kwargs["query_type"] == "semantic"
        assert kwargs["semantic_configuration_name"] == "default"
        assert kwargs["filter"] == "category eq 'Coffee'"

    def test_vector_semantic_reranks_without_any_keyword_search(self):
        text, kwargs = build_search_kwargs(Strategy.VECTOR_SEMANTIC, "latte", [0.0] * 1536, 5, None)
        assert text is None  # no search text means no BM25
        assert kwargs["semantic_query"] == "latte"  # the reranker still reads the question
        assert kwargs["query_type"] == "semantic"
        assert kwargs["vector_queries"][0].k_nearest_neighbors == VECTOR_CANDIDATES

    def test_only_the_semantic_strategies_are_reranked(self):
        assert {s for s in Strategy if s.reranked} == {
            Strategy.HYBRID_SEMANTIC,
            Strategy.VECTOR_SEMANTIC,
            Strategy.VECTOR_GATED,
        }

    def test_the_vector_is_never_returned(self):
        _, kwargs = build_search_kwargs(Strategy.HYBRID, "latte", [0.0] * 1536, 5, None)
        assert "content_vector" not in kwargs["select"]


def test_to_hit_reads_scores_and_optional_fields():
    hit = to_hit(result("product-latte", score=0.03, reranker=3.2, product_id="latte"))
    assert hit.score == 0.03 and hit.reranker_score == 3.2 and hit.product_id == "latte"


class TestThreshold:
    def test_the_threshold_also_applies_to_vector_semantic(self):
        backend = FakeBackend([result("a", reranker=2.0), result("b", reranker=0.2)])
        retrieval = Retriever(backend, fake_embed, min_reranker_score=1.5).search(
            "question", Strategy.VECTOR_SEMANTIC
        )
        assert retrieval.ids == ["a"]

    def test_semantic_drops_hits_below_the_threshold(self):
        backend = FakeBackend([result("a", reranker=3.1), result("b", reranker=0.8)])
        retrieval = Retriever(backend, fake_embed, min_reranker_score=1.5).search(
            "question", Strategy.HYBRID_SEMANTIC
        )
        assert retrieval.ids == ["a"]
        assert retrieval.below_threshold == 1

    def test_nothing_relevant_is_an_explicit_state(self):
        backend = FakeBackend([result("a", reranker=0.4)])
        retrieval = Retriever(backend, fake_embed).search(
            "what is the weather", Strategy.HYBRID_SEMANTIC
        )
        assert retrieval.found_nothing_relevant

    def test_other_strategies_have_no_threshold(self):
        # RRF and vector scores are relative to the query, so a fixed cut-off would be meaningless.
        backend = FakeBackend([result("a", score=0.01), result("b", score=0.009)])
        retrieval = Retriever(backend, fake_embed).search("question", Strategy.HYBRID)
        assert retrieval.ids == ["a", "b"]


def test_empty_query_is_rejected():
    with pytest.raises(ValueError):
        Retriever(FakeBackend([]), fake_embed).search("   ")


class TestVectorGated:
    """ADR-008: vector order, reranker gate, one request."""

    def test_is_one_semantic_query_without_keywords_over_every_candidate(self):
        text, kwargs = build_search_kwargs(Strategy.VECTOR_GATED, "latte", [0.0] * 1536, 3, None)
        assert text is None
        assert kwargs["semantic_query"] == "latte"
        assert kwargs["top"] == VECTOR_CANDIDATES  # re-sorted locally, so all candidates come back

    def test_keeps_the_vector_order_not_the_reranker_order(self):
        backend = FakeBackend(
            [result("b", score=0.60, reranker=3.1), result("a", score=0.80, reranker=2.0)]
        )
        retrieval = Retriever(backend, fake_embed).search("q", Strategy.VECTOR_GATED, top_k=3)
        assert retrieval.ids == ["a", "b"]
        assert retrieval.best_reranker_score == 3.1

    def test_one_relevant_document_opens_the_gate_for_the_top_k(self):
        # Below the threshold on its own, "c" is still kept: the gate is all or nothing.
        backend = FakeBackend(
            [
                result("a", 0.8, 1.9),
                result("b", 0.7, 0.4),
                result("c", 0.6, 0.3),
                result("d", 0.5, 0.2),
            ]
        )
        retrieval = Retriever(backend, fake_embed).search("q", Strategy.VECTOR_GATED, top_k=3)
        assert retrieval.ids == ["a", "b", "c"]

    def test_abstains_when_nothing_reaches_the_threshold(self):
        backend = FakeBackend([result("a", 0.8, 1.68), result("b", 0.7, 1.2)])
        retrieval = Retriever(backend, fake_embed).search("parking?", Strategy.VECTOR_GATED)
        assert retrieval.found_nothing_relevant
        assert retrieval.below_threshold == 2
