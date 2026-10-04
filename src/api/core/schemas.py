"""Data contracts shared by the pipelines and the API.

The catalogue is the single source of truth (ADR-004): the ingestion pipeline writes `Product`
documents and the API reads them back, so both sides validate against the same model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# EU list of major allergens, restricted to the ones this menu can contain.
Allergen = Literal["milk", "eggs", "gluten", "tree_nuts", "soy"]

# The menu currency: a display string, prices are not converted. Model costs stay in USD,
# the currency Azure bills the tokens in.
Currency = Literal["EUR"]
CURRENCY: Currency = "EUR"

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
    currency: Currency = CURRENCY
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


# ---- Agent decisions ---------------------------------------------------------------------------
# What each model call is allowed to return, enforced by Structured Outputs in strict mode: the
# model cannot emit a field that is not here, and cannot leave out one that is (debt D3).
# There is no "chain of thought" field, unlike the prototype: gpt-5.4-mini reasons internally, and
# asking it to write its reasoning out would only cost output tokens.

AgentName = Literal["details", "order", "recommendation"]


class GuardDecision(BaseModel):
    """Second safety layer, after Content Safety: is the message about the coffee shop?"""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: Literal["coffee_shop", "off_topic", "staff", "recipe", "unsafe"] = Field(
        description="Why. 'staff' and 'recipe' are shop-related yet out of scope."
    )


class SearchQuery(BaseModel):
    """A follow-up rewritten as a question that stands on its own, for retrieval."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(max_length=300)


class RouteDecision(BaseModel):
    """Which specialised agent answers this turn."""

    model_config = ConfigDict(extra="forbid")

    route: AgentName


class OrderLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(description="An id from the menu given in the prompt.")
    quantity: int = Field(ge=1, le=50)


class OrderExtraction(BaseModel):
    """What the order agent's model returns. No price anywhere: Python owns the money (ADR-003)."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderLineRequest] = Field(
        description="The whole basket after this message, not only what changed."
    )
    unrecognised: list[str] = Field(
        description="Items the customer asked for that are not on the menu."
    )
    ambiguous: list[str] = Field(
        description="Names matching several menu items, such as 'a scone', in the customer's words."
    )
    customer_done: bool = Field(description="True once the customer says they want nothing else.")
    suggestion_category: Category | None = Field(
        description="The kind of item the latest message asks a suggestion for, such as Coffee for "
        "'what coffee do you recommend with it?'; null when no suggestion is asked."
    )


class OrderMemory(BaseModel):
    """The order state the client sends back on every turn, under `memory.order` (ADR-005).
    Untrusted: only identifiers and quantities are kept, so a forged price has nowhere to go and
    every total is recomputed. Every assistant message carries it forward, whichever agent
    answered, so the basket survives questions in between and the history cap."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderLineRequest] = Field(default_factory=list, max_length=30)
    status: Literal["open", "closed"] = "open"


class RecommendationRequest(BaseModel):
    """Which recommendation source to query, and with what."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["basket", "popular", "popular_in_category"]
    product_ids: list[str] = Field(description="For 'basket': the products to complement.")
    categories: list[Category] = Field(description="For 'popular_in_category'.")


# ---- Conversation ------------------------------------------------------------------------------

MAX_MESSAGE_LENGTH = 2000
MAX_ASSISTANT_MESSAGE_LENGTH = 8000


class ChatMessage(BaseModel):
    """One message as the client sends it back on every turn (ADR-005: stateless API).

    `memory` travels with assistant messages. It comes from the client, so it is untrusted: agents
    re-validate what they read from it and never send it to the model as is.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_ASSISTANT_MESSAGE_LENGTH)
    memory: dict[str, Any] | None = None

    @model_validator(mode="after")
    def user_messages_stay_short(self) -> ChatMessage:
        # The assistant's own answers come back too (a long receipt), so only user text is capped
        # at the tighter limit: it is what reaches the models and Content Safety.
        if self.role == "user" and len(self.content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"a user message is limited to {MAX_MESSAGE_LENGTH} characters")
        return self
