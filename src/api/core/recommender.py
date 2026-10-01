"""Recommendations from precomputed artefacts: association rules and popularity (task 3.10).

The artefacts are keyed by `product_id`, never by display name: the prototype's files said
"Carmel syrup" and mixed two products under "Dark chocolate" (D11). Whatever produces them, the
legacy converter today or the Azure ML pipeline on day 4, writes this one format.

Every recommendation is checked against the live catalogue at the moment it is served: an
artefact trained last month cannot suggest a product withdrawn this morning (US3: items that
exist in the catalogue, nothing else).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.api.core.catalog import Catalog
from src.api.core.schemas import Category, Product

DEFAULT_TOP_K = 5
# Kept from the prototype: without it, a latte brings back four syrups and nothing else.
MAX_PER_CATEGORY = 2


class Association(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: str
    confidence: float = Field(ge=0, le=1)


class RecommendationArtifacts(BaseModel):
    """The serving format. Rules have one antecedent, so the lookup key is exact (debt D10)."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Where the artefacts come from, for the traces.")
    version: str | None = Field(
        default=None, description="Registered model version, such as 'coffee-reco-apriori:3'."
    )
    rules: dict[str, list[Association]]
    popularity: dict[str, int] = Field(description="product_id -> number of transactions.")

    @classmethod
    def load(cls, path: Path) -> RecommendationArtifacts:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_json(cls, data: bytes | str) -> RecommendationArtifacts:
        return cls.model_validate_json(data)


class Recommender:
    def __init__(self, artifacts: RecommendationArtifacts, catalog: Catalog) -> None:
        self._artifacts = artifacts
        self._catalog = catalog

    @property
    def source(self) -> str:
        return self._artifacts.source

    @property
    def version(self) -> str | None:
        return self._artifacts.version

    def for_basket(
        self,
        product_ids: Iterable[str],
        top_k: int = DEFAULT_TOP_K,
        complements_only: bool = False,
    ) -> list[Product]:
        """What customers who bought these items also bought, best confidence first.

        `complements_only` leaves out the categories already in the basket: someone holding a
        latte is offered a syrup or a pastry, not a cappuccino. The rules alone cannot tell a
        complement from a substitute, since both are often bought together.
        """
        basket = set(product_ids)
        best: dict[str, float] = {}
        for product_id in basket:
            for rule in self._artifacts.rules.get(product_id, []):
                best[rule.product_id] = max(best.get(rule.product_id, 0.0), rule.confidence)
        ranked = sorted(best, key=lambda pid: (-best[pid], pid))
        if complements_only:
            held = {p.category for pid in basket if (p := self._catalog.get(pid)) is not None}
            ranked = [
                pid
                for pid in ranked
                if (p := self._catalog.get(pid)) is not None and p.category not in held
            ]
        return self.serve(ranked, exclude=basket, top_k=top_k)

    def popular(
        self, categories: Sequence[Category] = (), top_k: int = DEFAULT_TOP_K
    ) -> list[Product]:
        """Best sellers, optionally within some categories."""
        counts = self._artifacts.popularity
        ranked = sorted(counts, key=lambda pid: (-counts[pid], pid))
        if categories:
            ranked = [
                pid
                for pid in ranked
                if (p := self._catalog.get(pid)) is not None and p.category in categories
            ]
            # One category asked for: the customer wants a choice within it, not a mix.
            return self.serve(ranked, top_k=top_k, per_category=top_k)
        return self.serve(ranked, top_k=top_k)

    def serve(
        self,
        ranked: Iterable[str],
        exclude: Iterable[str] = (),
        top_k: int = DEFAULT_TOP_K,
        per_category: int = MAX_PER_CATEGORY,
    ) -> list[Product]:
        """The output filter: live catalogue only, no duplicates, a few per category."""
        excluded = set(exclude)
        chosen: list[Product] = []
        per: dict[str, int] = {}
        for product_id in ranked:
            product = self._catalog.get(product_id)
            if product is None or product_id in excluded or product in chosen:
                continue
            if per.get(product.category, 0) >= per_category:
                continue
            per[product.category] = per.get(product.category, 0) + 1
            chosen.append(product)
            if len(chosen) == top_k:
                break
        return chosen
