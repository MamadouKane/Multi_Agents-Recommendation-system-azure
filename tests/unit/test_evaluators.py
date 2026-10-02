"""The evaluators guard the CI gate: a bug in them is a bug in the measurement."""

import pytest

from evals.evaluators.deterministic import (
    abstention_scores,
    declined,
    guard_scores,
    menu_hallucination,
    money_amounts,
    order_scores,
    router_accuracy,
)
from evals.run_eval import check
from src.api.core.catalog import Catalog
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


def test_a_crashed_routing_case_counts_as_wrong():
    results = [
        {"kind": "clear", "expected": "order", "predicted": "order"},
        {"kind": "clear", "expected": "details", "error": "RateLimitError"},
    ]
    scores = router_accuracy(results)
    assert scores["router_accuracy"] == 0.5
    assert scores["router_confusion"]["details->error"] == 1


def test_guard_recall_and_false_positives():
    results = [
        {"expected_allowed": False, "allowed": False, "layer": "scope"},
        {"expected_allowed": False, "allowed": True, "layer": "scope"},
        {"expected_allowed": True, "allowed": True, "layer": "scope"},
        {"expected_allowed": True, "error": "boom"},  # no answer: a customer turned away
    ]
    scores = guard_scores(results)
    assert scores["guard_recall_unsafe"] == 0.5
    assert scores["guard_false_positive_rate"] == 0.5


def test_order_total_error_recomputes_from_the_catalogue(catalog):
    good = {
        "order": [{"product_id": "latte", "quantity": 2, "unit_price": "4.75"}],
        "order_total": "9.50",
    }
    forged = {
        "order": [{"product_id": "latte", "quantity": 2, "unit_price": "0.01"}],
        "order_total": "0.02",
    }
    conversations = [
        {
            "expected_items": {"latte": 2},
            "expected_status": "open",
            "final_items": {"latte": 2},
            "final_status": "open",
            "turns": [{"trace": good}, {"trace": forged}],
        },
        {"expected_items": {"latte": 1}, "expected_status": "open", "error": "boom"},
    ]
    scores = order_scores(conversations, catalog)
    assert scores["order_total_error"] == 1
    assert scores["order_exact_match"] == 0.5  # the crash is a miss, not a case left out


def test_only_catalogue_prices_may_appear_in_written_answers(catalog):
    scores = menu_hallucination(
        ["latte", "matcha-latte"], ["A latte is 4.75 USD.", "It costs $3.20."], catalog
    )
    assert scores["unknown_product_ids"] == ["matcha-latte"]
    assert scores["unknown_prices"] == ["3.20"]
    assert scores["menu_hallucination"] == 2


def test_money_amounts_reads_both_notations():
    assert money_amounts("4.75 USD, or $2 for an espresso") == ["4.75", "2.00"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("I'm sorry, I don't have that information.", True),
        (
            "I don\u2019t have calorie information for the croissant.",
            True,
        ),  # the model's apostrophe
        ("Yes, the café has free wifi.", False),
    ],
)
def test_declined(answer, expected):
    assert declined(answer) is expected


def test_abstention_on_unanswerable_and_answerable_questions():
    results = [
        {"relevant": [], "declined": True},
        {"relevant": [], "declined": False},
        {"relevant": ["about-hours"], "declined": False},
        {"relevant": ["about-hours"], "error": "boom"},
    ]
    scores = abstention_scores(results)
    assert scores["rag_correct_abstention"] == 0.5
    assert scores["rag_false_abstention"] == 0.5


def test_thresholds_min_max_equals_and_non_blocking_rules():
    metrics = {"a": 0.9, "b": 6000, "c": 0, "d": 0.5}
    rules = {
        "a": {"min": 0.85},
        "b": {"max": 5000},
        "c": {"equals": 0},
        "d": {"min": 0.8, "blocking": False},
        "missing": {"min": 1},
    }
    rows = {r["metric"]: r for r in check(metrics, rules)}
    assert rows["a"]["passed"] and rows["c"]["passed"]
    assert not rows["b"]["passed"] and rows["b"]["blocking"]
    assert not rows["d"]["passed"] and not rows["d"]["blocking"]
    assert "missing" not in rows  # a suite that did not run is not a failure
