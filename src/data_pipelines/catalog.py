"""Turn the raw product file into the validated catalogue (roadmap task 2.1).

    data/raw/products.jsonl  ->  data/processed/catalog.jsonl

Three things are added to each raw record: a stable `product_id`, the allergens derived from
the ingredients, and an `is_active` flag. The raw file is never modified (data/README.md).

Run from the repository root:  python -m src.data_pipelines.catalog
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.api.core.schemas import Allergen, Product

RAW_PATH = Path("data/raw/products.jsonl")
OUT_PATH = Path("data/processed/catalog.jsonl")

# Every ingredient on the menu, mapped to (contains, may_contain).
# The table must be exhaustive: an ingredient missing from it stops the pipeline, because
# "no allergen found" and "allergen not checked" must never look the same to a customer.
INGREDIENT_ALLERGENS: dict[str, tuple[frozenset[Allergen], frozenset[Allergen]]] = {
    # Dairy
    "Butter": (frozenset({"milk"}), frozenset()),
    "Cheese": (frozenset({"milk"}), frozenset()),
    "Cream": (frozenset({"milk"}), frozenset()),
    "Milk": (frozenset({"milk"}), frozenset()),
    "Milk Foam": (frozenset({"milk"}), frozenset()),
    "Steamed Milk": (frozenset({"milk"}), frozenset()),
    # Eggs
    "Eggs": (frozenset({"eggs"}), frozenset()),
    # Cereals containing gluten (oats are on the EU list)
    "Flour": (frozenset({"gluten"}), frozenset()),
    "Oats": (frozenset({"gluten"}), frozenset()),
    # Tree nuts
    "Almonds": (frozenset({"tree_nuts"}), frozenset()),
    "Hazelnuts": (frozenset({"tree_nuts"}), frozenset()),
    "Hazelnut Extract": (frozenset({"tree_nuts"}), frozenset()),
    # Almond cream is almonds for certain, and usually butter and eggs: the recipe does not say.
    "Almond Cream": (frozenset({"tree_nuts"}), frozenset({"milk", "eggs"})),
    # Chocolate usually carries milk solids and soy lecithin, unconfirmed by the recipe.
    "Chocolate": (frozenset(), frozenset({"milk", "soy"})),
    "Chocolate Chips": (frozenset(), frozenset({"milk", "soy"})),
    # No major allergen
    "Baking Powder": (frozenset(), frozenset()),
    "Cocoa Powder": (frozenset(), frozenset()),
    "Cranberries": (frozenset(), frozenset()),
    "Espresso": (frozenset(), frozenset()),
    "Ginger": (frozenset(), frozenset()),
    "Herbs": (frozenset(), frozenset()),
    "Natural Flavors": (frozenset(), frozenset()),
    "Salt": (frozenset(), frozenset()),
    "Sucralose": (frozenset(), frozenset()),
    "Sugar": (frozenset(), frozenset()),
    "Vanilla Extract": (frozenset(), frozenset()),
    "Water": (frozenset(), frozenset()),
    "Yeast": (frozenset(), frozenset()),
}

# Identifiers that cannot be derived from the name. Debt D11: "Dark chocolate" is the drink,
# and the packaged bar is out of scope, so the identifier says which one it is.
PRODUCT_ID_OVERRIDES = {"Dark chocolate": "dark-chocolate-drinking"}

# Display name corrections. The raw spelling is kept as an alias so customers typing it still match.
DISPLAY_NAME_FIXES = {"Carmel syrup": "Caramel syrup"}

# Description claims about products the shop does not sell. The source text is marketing copy:
# three scones "pair with tea", the syrups top "desserts" and "ice cream", and the
# details agent repeated them faithfully to a customer (day 7 user test). Fixed here, and
# `tests/unit/test_catalog.py` refuses any description naming an unsold product.
DESCRIPTION_FIXES: dict[str, list[tuple[str, str]]] = {
    "Cranberry Scone": [
        ("pairs wonderfully with tea or coffee", "pairs wonderfully with a coffee")
    ],
    "Ginger Scone": [
        ("pairs beautifully with a cup of tea or coffee", "pairs beautifully with a coffee")
    ],
    "Jumbo Savory Scone": [("with your favorite coffee or tea", "with your favorite coffee")],
    "Carmel syrup": [
        ("topping your drinks and desserts", "flavouring your drinks"),
        ("everything from coffee to ice cream", "a coffee or a drinking chocolate"),
    ],
    "Chocolate syrup": [("drizzling over desserts or adding to", "adding to")],
    "Hazelnut syrup": [("perfect for lattes and desserts", "perfect for lattes")],
    "Sugar Free Vanilla syrup": [("perfect for your coffee or dessert", "perfect for your coffee")],
    # "Nutty" for a scone without any nut: misleading next to an allergen question (US2).
    "Oatmeal Scone": [("Nutty and wholesome", "Hearty and wholesome")],
}


def fixed_description(raw_name: str, text: str) -> str:
    for old, new in DESCRIPTION_FIXES.get(raw_name, []):
        text = text.replace(old, new)
    return text


# Names customers use that the catalogue name does not contain. "Hot chocolate" is what the Kaggle
# sales data calls this drink, and what a customer says: without it, fuzzy matching sent
# "hot chocolate" to the chocolate chip biscotti.
EXTRA_ALIASES = {"Dark chocolate": ["Hot chocolate", "Drinking chocolate"]}


class UnknownIngredientError(ValueError):
    """Raised when an ingredient has no allergen classification."""


def slugify(name: str) -> str:
    """'Sugar Free Vanilla syrup' -> 'sugar-free-vanilla-syrup'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build an identifier from {name!r}")
    return slug


def derive_allergens(ingredients: Iterable[str]) -> tuple[list[Allergen], list[Allergen]]:
    """Return (contains, may_contain), sorted, with no allergen listed in both."""
    ingredients = list(ingredients)
    unknown = sorted({i for i in ingredients if i not in INGREDIENT_ALLERGENS})
    if unknown:
        raise UnknownIngredientError(
            f"no allergen classification for {unknown}: add them to INGREDIENT_ALLERGENS"
        )
    contains: set[Allergen] = set()
    possible: set[Allergen] = set()
    for ingredient in ingredients:
        certain, maybe = INGREDIENT_ALLERGENS[ingredient]
        contains |= certain
        possible |= maybe
    # A certain allergen is not also a possible one.
    return sorted(contains), sorted(possible - contains)


def enrich(raw: dict[str, Any]) -> Product:
    """Validate one raw record and add the derived fields."""
    raw_name = raw["name"]
    name = DISPLAY_NAME_FIXES.get(raw_name, raw_name)
    product_id = PRODUCT_ID_OVERRIDES.get(raw_name, slugify(name))
    contains, may_contain = derive_allergens(raw["ingredients"])
    aliases = ([raw_name] if name != raw_name else []) + EXTRA_ALIASES.get(raw_name, [])

    return Product(
        id=product_id,
        product_id=product_id,
        name=name,
        category=raw["category"],
        description=fixed_description(raw_name, raw["description"].strip()),
        ingredients=raw["ingredients"],
        allergens=contains,
        may_contain=may_contain,
        # str() first: Decimal(4.75) would carry the binary float error into every price.
        price=Decimal(str(raw["price"])).quantize(Decimal("0.01")),
        rating=raw["rating"],
        image_file=raw["image_path"],
        aliases=aliases,
    )


def build_catalog(raw_records: Iterable[dict[str, Any]]) -> list[Product]:
    products = [enrich(record) for record in raw_records]
    ids = [p.product_id for p in products]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate product_id values: {duplicates}")
    return sorted(products, key=lambda p: p.product_id)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    products = build_catalog(read_jsonl(RAW_PATH))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        for product in products:
            handle.write(product.model_dump_json() + "\n")

    print(f"{len(products)} products written to {OUT_PATH}")
    for p in products:
        allergens = ", ".join(p.allergens) or "none"
        extra = f" (may contain {', '.join(p.may_contain)})" if p.may_contain else ""
        print(f"  {p.product_id:28} {p.price:>5} {p.currency}  {allergens}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
