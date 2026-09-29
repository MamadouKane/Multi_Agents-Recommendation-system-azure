"""Data contracts shared by the pipelines and the API.

The catalogue is the single source of truth (ADR-004): the ingestion pipeline writes `Product`
documents and the API reads them back, so both sides validate against the same model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# EU list of major allergens, restricted to the ones this menu can contain.
Allergen = Literal["milk", "eggs", "gluten", "tree_nuts", "soy"]

Category = Literal["Coffee", "Bakery", "Drinking Chocolate", "Flavours"]


class Product(BaseModel):
    """One catalogue entry, as stored in the Cosmos `products` container."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Cosmos document id, equal to product_id.")
    product_id: str = Field(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", description="Stable key, never a name."
    )
    name: str = Field(min_length=1, description="Display name shown to customers.")
    category: Category
    description: str
    ingredients: list[str]
    allergens: list[Allergen] = Field(description="Allergens the recipe certainly contains.")
    may_contain: list[Allergen] = Field(
        description="Allergens an ingredient usually carries but the recipe does not confirm."
    )
    price: Decimal = Field(
        gt=0, decimal_places=2, description="Unit price, the only price anywhere."
    )
    currency: Literal["USD"] = "USD"
    rating: float = Field(ge=0, le=5)
    image_file: str = Field(description="File name in the product-images blob container.")
    aliases: list[str] = Field(
        default_factory=list, description="Other spellings customers use, for fuzzy matching."
    )
    is_active: bool = True


DocType = Literal["product", "menu", "dietary", "about", "faq"]


class KnowledgeDoc(BaseModel):
    """One retrievable unit of the search index (schema in docs/01-requirements.md, section 6.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    doc_type: DocType
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    product_id: str | None = None
    category: Category | None = None
    price: Decimal | None = None
    source: str = Field(description="Where the text comes from, for citations and audits.")
