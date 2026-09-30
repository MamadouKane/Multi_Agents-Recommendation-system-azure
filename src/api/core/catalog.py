"""The catalogue as the API sees it: loaded once from Cosmos, then read from memory (ADR-004).

Three services for the agents:

- `get(product_id)`: the product behind an identifier, or None.
- `render_menu_for_prompt()`: the menu injected into prompts, rendered from the catalogue at
  runtime, never typed into a prompt (debt D2). It carries identifiers and names, and **no price**:
  a model that never sees a price cannot invent one (ADR-003).
- `resolve(text)`: the product a customer means by "a latte", "carmel syrup" or "croissants",
  or an explicit "ambiguous" or "unknown" instead of a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from rapidfuzz import fuzz, process, utils

from src.api.core.schemas import Product

# Cosmos adds these to every document; they are not part of the product.
COSMOS_SYSTEM_FIELDS = {"_rid", "_self", "_etag", "_attachments", "_ts"}

# Words that carry no product meaning in "a latte", "some croissants", "one espresso".
FILLER_WORDS = {"a", "an", "the", "some", "please", "of", "another", "more"}
# Quantities belong to the order, not to the product name: "two croissants" means "croissant".
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}

# A candidate is kept when WRatio reaches this, and a winner must beat the runner-up by the margin.
CANDIDATE_SCORE = 88
WINNER_MARGIN = 8
# A near exact spelling of a full name ("lattes", "cappucino") is accepted on its own.
SPELLING_SCORE = 88


class CosmosContainer(Protocol):
    def query_items(self, query: str, **kwargs: Any) -> Iterable[dict[str, Any]]: ...


@dataclass(frozen=True)
class Resolution:
    status: Literal["exact", "fuzzy", "ambiguous", "unknown"]
    query: str
    product: Product | None = None
    candidates: tuple[Product, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.product is not None


def normalise(text: str) -> list[str]:
    """Lower case, no punctuation, no filler words, simple plurals folded: 'lattes' -> 'latte'."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    folded = []
    for word in words:
        if word in FILLER_WORDS or word in NUMBER_WORDS or word.isdigit():
            continue
        # "scones" -> "scone", but keep short words and words that end in "ss" ("espresso" is safe).
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        folded.append(word)
    return folded


class Catalog:
    def __init__(self, products: Iterable[Product]) -> None:
        active = [p for p in products if p.is_active]
        self._by_id = {p.product_id: p for p in active}
        if len(self._by_id) != len(active):
            raise ValueError("duplicate product_id in the catalogue")
        # Every spelling a customer may use, pointing to its product: name and aliases.
        self._labels: dict[str, Product] = {}
        for product in active:
            for label in (product.name, *product.aliases):
                self._labels[" ".join(normalise(label))] = product

    @classmethod
    def from_cosmos(cls, container: CosmosContainer) -> Catalog:
        documents = container.query_items("SELECT * FROM c", enable_cross_partition_query=True)
        products = [
            Product.model_validate({k: v for k, v in doc.items() if k not in COSMOS_SYSTEM_FIELDS})
            for doc in documents
        ]
        if not products:
            # Fail at startup with a clear message, not on the first order with an empty menu.
            raise RuntimeError("the Cosmos catalogue is empty: run `make ingest`")
        return cls(products)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, product_id: object) -> bool:
        return product_id in self._by_id

    @property
    def products(self) -> list[Product]:
        return sorted(self._by_id.values(), key=lambda p: (p.category, p.name))

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def render_menu_for_prompt(self) -> str:
        """One line per product: identifier, name, category. Deliberately no price (ADR-003)."""
        lines = []
        for p in self.products:
            line = f"- {p.product_id}: {p.name} ({p.category})"
            if p.aliases:
                # Without them, a model reading "hot chocolate" finds no match in "Dark chocolate".
                line += f", also called {', '.join(p.aliases)}"
            lines.append(line)
        return "\n".join(lines)

    def resolve(self, text: str) -> Resolution:
        words = normalise(text)
        key = " ".join(words)
        if not words:
            return Resolution("unknown", text)

        # 1. An identifier or a known spelling, exactly.
        if key.replace(" ", "-") in self._by_id:
            return Resolution("exact", text, self._by_id[key.replace(" ", "-")])
        if key in self._labels:
            return Resolution("exact", text, self._labels[key])

        # 2. A near spelling of a whole name: "cappucino", "carmel syrup".
        spelling = process.extractOne(key, list(self._labels), scorer=fuzz.ratio)
        if spelling and spelling[1] >= SPELLING_SCORE:
            return Resolution("fuzzy", text, self._labels[spelling[0]])

        # 3. A partial name: accepted only when every word typed belongs to the product name.
        #    This is what stops "matcha latte" from becoming a latte.
        matches = process.extract(
            key, list(self._labels), scorer=fuzz.WRatio, processor=utils.default_process, limit=6
        )
        typed = set(words)
        covering: dict[str, tuple[Product, float]] = {}
        for label, score, _ in matches:
            product = self._labels[label]
            if score >= CANDIDATE_SCORE and typed <= set(label.split()):
                best = covering.get(product.product_id)
                if best is None or score > best[1]:
                    covering[product.product_id] = (product, score)

        ranked = sorted(covering.values(), key=lambda item: item[1], reverse=True)
        if not ranked:
            return Resolution("unknown", text)
        if len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= WINNER_MARGIN:
            return Resolution("fuzzy", text, ranked[0][0])
        return Resolution("ambiguous", text, candidates=tuple(p for p, _ in ranked))
