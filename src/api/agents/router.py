"""Router agent: which specialised agent answers this turn.

Runs only on messages the Guard allowed. The legacy version asked for a JSON string with a
"chain of thought" field and parsed it by hand; here the decision is a validated `RouteDecision`,
and the reasoning happens inside the model (`reasoning_effort`), not in an output field to parse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.api.agents.base import recent_turns
from src.api.core.llm import DECISION, ChatClient, Usage
from src.api.core.schemas import AgentName, ChatMessage, RouteDecision

ROUTER_PROMPT = """\
You route messages sent to the assistant of Merry's Way, a coffee shop. Pick one agent:

- details: questions about the shop (location, opening hours, delivery, payment, wifi, events)
  or about menu items (what is on the menu, ingredients, allergens, prices).
- order: placing, changing, confirming or cancelling an order, including short answers to a
  question the assistant asked about the current order, such as "yes", "two please" or
  "that's all".
- recommendation: asking what to choose, what goes well with something, or what is popular.

When a message both asks a question and orders ("what is in a latte? I'll take one"), choose
order: the customer's action matters more than the question.
When a message mentions an allergy, an intolerance or a diet (no milk, nut-free, gluten-free,
vegan), choose details even if it asks for a suggestion: only details reads the allergen
information, and a recommendation that ignores it could harm the customer.
Judge the latest user message. Use the earlier turns to understand short follow-ups.
"""


@dataclass(frozen=True)
class RouteOutcome:
    route: AgentName
    usage: Usage
    latency_ms: int


class Router:
    def __init__(self, chat: ChatClient) -> None:
        self._chat = chat

    def route(self, messages: Sequence[ChatMessage]) -> RouteOutcome:
        result = self._chat.structured(
            [{"role": "system", "content": ROUTER_PROMPT}, *recent_turns(messages)],
            RouteDecision,
            DECISION,
        )
        return RouteOutcome(result.value.route, result.usage, result.latency_ms)
