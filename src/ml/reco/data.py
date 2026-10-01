"""From raw sales lines to baskets of catalogue products (debts D11 and D14).

A basket is one receipt. The legacy notebook keyed receipts by `transaction_id` and
`customer_id`, but `transaction_id` restarts every day in every outlet and half of the sales are
anonymous (`customer_id` 0), so its key merged unrelated receipts: "baskets" of up to 14 items
when a real receipt never holds more than 4 (D14). Here a receipt is keyed by date, outlet and
transaction, which was checked to always belong to a single customer.

Kaggle product ids map explicitly to catalogue identifiers. Sizes (Rg, Lg) are the same product.
Id 19, the packaged chocolate bar, has no catalogue record and is dropped (D11, 1.2 % of the
chocolate volume); the drink, ids 58 and 59, becomes `dark-chocolate-drinking`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RECEIPTS_FILE = "201904 sales reciepts.csv"

# Kaggle product.csv id -> catalogue product_id. Everything else (beans, teas, merchandise) is
# not sold through the assistant and is ignored.
KAGGLE_TO_PRODUCT: dict[int, str] = {
    37: "espresso-shot",
    38: "latte",
    39: "latte",
    40: "cappuccino",
    41: "cappuccino",
    58: "dark-chocolate-drinking",
    59: "dark-chocolate-drinking",
    63: "caramel-syrup",
    64: "hazelnut-syrup",
    65: "sugar-free-vanilla-syrup",
    84: "chocolate-syrup",
    69: "hazelnut-biscotti",
    70: "cranberry-scone",
    71: "chocolate-croissant",
    72: "ginger-scone",
    73: "almond-croissant",
    74: "ginger-biscotti",
    75: "croissant",
    76: "chocolate-chip-biscotti",
    77: "oatmeal-scone",
    79: "jumbo-savory-scone",
}
DROPPED_IDS = {19: "packaged dark chocolate bar, not in the catalogue (D11)"}

CATEGORIES: dict[str, str] = {
    "espresso-shot": "Coffee",
    "latte": "Coffee",
    "cappuccino": "Coffee",
    "dark-chocolate-drinking": "Drinking Chocolate",
    "caramel-syrup": "Flavours",
    "hazelnut-syrup": "Flavours",
    "sugar-free-vanilla-syrup": "Flavours",
    "chocolate-syrup": "Flavours",
    "hazelnut-biscotti": "Bakery",
    "cranberry-scone": "Bakery",
    "chocolate-croissant": "Bakery",
    "ginger-scone": "Bakery",
    "almond-croissant": "Bakery",
    "ginger-biscotti": "Bakery",
    "croissant": "Bakery",
    "chocolate-chip-biscotti": "Bakery",
    "oatmeal-scone": "Bakery",
    "jumbo-savory-scone": "Bakery",
}


@dataclass(frozen=True)
class Basket:
    date: str  # ISO date, so string order is time order
    items: frozenset[str]


def load_baskets(sales_dir: Path) -> list[Basket]:
    """Every receipt holding at least one catalogue product, in time order."""
    import pandas as pd  # only the loading step needs pandas

    sales = pd.read_csv(Path(sales_dir) / RECEIPTS_FILE)
    sales = sales[sales["product_id"].isin(KAGGLE_TO_PRODUCT)]
    sales = sales.assign(item=sales["product_id"].map(KAGGLE_TO_PRODUCT))
    grouped = sales.groupby(["transaction_date", "sales_outlet_id", "transaction_id"])["item"]
    baskets = [Basket(date=str(key[0]), items=frozenset(items)) for key, items in grouped]
    return sorted(baskets, key=lambda b: b.date)


def split_by_date(baskets: Iterable[Basket], start: str) -> tuple[list[Basket], list[Basket]]:
    """Before `start`, and from `start` on: the past trains, the future evaluates."""
    before: list[Basket] = []
    after: list[Basket] = []
    for basket in baskets:
        (before if basket.date < start else after).append(basket)
    return before, after


def popularity(baskets: Iterable[Basket]) -> dict[str, int]:
    """Number of receipts containing each product."""
    counts: Counter[str] = Counter()
    for basket in baskets:
        counts.update(basket.items)
    return dict(counts)
