"""Offline evaluation, the part the prototype never had (task 4.7).

Protocol, leave one out on future baskets:
- train on the past only (temporal split, never a random one: shuffling would let the model
  learn from the very week it is tested on);
- for every test basket of at least two items, hide one item, give the model the rest, and
  check whether the hidden item comes back in its top k. Every item of every basket is hidden
  in turn.

Metrics, averaged over those cases:
- hit_rate@k: the hidden item is in the list. With one hidden item it is also recall@k.
- precision@k: hits / k. Bounded by 1/k here, kept because the roadmap asks for it.
- mrr@k: 1 / rank of the hidden item, 0 when absent: rewards putting it first.
- coverage@k: share of the catalogue recommended at least once. A model that always says
  "cappuccino" scores well on hits and badly here.
- diversity@k: mean number of distinct categories in a list.
- mean_list_length: rules can return fewer than k items.

Models:
- popularity: best sellers not already in the basket. The baseline to beat.
- apriori: the association rules alone, served as the API serves them.
- apriori_fill: the rules, completed with best sellers up to k, as the API serves them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .data import Basket
from .serve import Rules, for_basket, popular

K = 5
Recommend = Callable[[Sequence[str]], list[str]]


@dataclass(frozen=True)
class Scores:
    cases: int
    hit_rate: float
    precision: float
    mrr: float
    coverage: float
    diversity: float
    mean_list_length: float

    def as_metrics(self, prefix: str) -> dict[str, float]:
        return {
            f"{prefix}.hit_rate_at_{K}": self.hit_rate,
            f"{prefix}.precision_at_{K}": self.precision,
            f"{prefix}.mrr_at_{K}": self.mrr,
            f"{prefix}.coverage_at_{K}": self.coverage,
            f"{prefix}.diversity_at_{K}": self.diversity,
            f"{prefix}.mean_list_length": self.mean_list_length,
        }


def models(
    rules: Rules, counts: Mapping[str, int], categories: Mapping[str, str]
) -> dict[str, Recommend]:
    def by_popularity(given: Sequence[str]) -> list[str]:
        # The plain baseline: no category cap, just the best sellers.
        return popular(counts, categories, exclude=given, top_k=K, per_category=K)

    def by_rules(given: Sequence[str]) -> list[str]:
        return for_basket(rules, categories, given, top_k=K)

    def by_rules_then_popularity(given: Sequence[str]) -> list[str]:
        # What the API serves: the rules, then capped best sellers up to k.
        chosen = by_rules(given)
        for product_id in popular(counts, categories, exclude=[*given, *chosen], top_k=K):
            if len(chosen) == K:
                break
            chosen.append(product_id)
        return chosen

    return {
        "popularity": by_popularity,
        "apriori": by_rules,
        "apriori_fill": by_rules_then_popularity,
    }


def evaluate(
    recommend: Recommend, baskets: Iterable[Basket], categories: Mapping[str, str]
) -> Scores:
    hits = reciprocal = length = distinct = 0.0
    cases = 0
    seen: set[str] = set()
    for basket in baskets:
        if len(basket.items) < 2:
            continue
        for hidden in sorted(basket.items):
            given = sorted(basket.items - {hidden})
            recommended = recommend(given)[:K]
            cases += 1
            seen.update(recommended)
            length += len(recommended)
            distinct += len({categories[p] for p in recommended})
            if hidden in recommended:
                hits += 1
                reciprocal += 1 / (recommended.index(hidden) + 1)
    if cases == 0:
        raise ValueError("no test basket with at least two items")
    return Scores(
        cases=cases,
        hit_rate=hits / cases,
        precision=hits / (cases * K),
        mrr=reciprocal / cases,
        coverage=len(seen) / len(categories),
        diversity=distinct / cases,
        mean_list_length=length / cases,
    )


def hits_per_basket(recommend: Recommend, baskets: Iterable[Basket]) -> list[tuple[int, int]]:
    """(hits, cases) for every test basket: the unit a bootstrap must resample, since the cases
    of one basket are not independent of each other."""
    out = []
    for basket in baskets:
        if len(basket.items) < 2:
            continue
        hits = 0
        for hidden in sorted(basket.items):
            if hidden in recommend(sorted(basket.items - {hidden}))[:K]:
                hits += 1
        out.append((hits, len(basket.items)))
    return out


def paired_bootstrap(
    model: Sequence[tuple[int, int]],
    baseline: Sequence[tuple[int, int]],
    samples: int = 2000,
    seed: int = 7,
) -> dict[str, float]:
    """95 % interval of hit_rate(model) - hit_rate(baseline), resampling whole baskets.

    Paired: both models are scored on the same resampled baskets, so the noise of which baskets
    happened to fall in the test week cancels out of the difference.
    """
    import random

    if len(model) != len(baseline) or not model:
        raise ValueError("both models must be scored on the same, non empty, baskets")
    rng = random.Random(seed)
    n = len(model)
    deltas = []
    for _ in range(samples):
        picks = [rng.randrange(n) for _ in range(n)]
        cases = sum(model[i][1] for i in picks)
        deltas.append(
            (sum(model[i][0] for i in picks) - sum(baseline[i][0] for i in picks)) / cases
        )
    deltas.sort()
    observed = (sum(h for h, _ in model) - sum(h for h, _ in baseline)) / sum(c for _, c in model)
    return {
        "delta": observed,
        "ci_low": deltas[int(0.025 * samples)],
        "ci_high": deltas[int(0.975 * samples) - 1],
    }
