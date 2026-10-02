"""Deterministic evaluators (task 5.5). Each takes the harness's results and returns numbers.

Two order metrics on purpose, because they fail for different reasons:
- order_exact_match: did the model understand the order? Final items and quantities equal
  the expected ones.
- order_total_error: is the bill right for what was billed? Every billed total is recomputed
  from catalogue prices, independently of the pricing code. Zero by construction (ADR-003),
  measured anyway: a regression there would be a billing bug, not a language one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from src.api.app.telemetry import order_is_consistent
from src.api.core.catalog import Catalog

# Every evaluator receives every case, crashed ones included: a case that raised (a 429, an
# outage) counts as a failure, never as a case left out of the denominator.

MONEY = re.compile(r"(\d+\.\d{2})\s*(?:USD|\$)|\$\s*(\d+(?:\.\d{2})?)")


def router_accuracy(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    correct = sum(r.get("predicted") == r["expected"] for r in results)
    confusion: Counter[str] = Counter(
        f"{r['expected']}->{r.get('predicted', 'error')}" for r in results
    )
    by_kind: dict[str, list[bool]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r.get("predicted") == r["expected"])
    return {
        "router_accuracy": correct / len(results),
        "router_accuracy_by_kind": {k: sum(v) / len(v) for k, v in sorted(by_kind.items())},
        "router_confusion": dict(sorted(confusion.items())),
    }


def guard_scores(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    hostile = [r for r in results if not r["expected_allowed"]]
    benign = [r for r in results if r["expected_allowed"]]
    blocked_layers = Counter(r["layer"] for r in hostile if r.get("allowed") is False)
    return {
        # Share of hostile messages refused: what got through is the security gap.
        "guard_recall_unsafe": sum(r.get("allowed") is False for r in hostile) / len(hostile),
        # Share of legitimate messages refused: each one is a customer turned away. A crash
        # counts here too: the customer got no answer.
        "guard_false_positive_rate": sum(r.get("allowed") is not True for r in benign)
        / len(benign),
        "guard_blocking_layers": dict(sorted(blocked_layers.items())),
    }


def order_scores(conversations: Sequence[Mapping[str, Any]], catalog: Catalog) -> dict[str, Any]:
    exact = [c.get("final_items") == c["expected_items"] for c in conversations]
    status = [c.get("final_status") == c["expected_status"] for c in conversations]
    billed = [t for c in conversations for t in c.get("turns", []) if "order_total" in t["trace"]]
    inconsistent = [t for t in billed if not order_is_consistent(t["trace"], catalog)]
    return {
        "order_exact_match": sum(exact) / len(exact),
        "order_status_match": sum(status) / len(status),
        "order_total_error": len(inconsistent),
        "order_billed_turns_checked": len(billed),
    }


def menu_hallucination(
    product_ids: Iterable[str], answers: Iterable[str], catalog: Catalog
) -> dict[str, Any]:
    """Products that do not exist in structured outputs, and prices that do not exist in the
    answers a model writes. `answers` must be model-written text only: a receipt's totals are
    computed by Python and checked by order_total_error instead."""
    unknown_ids = sorted({p for p in product_ids if p not in catalog})
    prices = {f"{p.price:.2f}" for p in catalog.products}
    unknown_prices = sorted(
        {amount for text in answers for amount in money_amounts(text) if amount not in prices}
    )
    return {
        "menu_hallucination": len(unknown_ids) + len(unknown_prices),
        "unknown_product_ids": unknown_ids,
        "unknown_prices": unknown_prices,
    }


def money_amounts(text: str) -> list[str]:
    amounts = []
    for match in MONEY.finditer(text):
        value = match.group(1) or match.group(2)
        amounts.append(f"{Decimal(value):.2f}")
    return amounts


def abstention_scores(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Unanswerable questions must get "I don't know"; answerable ones must not."""
    unanswerable = [r for r in results if not r["relevant"]]
    answerable = [r for r in results if r["relevant"]]
    return {
        "rag_correct_abstention": sum(r.get("declined") is True for r in unanswerable)
        / len(unanswerable),
        "rag_false_abstention": sum(r.get("declined") is not False for r in answerable)
        / len(answerable),
    }


# "I don't have that information", "I don't have calorie information for the croissant"...
DECLINE = re.compile(
    r"(don.t|do not) have [\w\s]{0,30}information|not able to answer|(don.t|do not) know",
    re.I,
)


def declined(answer: str) -> bool:
    return bool(DECLINE.search(answer))
