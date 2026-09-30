"""The HTTP contract of `/api/v1`. It keeps the prototype's shape, `messages` in and one assistant
message with its `memory` out (ADR-005), and adds the `conversation_id` that makes replay possible.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema

from src.api.core.schemas import Allergen, Category, ChatMessage

# Accepted on the way in, then capped to MAX_HISTORY_TURNS before any agent runs. A request far
# above the cap is not a conversation, it is a payload: rejected outright.
MAX_REQUEST_MESSAGES = 200
CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_REQUEST_MESSAGES)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID_PATTERN)


class ChatResponse(BaseModel):
    conversation_id: str
    agent: str = Field(description="Which agent answered, or 'guard' for a refusal.")
    output: ChatMessage


# Money travels as a string with exactly two decimals: a JSON number is a float for most clients,
# and 4.75 as a float is not 4.75. The explicit schema also gives the docs a real example.
Price = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^\d+\.\d{2}$",
            "description": "Unit price, two decimals, in `currency`.",
            "examples": ["4.75"],
        }
    ),
]


class ProductOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "product_id": "latte",
                    "name": "Latte",
                    "category": "Coffee",
                    "description": "Smooth and creamy, our latte combines rich espresso with "
                    "velvety steamed milk.",
                    "ingredients": ["Espresso", "Steamed milk"],
                    "allergens": ["milk"],
                    "may_contain": [],
                    "price": "4.75",
                    "currency": "USD",
                    "rating": 4.7,
                    "image_url": "https://<api>/api/v1/products/latte/image",
                }
            ]
        }
    )

    product_id: str
    name: str
    category: Category
    description: str
    ingredients: list[str]
    allergens: list[Allergen]
    may_contain: list[Allergen]
    price: Price
    currency: Literal["USD"]
    rating: float
    image_url: str


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    products: int
