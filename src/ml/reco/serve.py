"""The ranking the API serves, reproduced without the API's dependencies.

The offline evaluation must measure what customers actually get: the per-category cap, the
basket excluded, the best confidence across the basket's items. `src/api/core/recommender.py` is
the serving implementation; this one exists so the evaluation runs on Azure ML without pydantic
or the catalogue, and a parity test keeps the two identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

MAX_PER_CATEGORY = 2

Rules = Mapping[
    str, Sequence[Mapping[str, object]]
]  # serving format: {pid: [{product_id, confidence}]}


def serve(
    ranked: Iterable[str],
    categories: Mapping[str, str],
    exclude: Iterable[str] = (),
    top_k: int = 5,
    per_category: int = MAX_PER_CATEGORY,
) -> list[str]:
    excluded = set(exclude)
    chosen: list[str] = []
    per: dict[str, int] = {}
    for product_id in ranked:
        category = categories.get(product_id)
        if category is None or product_id in excluded or product_id in chosen:
            continue
        if per.get(category, 0) >= per_category:
            continue
        per[category] = per.get(category, 0) + 1
        chosen.append(product_id)
        if len(chosen) == top_k:
            break
    return chosen


def for_basket(
    rules: Rules,
    categories: Mapping[str, str],
    basket: Iterable[str],
    top_k: int = 5,
    complements_only: bool = False,
) -> list[str]:
    held = set(basket)
    best: dict[str, float] = {}
    for product_id in held:
        for rule in rules.get(product_id, []):
            consequent, confidence = str(rule["product_id"]), float(rule["confidence"])  # type: ignore[arg-type]
            best[consequent] = max(best.get(consequent, 0.0), confidence)
    ranked = sorted(best, key=lambda pid: (-best[pid], pid))
    if complements_only:
        held_categories = {categories.get(pid) for pid in held}
        ranked = [pid for pid in ranked if categories.get(pid) not in held_categories]
    return serve(ranked, categories, exclude=held, top_k=top_k)


def popular(
    counts: Mapping[str, int],
    categories: Mapping[str, str],
    exclude: Iterable[str] = (),
    top_k: int = 5,
    per_category: int = MAX_PER_CATEGORY,
) -> list[str]:
    ranked = sorted(counts, key=lambda pid: (-counts[pid], pid))
    return serve(ranked, categories, exclude=exclude, top_k=top_k, per_category=per_category)
