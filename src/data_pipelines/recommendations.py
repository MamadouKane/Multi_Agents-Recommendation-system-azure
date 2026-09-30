"""Convert the prototype's recommendation artefacts to the serving format (task 3.10).

    apriori_recommendations.json     {name: [{product, product_category, confidence}]}
    popularity_recommendation.csv    product, product_category, number_of_transactions
      -> data/processed/recommendations.json, keyed by product_id

A name maps to a product only when the catalogue resolves it exactly, or by spelling ("Carmel
syrup"), **and** the category matches. That second check is what removes the packaged dark
chocolate bar, out of scope since D11, which shares its name with the drink. Everything dropped is
reported, never silently lost. Day 4 replaces this with a pipeline retrained on the sales data.

Usage: python -m src.data_pipelines.recommendations [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.api.core.catalog import Catalog
from src.api.core.recommender import Association, RecommendationArtifacts
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl

LEGACY_DIR = Path("legacy/recommendation_objects")
APRIORI_PATH = LEGACY_DIR / "apriori_recommendations.json"
POPULARITY_PATH = LEGACY_DIR / "popularity_recommendation.csv"
OUT_PATH = Path("data/processed/recommendations.json")


@dataclass
class Conversion:
    artifacts: RecommendationArtifacts
    dropped: list[str] = field(default_factory=list)


class NameMapper:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def __call__(self, name: str, category: str | None = None) -> str | None:
        resolution = self._catalog.resolve(name)
        product = resolution.product
        if product is None or resolution.status == "ambiguous":
            return None
        if category is not None and product.category != category:
            return None
        return product.product_id


def convert(
    apriori: dict[str, list[dict[str, object]]], popularity: list[dict[str, str]], catalog: Catalog
) -> Conversion:
    to_id = NameMapper(catalog)
    dropped: list[str] = []

    popular: dict[str, int] = {}
    categories: dict[str, set[str]] = {}
    for row in popularity:
        name, category = row["product"], row["product_category"]
        categories.setdefault(name, set()).add(category)
        product_id = to_id(name, category)
        if product_id is None:
            dropped.append(f"popularity: {name} ({category})")
            continue
        popular[product_id] = popular.get(product_id, 0) + int(row["number_of_transactions"])

    rules: dict[str, list[Association]] = {}
    for antecedent, consequents in apriori.items():
        # The legacy keys carry no category. A name sold in two categories ("Dark chocolate") is
        # read as the catalogue product, since that is the only one a customer can have ordered.
        antecedent_id = to_id(antecedent)
        if antecedent_id is None:
            dropped.append(f"rule antecedent: {antecedent}")
            continue
        kept = []
        for consequent in consequents:
            name, category = str(consequent["product"]), str(consequent["product_category"])
            product_id = to_id(name, category)
            if product_id is None or product_id == antecedent_id:
                dropped.append(f"rule {antecedent} -> {name} ({category})")
                continue
            kept.append(
                Association(product_id=product_id, confidence=float(str(consequent["confidence"])))
            )
        rules[antecedent_id] = sorted(kept, key=lambda a: -a.confidence)

    source = f"legacy prototype artefacts ({APRIORI_PATH.name}, {POPULARITY_PATH.name})"
    return Conversion(
        RecommendationArtifacts(source=source, rules=rules, popularity=popular), dropped
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    catalog = Catalog(build_catalog(read_jsonl(RAW_PATH)))
    with APRIORI_PATH.open(encoding="utf-8") as f:
        apriori = json.load(f)
    with POPULARITY_PATH.open(encoding="utf-8", newline="") as f:
        popularity = list(csv.DictReader(f))

    result = convert(apriori, popularity, catalog)
    artifacts = result.artifacts
    rule_count = sum(len(v) for v in artifacts.rules.values())
    print(
        f"{len(artifacts.rules)} antecedents, {rule_count} rules, "
        f"{len(artifacts.popularity)} products with sales"
    )
    for line in result.dropped:
        print(f"  dropped {line}")

    uncovered = sorted(
        p.product_id for p in catalog.products if p.product_id not in artifacts.rules
    )
    if uncovered:
        print(f"  no rule for {', '.join(uncovered)}: popular items are suggested instead")

    if not args.dry_run:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(artifacts.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"written {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
