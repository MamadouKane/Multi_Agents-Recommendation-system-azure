"""The API catalogue: loading from Cosmos, menu rendering, and product resolution."""

import pytest

from src.api.core.catalog import Catalog, normalise
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


class FakeContainer:
    def __init__(self, documents):
        self.documents = documents

    def query_items(self, query, **kwargs):
        return iter(self.documents)


class TestLoading:
    def test_cosmos_system_fields_are_dropped(self, catalog):
        latte = catalog.get("latte").model_dump(mode="json")
        document = {**latte, "_rid": "x", "_self": "y", "_etag": "z", "_attachments": "a", "_ts": 1}
        loaded = Catalog.from_cosmos(FakeContainer([document]))
        assert loaded.get("latte").price == catalog.get("latte").price

    def test_an_empty_catalogue_fails_at_startup(self):
        with pytest.raises(RuntimeError, match="make ingest"):
            Catalog.from_cosmos(FakeContainer([]))

    def test_inactive_products_are_invisible(self, catalog):
        retired = catalog.get("latte").model_copy(update={"is_active": False})
        assert "latte" not in Catalog([retired])


class TestMenu:
    def test_the_menu_lists_every_product_with_its_identifier(self, catalog):
        menu = catalog.render_menu_for_prompt()
        assert len(menu.splitlines()) == 18
        assert "- latte: Latte (Coffee)" in menu

    def test_the_menu_shows_the_other_names_customers_use(self, catalog):
        menu = catalog.render_menu_for_prompt()
        assert "Dark chocolate (Drinking Chocolate), also called Hot chocolate" in menu

    def test_the_menu_carries_no_price(self, catalog):
        # ADR-003: a model that never sees a price cannot invent one.
        menu = catalog.render_menu_for_prompt()
        assert "$" not in menu and "EUR" not in menu and "USD" not in menu
        assert "4.75" not in menu


def test_normalise_folds_plurals_and_drops_fillers_and_quantities():
    assert normalise("Two Croissants, please!") == ["croissant"]
    assert normalise("3 lattes") == ["latte"]
    assert normalise("espresso") == ["espresso"]  # 'ss' ending is not a plural


class TestResolve:
    @pytest.mark.parametrize(
        ("text", "product_id"),
        [
            ("latte", "latte"),
            ("a latte", "latte"),
            ("lattes", "latte"),
            ("two croissants please", "croissant"),
            ("Carmel syrup", "caramel-syrup"),  # raw spelling kept as an alias
            ("hot chocolate", "dark-chocolate-drinking"),  # Kaggle name kept as an alias
            ("dark-chocolate-drinking", "dark-chocolate-drinking"),  # an identifier
            ("cappucino", "cappuccino"),  # a typo
            ("caramel", "caramel-syrup"),  # a partial name with a single match
            ("espresso", "espresso-shot"),
        ],
    )
    def test_found(self, catalog, text, product_id):
        resolution = catalog.resolve(text)
        assert resolution.found
        assert resolution.product.product_id == product_id

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("scone", {"cranberry-scone", "ginger-scone", "jumbo-savory-scone", "oatmeal-scone"}),
            ("hazelnut", {"hazelnut-biscotti", "hazelnut-syrup"}),
            ("biscotti", {"chocolate-chip-biscotti", "ginger-biscotti", "hazelnut-biscotti"}),
        ],
    )
    def test_ambiguous_names_return_the_candidates_instead_of_a_guess(
        self, catalog, text, expected
    ):
        resolution = catalog.resolve(text)
        assert resolution.status == "ambiguous"
        assert {p.product_id for p in resolution.candidates} == expected

    @pytest.mark.parametrize("text", ["matcha latte", "pizza", "oat milk flat white", "", "   "])
    def test_off_menu_items_are_unknown(self, catalog, text):
        # "matcha latte" scores 90 against "latte" with a partial matcher: accepting it would bill
        # a latte for a drink the shop does not sell.
        assert catalog.resolve(text).status == "unknown"
