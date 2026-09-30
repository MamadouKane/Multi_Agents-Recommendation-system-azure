"""Order totals: business objective OM4 tolerates zero error, so the edge cases matter most."""

from decimal import Decimal

import pytest

from src.api.core.catalog import Catalog
from src.api.core.pricing import price_order
from src.api.core.schemas import OrderLineRequest
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


def line(product_id, quantity=1):
    return OrderLineRequest(product_id=product_id, quantity=quantity)


def test_acceptance_example_latte_and_croissant_is_exactly_eight(catalog):
    # User story US1: 4.75 + 3.25 = 8.00, computed here, never by the model.
    order = price_order([line("latte"), line("croissant")], catalog)
    assert order.total == Decimal("8.00")
    assert str(order.total) == "8.00"


def test_quantities_multiply_the_catalogue_price(catalog):
    order = price_order([line("espresso-shot", 3)], catalog)
    assert order.lines[0].unit_price == Decimal("2.00")
    assert order.lines[0].line_total == Decimal("6.00")


def test_the_same_product_twice_becomes_one_line(catalog):
    order = price_order([line("latte"), line("latte", 2)], catalog)
    assert len(order.lines) == 1
    assert order.lines[0].quantity == 3
    assert order.total == Decimal("14.25")


def test_an_unknown_identifier_is_rejected_and_never_billed(catalog):
    order = price_order([line("latte"), line("matcha-latte")], catalog)
    assert order.rejected_ids == ("matcha-latte",)
    assert order.total == Decimal("4.75")


def test_an_order_of_unknown_items_only_is_empty_and_free(catalog):
    order = price_order([line("pizza")], catalog)
    assert order.is_empty
    assert order.total == Decimal("0.00")


def test_no_float_error_accumulates_on_a_large_basket(catalog):
    # With floats, 0.1 + 0.2 != 0.3. Decimal keeps every cent exact whatever the basket size.
    items = [line(p.product_id, 7) for p in catalog.products]
    order = price_order(items, catalog)
    expected = sum((p.price * 7 for p in catalog.products), Decimal("0"))
    assert order.total == expected
    assert order.total.as_tuple().exponent == -2


def test_a_price_with_three_decimals_is_refused_before_it_reaches_a_bill():
    # A two-decimal price times an integer quantity always has two decimals, so rounding never
    # triggers in price_order. What keeps every bill exact is refusing any other price upstream,
    # in the catalogue schema itself.
    from pydantic import ValidationError

    from src.api.core.schemas import Product

    with pytest.raises(ValidationError, match="decimal"):
        Product(
            id="odd",
            product_id="odd",
            name="Odd",
            category="Bakery",
            description="",
            ingredients=[],
            allergens=[],
            may_contain=[],
            price=Decimal("2.675"),
            rating=4.0,
            image_file="odd.jpg",
        )


def test_the_receipt_states_the_computed_numbers(catalog):
    receipt = price_order([line("latte"), line("croissant", 2)], catalog).receipt()
    assert "1 x Latte at 4.75 = 4.75 USD" in receipt
    assert "2 x Croissant at 3.25 = 6.50 USD" in receipt
    assert receipt.endswith("Total: 11.25 USD")
