"""Association rules between two products: "customers who bought A also bought B".

The serving side only ever looks up one product at a time (debt D10), so only rules with one
antecedent and one consequent are useful. Those come from pairs of products, which are counted
directly here instead of running the full Apriori search: same rules as mlxtend's
`apriori` + `association_rules` restricted to pairs (checked by a test), no extra dependency.

    support(A, B)  = share of baskets holding both A and B
    confidence     = support(A, B) / support(A)      P(B | A)
    lift           = confidence / support(B)          above 1: bought together more than chance

Rules are mined on baskets of at least two items, as the prototype did: a single-item receipt
says nothing about what goes together, it only dilutes every support.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import permutations

from .data import Basket


@dataclass(frozen=True)
class Rule:
    antecedent: str
    consequent: str
    support: float
    confidence: float
    lift: float


def mine_rules(baskets: Iterable[Basket], min_support: float, min_lift: float = 1.0) -> list[Rule]:
    multi = [b.items for b in baskets if len(b.items) >= 2]
    if not multi:
        return []
    n = len(multi)
    singles: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for items in multi:
        singles.update(items)
        pairs.update(permutations(sorted(items), 2))

    rules = []
    for (a, b), count in pairs.items():
        support = count / n
        # Apriori keeps a pair only when the pair and both items are frequent; for a pair, the
        # pair's support is the binding constraint, since an item is at least as frequent.
        if support < min_support:
            continue
        confidence = count / singles[a]
        lift = confidence / (singles[b] / n)
        if lift >= min_lift:
            rules.append(Rule(a, b, support, confidence, lift))
    return sorted(rules, key=lambda r: (r.antecedent, -r.confidence, r.consequent))


def rules_by_antecedent(rules: Iterable[Rule]) -> dict[str, list[Rule]]:
    grouped: dict[str, list[Rule]] = {}
    for rule in rules:
        grouped.setdefault(rule.antecedent, []).append(rule)
    for antecedent in grouped:
        grouped[antecedent].sort(key=lambda r: (-r.confidence, r.consequent))
    return grouped
