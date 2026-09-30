"""Router agent, with a fake chat client: the routing quality itself is measured in evals/."""

import pytest
from pydantic import ValidationError

from src.api.agents.router import Router
from src.api.core.llm import DECISION, ChatResult, Usage
from src.api.core.schemas import ChatMessage, RouteDecision


class FakeChat:
    def __init__(self, route):
        self.route = route
        self.calls = []

    def structured(self, messages, schema, sampling):
        self.calls.append((messages, schema, sampling))
        return ChatResult(RouteDecision(route=self.route), Usage(80, 5, 3), 250, "gpt-5.4-mini")


def conversation(*contents):
    roles = ["user", "assistant"]
    return [ChatMessage(role=roles[i % 2], content=c) for i, c in enumerate(contents)]


def test_returns_the_route_with_its_cost():
    outcome = Router(FakeChat("order")).route(conversation("A latte please"))
    assert outcome.route == "order"
    assert outcome.usage.input_tokens == 80
    assert outcome.latency_ms == 250


def test_asks_for_a_structured_decision_on_the_fast_path():
    chat = FakeChat("details")
    Router(chat).route(conversation("Where are you?"))
    _, schema, sampling = chat.calls[0]
    assert schema is RouteDecision
    assert sampling is DECISION


def test_sees_the_prompt_then_the_last_three_turns():
    chat = FakeChat("order")
    Router(chat).route(conversation("hi", "Hello!", "A latte", "Anything else?", "that's all"))
    messages = chat.calls[0][0]
    assert messages[0]["role"] == "system"
    assert [m["content"] for m in messages[1:]] == ["A latte", "Anything else?", "that's all"]


def test_an_unknown_agent_cannot_be_returned():
    # The legacy router could answer any string; the schema only accepts the three agents.
    with pytest.raises(ValidationError):
        RouteDecision(route="classification_agent")
