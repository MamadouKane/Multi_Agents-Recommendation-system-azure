"""Order totals, computed in Python from catalogue prices (ADR-003, business objective OM4).

The model returns identifiers and quantities. Everything that ends up on a bill is computed here:
unit price read from the catalogue, line total and order total in `Decimal`, rounded half up to
the cent. The model is then given these numbers to phrase, never to compute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from src.api.core.catalog import Catalog
from src.api.core.schemas import OrderLineRequest

CENT = Decimal("0.01")


@dataclass(frozen=True)
class PricedLine:
    product_id: str
    name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class PricedOrder:
    lines: tuple[PricedLine, ...]
    total: Decimal
    currency: str = "USD"
    # Identifiers the model produced that the catalogue does not know. Never priced, never billed.
    rejected_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def receipt(self) -> str:
        """The lines the answer must repeat, already computed. The model only phrases them."""
        rows = [
            f"{ln.quantity} x {ln.name} at {ln.unit_price} = {ln.line_total} {self.currency}"
            for ln in self.lines
        ]
        return "\n".join([*rows, f"Total: {self.total} {self.currency}"])


def price_order(items: Iterable[OrderLineRequest], catalog: Catalog) -> PricedOrder:
    """Merge duplicate lines, drop unknown identifiers, price the rest exactly."""
    quantities: dict[str, int] = {}
    rejected: list[str] = []
    for item in items:
        if item.product_id not in catalog:
            rejected.append(item.product_id)
            continue
        # "a latte" then "another latte" in the same basket is one line of two, not two lines.
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

    lines = []
    for product_id, quantity in quantities.items():
        product = catalog.get(product_id)
        assert product is not None  # checked above
        line_total = (product.price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
        lines.append(PricedLine(product_id, product.name, quantity, product.price, line_total))

    total = sum((line.line_total for line in lines), Decimal("0.00"))
    return PricedOrder(
        lines=tuple(lines),
        total=total.quantize(CENT, rounding=ROUND_HALF_UP),
        rejected_ids=tuple(sorted(set(rejected))),
    )
