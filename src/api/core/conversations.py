"""One document per turn in the Cosmos `conversations` container (ADR-005, US6, OT4).

Written after the response is sent, so persistence never adds latency and never fails a turn.
Partition key `/conversation_id`: replaying a conversation is one partition read. Documents
expire after 90 days (container TTL).

NFR11: what the customer typed is stored, so e-mail addresses and phone numbers are masked first.
Memory is not stored as sent: only the validated order is, as priced by the order agent.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Nine digits or more, with the usual separators: long enough to leave "2 lattes" and "4.75" alone.
PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")


def scrub(text: str) -> str:
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text))


class Tokens(BaseModel):
    input: int
    output: int
    reasoning: int


class GuardRecord(BaseModel):
    allowed: bool
    reason: str
    layer: str


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    turn: int
    user_message: str
    assistant_message: str
    agent: str
    route: str | None
    guard: GuardRecord
    order: list[dict[str, Any]]
    order_total: str | None
    retrieved_doc_ids: list[str]
    upsell: list[str]
    tokens: Tokens
    cost_usd: Decimal
    latency_ms: int
    model: str
    app_version: str
    created_at: datetime


class CosmosWriter(Protocol):
    def upsert_item(self, body: dict[str, Any], **kwargs: Any) -> Any: ...


class ConversationStore:
    def __init__(self, container: CosmosWriter) -> None:
        self._container = container

    def save(self, record: TurnRecord) -> None:
        """Never raises: a lost trace is logged, a lost answer would be worse."""
        try:
            # upsert with a deterministic id: a retried request overwrites, never duplicates.
            self._container.upsert_item(record.model_dump(mode="json"))
        except Exception:
            logger.exception(
                "turn not persisted", extra={"conversation_id": record.conversation_id}
            )


def now() -> datetime:
    return datetime.now(UTC)
