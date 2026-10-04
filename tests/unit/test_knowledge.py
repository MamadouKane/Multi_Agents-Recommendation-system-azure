"""Knowledge corpus: structure, and consistency between hand-written text and the catalogue."""

import re
from pathlib import Path

import pytest

from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.knowledge import (
    KNOWLEDGE_DIR,
    build_corpus,
    split_front_matter,
    strip_comments,
    summary,
)

PRICE_PATTERN = re.compile(r"\$|\bUSD\b|\bEUR\b|\d+\.\d{2}")


@pytest.fixture(scope="module")
def products():
    return build_catalog(read_jsonl(RAW_PATH))


@pytest.fixture(scope="module")
def corpus(products):
    return {d.id: d for d in build_corpus(products, KNOWLEDGE_DIR)}


def test_front_matter_is_parsed():
    meta, body = split_front_matter("---\nid: x\ntitle: A: B\n---\nHello")
    assert meta == {"id": "x", "title": "A: B"}
    assert body == "Hello"


def test_front_matter_is_mandatory():
    with pytest.raises(ValueError):
        split_front_matter("no header here")


def test_html_comments_are_removed():
    assert strip_comments("a <!-- hidden --> b") == "a  b"


def test_corpus_composition(corpus):
    counts = summary(corpus.values())
    assert counts["product"] == 18
    assert counts["menu"] == 5  # four categories plus the full menu
    assert counts["dietary"] == 4
    assert counts["about"] == 6
    assert counts["faq"] == 10
    assert 40 <= len(corpus) <= 50  # roadmap target: about 45, up from 20 in the prototype


def test_ids_follow_the_slug_pattern(corpus):
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", doc_id) for doc_id in corpus)


class TestHandWrittenTextNeverStatesAPrice:
    """ADR-003: a price exists in the catalogue only. Hand-written text must not carry one."""

    def test_faq_and_about(self, corpus):
        offenders = [
            d.id
            for d in corpus.values()
            if d.doc_type in {"faq", "about"} and PRICE_PATTERN.search(d.content)
        ]
        assert offenders == []


class TestFaqAgreesWithTheCatalogue:
    def test_no_bakery_item_is_gluten_free_as_the_faq_says(self, products):
        bakery = [p for p in products if p.category == "Bakery"]
        assert bakery and all("gluten" in p.allergens for p in bakery)

    def test_drinks_and_syrups_have_no_gluten_as_the_faq_says(self, products):
        others = [p for p in products if p.category != "Bakery"]
        assert all("gluten" not in {*p.allergens, *p.may_contain} for p in others)

    def test_no_plant_based_milk_is_sold_as_the_faq_says(self, products):
        plant_milks = ("oat milk", "almond milk", "soy milk", "coconut milk")
        ingredients = {i.lower() for p in products for i in p.ingredients}
        assert not ingredients & set(plant_milks)

    def test_about_text_does_not_promise_products_the_catalogue_lacks(self):
        # The raw about page mentions teas, cold brews, plant-based milk and gluten-free snacks,
        # none of which are sold. The curated version must not repeat those claims.
        curated = " ".join(
            p.read_text().lower() for p in Path(KNOWLEDGE_DIR).glob("*.md") if p.name != "faq.md"
        )
        for claim in ("cold brew", "tea", "plant-based", "gluten-free"):
            assert claim not in curated, claim


class TestDietaryDocuments:
    def test_nut_free_list_excludes_the_biscotti_that_hides_almonds(self, corpus):
        # Chocolate Chip Biscotti contains almonds although its name does not say so.
        text = corpus["dietary-tree-nuts-free"].content
        free_part = text.split("\n")[0]
        assert "Chocolate Chip Biscotti" not in free_part
        assert "Espresso shot" in free_part

    def test_possible_allergens_count_as_not_free(self, corpus):
        # Chocolate Croissant may contain soy and milk through its chocolate: not milk-free.
        free_part = corpus["dietary-milk-free"].content.split("\n")[0]
        assert "Chocolate Croissant" not in free_part

    def test_every_dietary_document_carries_the_cross_contact_warning(self, corpus):
        dietary = [d for d in corpus.values() if d.doc_type == "dietary"]
        assert all("same kitchen" in d.content for d in dietary)


def test_product_documents_carry_the_catalogue_price(corpus, products):
    latte = next(p for p in products if p.product_id == "latte")
    doc = corpus["product-latte"]
    assert doc.price == latte.price
    assert f"{latte.price} EUR" in doc.content


def test_aliases_are_indexed_so_keyword_search_matches_them(corpus):
    assert "Carmel syrup" in corpus["product-caramel-syrup"].content
