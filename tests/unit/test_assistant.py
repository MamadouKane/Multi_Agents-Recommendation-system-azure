"""Orchestrator: refusals keep the order, state is carried forward, the upsell happens once."""

import pytest

from src.api.agents.assistant import Assistant
from src.api.agents.base import REFUSAL, AgentReply
from src.api.agents.guard import GuardOutcome
from src.api.agents.router import RouteOutcome
from src.api.core.catalog import Catalog
from src.api.core.llm import LLMContentFilterError, Usage
from src.api.core.recommender import Association, RecommendationArtifacts, Recommender
from src.api.core.schemas import ChatMessage
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl

LATTE = {"items": [{"product_id": "latte", "quantity": 1}], "status": "open"}


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


class FakeGuard:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def check(self, messages):
        return GuardOutcome(
            self.allowed, "coffee_shop" if self.allowed else "off_topic", "scope", Usage(10, 1, 0)
        )


class FakeRouter:
    def __init__(self, route):
        self.route_name = route

    def route(self, messages):
        return RouteOutcome(self.route_name, Usage(20, 2, 0), 100)


class FakeAgent:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error

    def answer(self, messages):
        if self.error:
            raise self.error
        return self.reply


def assistant(catalog, route="details", reply=None, allowed=True, error=None):
    reply = reply or AgentReply("details", "We open at 8.", usage=Usage(300, 30, 0))
    artifacts = RecommendationArtifacts(
        source="test",
        rules={
            "latte": [
                Association(product_id="cappuccino", confidence=0.6),
                Association(product_id="croissant", confidence=0.4),
            ]
        },
        popularity={},
    )
    return Assistant(
        FakeGuard(allowed),
        FakeRouter(route),
        {route: FakeAgent(reply, error)},
        Recommender(artifacts, catalog),
    )


def user(text):
    return ChatMessage(role="user", content=text)


def assistant_msg(memory):
    return ChatMessage(role="assistant", content="...", memory=memory)


def test_a_refusal_keeps_the_order_in_progress(catalog):
    history = [user("a latte"), assistant_msg({"order": LATTE}), user("who won the cup?")]
    turn = assistant(catalog, allowed=False).respond(history)
    assert turn.content == REFUSAL
    assert turn.agent == "guard"
    assert turn.memory["order"]["items"] == LATTE["items"]


def test_a_details_answer_carries_the_order_forward(catalog):
    history = [user("a latte"), assistant_msg({"order": LATTE}), user("do you have wifi?")]
    turn = assistant(catalog).respond(history)
    assert turn.memory["agent"] == "details"
    assert turn.memory["order"]["items"] == LATTE["items"]


def test_usage_adds_every_model_call_of_the_turn(catalog):
    turn = assistant(catalog).respond([user("when do you open?")])
    assert turn.usage.input_tokens == 330  # guard 10 + router 20 + agent 300


def test_the_deployment_filter_on_an_agent_is_a_refusal_not_a_500(catalog):
    turn = assistant(catalog, error=LLMContentFilterError("filtered")).respond([user("...")])
    assert turn.content == REFUSAL
    assert turn.guard.layer == "model_filter"


class TestUpsell:
    def order_reply(self, awaiting_choice=False):
        return AgentReply(
            "order",
            "Here is your order so far.\nWould you like anything else?",
            memory={"order": LATTE},
            trace={"awaiting_choice": awaiting_choice},
        )

    def test_offered_after_the_first_items(self, catalog):
        turn = assistant(catalog, "order", self.order_reply()).respond([user("a latte")])
        assert "Customers often add Croissant to this order" in turn.content
        assert turn.memory["upsell_offered"] is True

    def test_suggests_a_complement_not_a_substitute(self, catalog):
        # Cappuccino has the best confidence, but the basket already holds a coffee.
        turn = assistant(catalog, "order", self.order_reply()).respond([user("a latte")])
        assert "Cappuccino" not in turn.content

    def test_asks_one_question_not_two(self, catalog):
        turn = assistant(catalog, "order", self.order_reply()).respond([user("a latte")])
        assert "Would you like anything else?" not in turn.content
        assert turn.content.count("?") == 1

    def test_offered_once_per_conversation(self, catalog):
        history = [
            user("a latte"),
            assistant_msg({"order": LATTE, "upsell_offered": True}),
            user("and another latte"),
        ]
        turn = assistant(catalog, "order", self.order_reply()).respond(history)
        assert "Customers often add" not in turn.content

    def test_never_on_top_of_a_pending_question(self, catalog):
        reply = self.order_reply(awaiting_choice=True)
        turn = assistant(catalog, "order", reply).respond([user("a latte and a scone")])
        assert "Customers often add" not in turn.content
        assert turn.memory["upsell_offered"] is False


def test_an_order_turn_asking_for_a_coffee_gets_coffees_not_the_generic_upsell(catalog):
    reply = AgentReply(
        "order",
        "Here is your order so far.\nWould you like anything else?",
        memory={"order": LATTE},
        trace={"awaiting_choice": False, "suggestion_category": "Coffee"},
    )
    turn = assistant(catalog, "order", reply).respond([user("a latte, which other coffee?")])
    # The rules for a latte rank a cappuccino then a croissant: only the coffee is offered.
    assert "For a coffee, customers often choose Cappuccino with this order" in turn.content
    assert "Croissant" not in turn.content
    assert turn.memory["upsell_offered"] is True
