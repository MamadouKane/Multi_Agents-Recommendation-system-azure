"""Retrieval metrics. Plain functions, deterministic and free, reused by the day 5 harness."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence


def first_relevant_rank(ranked_ids: Sequence[str], relevant: Collection[str]) -> int | None:
    """1-based rank of the first relevant document, None when none was retrieved."""
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant:
            return rank
    return None


def recall_at_k(ranked_ids: Sequence[str], relevant: Collection[str], k: int) -> float:
    """1.0 when at least one relevant document is in the top k, else 0.0.

    "At least one" rather than "all of them": any listed document answers the question, and the
    agent receives several documents as context.
    """
    rank = first_relevant_rank(ranked_ids, relevant)
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(ranked_ids: Sequence[str], relevant: Collection[str]) -> float:
    rank = first_relevant_rank(ranked_ids, relevant)
    return 0.0 if rank is None else 1.0 / rank


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, the definition latency SLOs use."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return float(ordered[index])
