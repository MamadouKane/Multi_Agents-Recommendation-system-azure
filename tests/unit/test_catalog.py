"""Catalogue enrichment: identifiers, prices and, above all, allergens (persona Marc, US2)."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.data_pipelines.catalog import (
    INGREDIENT_ALLERGENS,
    UnknownIngredientError,
    build_catalog,
    derive_allergens,
    enrich,
    read_jsonl,
    slugify,
)

RAW = Path("data/raw/products.jsonl")


def raw_record(**overrides):
    record = {
        "name": "Latte",
        "category": "Coffee",
        "description": " Smooth and creamy. ",
        "ingredients": ["Espresso", "Steamed Milk", "Milk Foam"],
        "price": 4.75,
        "rating": 4.8,
        "image_path": "Latte.jpg",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Latte", "latte"),
        ("Sugar Free Vanilla syrup", "sugar-free-vanilla-syrup"),
        ("Espresso shot", "espresso-shot"),
        ("  Jumbo   Savory Scone ", "jumbo-savory-scone"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_rejects_a_name_without_letters():
    with pytest.raises(ValueError):
        slugify("  --  ")


class TestDeriveAllergens:
    def test_certain_allergens_are_collected_and_sorted(self):
        contains, may = derive_allergens(["Flour", "Butter", "Eggs"])
        assert contains == ["eggs", "gluten", "milk"]
        assert may == []

    def test_almond_cream_is_tree_nuts_for_sure_and_maybe_dairy_and_eggs(self):
        contains, may = derive_allergens(["Almond Cream", "Flour"])
        assert contains == ["gluten", "tree_nuts"]
        assert may == ["eggs", "milk"]

    def test_an_allergen_already_certain_is_not_repeated_as_possible(self):
        # Chocolate chips may carry milk, but butter already makes milk certain.
        contains, may = derive_allergens(["Chocolate Chips", "Butter"])
        assert contains == ["milk"]
        assert may == ["soy"]

    def test_no_allergen(self):
        assert derive_allergens(["Espresso", "Water"]) == ([], [])

    def test_unknown_ingredient_stops_the_pipeline(self):
        # Silence would read as "no allergen", which is the dangerous answer.
        with pytest.raises(UnknownIngredientError, match="Pistachio Cream"):
            derive_allergens(["Flour", "Pistachio Cream"])


class TestEnrich:
    def test_price_is_an_exact_decimal(self):
        product = enrich(raw_record(price=4.75))
        assert product.price == Decimal("4.75")
        assert str(product.price) == "4.75"

    def test_description_is_trimmed(self):
        assert enrich(raw_record()).description == "Smooth and creamy."

    def test_dark_chocolate_is_the_drink(self):
        # Debt D11: the packaged bar is out of scope, the identifier says which product this is.
        product = enrich(raw_record(name="Dark chocolate", category="Drinking Chocolate"))
        assert product.product_id == "dark-chocolate-drinking"
        assert product.id == product.product_id

    def test_caramel_typo_is_fixed_and_kept_as_an_alias(self):
        product = enrich(
            raw_record(name="Carmel syrup", category="Flavours", ingredients=["Sugar", "Water"])
        )
        assert product.name == "Caramel syrup"
        assert product.product_id == "caramel-syrup"
        assert product.aliases == ["Carmel syrup"]

    def test_is_active_by_default(self):
        assert enrich(raw_record()).is_active is True


def test_duplicate_identifiers_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_catalog([raw_record(), raw_record()])


@pytest.fixture(scope="module")
def catalogue():
    return {p.product_id: p for p in build_catalog(read_jsonl(RAW))}


class TestRealCatalogue:
    """Runs on the actual raw file: these are the facts the assistant will state to customers."""

    def test_eighteen_products_with_unique_ids(self, catalogue):
        assert len(catalogue) == 18

    def test_every_raw_ingredient_is_classified(self):
        ingredients = {i for record in read_jsonl(RAW) for i in record["ingredients"]}
        assert ingredients <= INGREDIENT_ALLERGENS.keys()

    @pytest.mark.parametrize(
        "product_id", ["almond-croissant", "hazelnut-biscotti", "hazelnut-syrup"]
    )
    def test_nut_products_declare_tree_nuts(self, catalogue, product_id):
        assert "tree_nuts" in catalogue[product_id].allergens

    def test_latte_price_matches_the_acceptance_example(self, catalogue):
        # US1: one latte and one croissant must total exactly 8.00.
        assert catalogue["latte"].price + catalogue["croissant"].price == Decimal("8.00")

    def test_espresso_is_allergen_free(self, catalogue):
        assert catalogue["espresso-shot"].allergens == []
        assert catalogue["espresso-shot"].may_contain == []
