"""Recommender and legacy artefact conversion: every suggestion must exist in the catalogue."""

import csv
import json

import pytest

from src.api.core.catalog import Catalog
from src.api.core.recommender import Association, RecommendationArtifacts, Recommender
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.recommendations import APRIORI_PATH, POPULARITY_PATH, convert


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


@pytest.fixture(scope="module")
def conversion(catalog):
    apriori = json.loads(APRIORI_PATH.read_text(encoding="utf-8"))
    with POPULARITY_PATH.open(encoding="utf-8", newline="") as f:
        popularity = list(csv.DictReader(f))
    return convert(apriori, popularity, catalog)


def artifacts(rules=None, popularity=None):
    return RecommendationArtifacts(
        source="test",
        rules={
            k: [Association(product_id=p, confidence=c) for p, c in v]
            for k, v in (rules or {}).items()
        },
        popularity=popularity or {},
    )


class TestLegacyConversion:
    def test_every_identifier_exists_in_the_catalogue(self, conversion, catalog):
        a = conversion.artifacts
        ids = set(a.rules) | set(a.popularity) | {r.product_id for v in a.rules.values() for r in v}
        assert ids <= {p.product_id for p in catalog.products}

    def test_the_packaged_chocolate_bar_is_dropped_not_merged(self, conversion):
        # D11: same name as the drink, another category, no catalogue record.
        assert any("Packaged Chocolate" in line for line in conversion.dropped)
        assert conversion.artifacts.popularity["dark-chocolate-drinking"] == 947  # not 947 + 22

    def test_a_misspelled_legacy_name_is_mapped(self, conversion):
        assert "caramel-syrup" in conversion.artifacts.rules  # legacy key "Carmel syrup"

    def test_rules_are_sorted_by_confidence(self, conversion):
        for rules in conversion.artifacts.rules.values():
            confidences = [r.confidence for r in rules]
            assert confidences == sorted(confidences, reverse=True)


class TestRecommender:
    def test_basket_ranks_by_best_confidence_and_skips_the_basket(self, catalog):
        r = Recommender(
            artifacts(
                {
                    "latte": [("croissant", 0.3), ("cappuccino", 0.5)],
                    "cappuccino": [("croissant", 0.6)],
                }
            ),
            catalog,
        )
        assert [p.product_id for p in r.for_basket(["latte", "cappuccino"])] == ["croissant"]

    def test_a_withdrawn_product_is_never_served(self, catalog):
        r = Recommender(artifacts({"latte": [("pumpkin-spice", 0.9), ("croissant", 0.2)]}), catalog)
        assert [p.product_id for p in r.for_basket(["latte"])] == ["croissant"]

    def test_at_most_two_per_category(self, catalog):
        syrups = ["caramel-syrup", "chocolate-syrup", "hazelnut-syrup", "sugar-free-vanilla-syrup"]
        r = Recommender(
            artifacts({"latte": [(s, 0.5 - i / 10) for i, s in enumerate(syrups)]}), catalog
        )
        assert len(r.for_basket(["latte"])) == 2

    def test_popular_within_one_category_is_not_capped_at_two(self, catalog, conversion):
        products = Recommender(conversion.artifacts, catalog).popular(["Bakery"], top_k=5)
        assert len(products) == 5
        assert {p.category for p in products} == {"Bakery"}
        assert products[0].product_id == "chocolate-croissant"  # 636 transactions, the top pastry

    def test_popular_overall_starts_with_the_best_seller(self, catalog, conversion):
        assert Recommender(conversion.artifacts, catalog).popular()[0].product_id == "cappuccino"

    def test_artefacts_round_trip_through_json(self, conversion, tmp_path):
        path = tmp_path / "recommendations.json"
        path.write_text(conversion.artifacts.model_dump_json(), encoding="utf-8")
        assert RecommendationArtifacts.load(path) == conversion.artifacts
