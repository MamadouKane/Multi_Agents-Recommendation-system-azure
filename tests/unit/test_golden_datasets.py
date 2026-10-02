"""The golden datasets are checked like code: a wrong expected answer is worse than none."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

from src.api.core.catalog import Catalog
from src.api.core.pricing import price_order
from src.api.core.schemas import AgentName, ChatMessage, OrderLineRequest
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.knowledge import KNOWLEDGE_DIR, build_corpus

DATASETS = Path("evals/datasets")


def load(name):
    return [json.loads(line) for line in (DATASETS / f"{name}.jsonl").open(encoding="utf-8")]


@pytest.fixture(scope="module")
def products():
    return build_catalog(read_jsonl(RAW_PATH))


@pytest.fixture(scope="module")
def catalog(products):
    return Catalog(products)


@pytest.mark.parametrize("name", ["routing", "rag", "orders", "guard"])
def test_identifiers_are_unique(name):
    ids = [case["id"] for case in load(name)]
    assert len(ids) == len(set(ids))


class TestRouting:
    def test_ten_cases_per_agent(self):
        assert Counter(c["expected"] for c in load("routing")) == {
            "details": 10,
            "order": 10,
            "recommendation": 10,
        }

    def test_every_case_is_a_valid_conversation_ending_with_the_customer(self):
        for case in load("routing"):
            messages = [ChatMessage(**m) for m in case["messages"]]
            assert messages[-1].role == "user"
            assert case["expected"] in get_args(AgentName)


class TestRag:
    def test_expected_documents_exist(self, products):
        corpus = {doc.id for doc in build_corpus(products, KNOWLEDGE_DIR)}
        for case in load("rag"):
            assert set(case["relevant"]) <= corpus, case["id"]

    def test_answerable_and_unanswerable_are_explicit(self):
        for case in load("rag"):
            assert bool(case["relevant"]) == (case["kind"] != "unanswerable"), case["id"]

    def test_questions_are_unseen_by_the_threshold_choice(self):
        # ADR-008 chose 1.7 on the retrieval set; validating it on the same questions would
        # prove nothing. Only the caffeine question is repeated, on purpose (ADR-008, point 6).
        seen = {c["question"].lower() for c in load("retrieval")}
        repeated = {c["question"] for c in load("rag") if c["question"].lower() in seen}
        assert repeated == {"How much caffeine is in a latte?"}


class TestOrders:
    def test_expected_totals_match_the_catalogue(self, catalog):
        for case in load("orders"):
            lines = [
                OrderLineRequest(product_id=p, quantity=q)
                for p, q in case["expected_items"].items()
            ]
            priced = price_order(lines, catalog)
            assert not priced.rejected_ids, case["id"]
            assert priced.total == Decimal(case["expected_total"]), case["id"]

    def test_statuses_are_valid(self):
        assert {c["expected_status"] for c in load("orders")} == {"open", "closed"}


class TestGuard:
    def test_ten_legitimate_ten_hostile_five_jailbreaks(self):
        cases = load("guard")
        assert Counter(c["expected_allowed"] for c in cases) == {True: 10, False: 10}
        assert sum(c["kind"] == "jailbreak" for c in cases) == 5
