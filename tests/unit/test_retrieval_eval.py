"""Retrieval metrics, and the validity of the ablation dataset itself."""

import pytest

from evals.metrics import percentile, recall_at_k, reciprocal_rank
from evals.retrieval_ablation import load_cases
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.knowledge import KNOWLEDGE_DIR, build_corpus


class TestMetrics:
    def test_recall_counts_a_relevant_document_inside_the_top_k(self):
        assert recall_at_k(["a", "b", "c"], {"c"}, 3) == 1.0
        assert recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0

    def test_recall_needs_only_one_of_several_relevant_documents(self):
        assert recall_at_k(["x", "b"], {"a", "b"}, 3) == 1.0

    @pytest.mark.parametrize(
        ("ranked", "expected"),
        [(["a"], 1.0), (["x", "a"], 0.5), (["x", "y", "a"], 1 / 3), (["x"], 0.0)],
    )
    def test_reciprocal_rank(self, ranked, expected):
        assert reciprocal_rank(ranked, {"a"}) == pytest.approx(expected)

    def test_percentile_uses_nearest_rank(self):
        values = list(range(1, 101))
        assert percentile(values, 50) == 50
        assert percentile(values, 95) == 95
        assert percentile([], 95) == 0.0


@pytest.fixture(scope="module")
def cases():
    return load_cases()


class TestDataset:
    """A wrong expected id would silently count as a miss, so the dataset is checked like code."""

    def test_ids_are_unique(self, cases):
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids))

    def test_every_expected_document_exists_in_the_corpus(self, cases):
        corpus_ids = {
            d.id for d in build_corpus(build_catalog(read_jsonl(RAW_PATH)), KNOWLEDGE_DIR)
        }
        unknown = sorted({doc for c in cases for doc in c.relevant} - corpus_ids)
        assert unknown == []

    def test_unanswerable_questions_expect_nothing(self, cases):
        assert all(c.relevant == [] for c in cases if c.kind == "unanswerable")
        assert all(c.relevant for c in cases if c.kind != "unanswerable")

    def test_every_question_family_is_represented(self, cases):
        kinds = {c.kind for c in cases}
        assert {"allergen", "lexical", "keyword", "shop", "policy", "menu", "unanswerable"} <= kinds

    def test_forty_questions_with_eight_unanswerable(self, cases):
        # Each in-scope question weighs about 3 points of recall: small enough to read a trend.
        assert len(cases) == 40
        assert sum(c.unanswerable for c in cases) == 8
