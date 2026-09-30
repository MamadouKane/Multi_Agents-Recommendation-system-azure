"""Turn persistence: PII masked, cost exact, a Cosmos failure never reaches the customer."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.api.core.conversations import ConversationStore, GuardRecord, Tokens, TurnRecord, scrub
from src.api.core.cost import ModelPrice
from src.api.core.llm import Usage


class TestScrub:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("mail me at jane.doe+cafe@example.co.uk", "mail me at [email]"),
            ("call +33 6 12 34 56 78 please", "call [phone] please"),
            ("my number is 06.12.34.56.78", "my number is [phone]"),
        ],
    )
    def test_personal_data_is_masked(self, text, expected):
        assert scrub(text) == expected

    @pytest.mark.parametrize(
        "text", ["2 lattes and 3 croissants", "Total: 11.25 USD", "we open at 8:00", "order 12345"]
    )
    def test_order_talk_is_left_alone(self, text):
        assert scrub(text) == text


def test_cost_is_exact_to_the_micro_dollar():
    price = ModelPrice(Decimal("0.75"), Decimal("4.50"))
    # 1200 x 0.75 / 1e6 + 150 x 4.50 / 1e6 = 0.0009 + 0.000675
    assert price.cost(Usage(1200, 150, 90)) == Decimal("0.001575")


def record(**overrides):
    fields = dict(
        id="conv_12345678:1",
        conversation_id="conv_12345678",
        turn=1,
        user_message="a latte",
        assistant_message="...",
        agent="order",
        route="order",
        guard=GuardRecord(allowed=True, reason="coffee_shop", layer="scope"),
        order=[],
        order_total=None,
        retrieved_doc_ids=[],
        upsell=[],
        tokens=Tokens(input=1, output=1, reasoning=0),
        cost_usd=Decimal("0.000001"),
        latency_ms=1000,
        model="gpt-5.4-mini",
        app_version="0.1.0",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    return TurnRecord(**(fields | overrides))


class FakeContainer:
    def __init__(self, error=None):
        self.error = error
        self.items = []

    def upsert_item(self, body, **kwargs):
        if self.error:
            raise self.error
        self.items.append(body)


def test_the_record_is_upserted_as_json():
    container = FakeContainer()
    ConversationStore(container).save(record())
    saved = container.items[0]
    assert saved["id"] == "conv_12345678:1"
    assert saved["cost_usd"] == "0.000001"  # Decimal as a string, never a float


def test_a_cosmos_failure_is_logged_not_raised(caplog):
    ConversationStore(FakeContainer(RuntimeError("403"))).save(record())
    assert "turn not persisted" in caplog.text
