"""The orchestrator: guard, route, answer, then carry the conversation state forward (ADR-001).

Hand-written on purpose: the flow is a fixed chain, so a framework would add a dependency and
hide the one thing worth reading. Three rules live here and nowhere else:

- A refusal, from any layer (Content Safety, the deployment's filter, the scope check), is the
  same polite answer, and the order in progress is kept.
- Every answer carries the order state forward in `memory`, whichever agent answered, so a basket
  survives questions in between and the history cap.
- The upsell from the prototype: once per conversation, after the first items are added, the
  association rules suggest what customers often add. Deterministic, no model call.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.api.agents.base import REFUSAL, AgentReply
from src.api.agents.guard import Guard, GuardOutcome
from src.api.agents.order import FOLLOW_UP, join, previous_order
from src.api.agents.router import Router
from src.api.core.llm import LLMContentFilterError, Usage
from src.api.core.recommender import Recommender
from src.api.core.schemas import AgentName, ChatMessage

UPSELL_ITEMS = 2


class Agent(Protocol):
    def answer(self, messages: Sequence[ChatMessage]) -> AgentReply: ...


@dataclass(frozen=True)
class Turn:
    content: str
    memory: dict[str, Any]
    agent: str
    guard: GuardOutcome
    route: AgentName | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    trace: dict[str, Any] = field(default_factory=dict)


class Assistant:
    def __init__(
        self,
        guard: Guard,
        router: Router,
        agents: Mapping[AgentName, Agent],
        recommender: Recommender,
    ) -> None:
        self._guard = guard
        self._router = router
        self._agents = agents
        self._recommender = recommender

    def respond(self, messages: Sequence[ChatMessage]) -> Turn:
        started = time.perf_counter()
        order, _ = previous_order(messages)
        carried = {
            "order": order.model_dump(mode="json"),
            "upsell_offered": upsell_offered(messages),
        }

        guard = self._guard.check(messages)
        if not guard.allowed:
            return self.refuse(carried, guard, started)

        try:
            route = self._router.route(messages)
            reply = self._agents[route.route].answer(messages)
        except LLMContentFilterError:
            # The deployment's filter can also fire after the guard, on the router or an agent.
            blocked = GuardOutcome(False, "unsafe", "model_filter")
            return self.refuse(carried, blocked, started, guard.usage)

        memory = {**carried, **reply.memory, "agent": reply.agent}
        content = reply.content
        upsell: list[str] = []
        if self.should_upsell(reply, memory):
            basket = [item["product_id"] for item in memory["order"]["items"]]
            suggested = self._recommender.for_basket(basket, UPSELL_ITEMS, complements_only=True)
            upsell = [p.name for p in suggested]
            if upsell:
                # One question, not two: the suggestion replaces "anything else?".
                content = content.removesuffix(FOLLOW_UP).rstrip()
                content += (
                    f"\n\nCustomers often add {join(upsell, 'or')} to this order. "
                    "Would you like one, or anything else?"
                )
                memory["upsell_offered"] = True

        return Turn(
            content=content,
            memory=memory,
            agent=reply.agent,
            guard=guard,
            route=route.route,
            usage=guard.usage + route.usage + reply.usage,
            latency_ms=elapsed(started),
            trace=reply.trace | {"upsell": upsell},
        )

    def refuse(
        self,
        carried: dict[str, Any],
        guard: GuardOutcome,
        started: float,
        usage: Usage | None = None,
    ) -> Turn:
        return Turn(
            content=REFUSAL,
            memory={**carried, "agent": "guard"},
            agent="guard",
            guard=guard,
            usage=usage or guard.usage,
            latency_ms=elapsed(started),
        )

    @staticmethod
    def should_upsell(reply: AgentReply, memory: dict[str, Any]) -> bool:
        return (
            reply.agent == "order"
            and not memory["upsell_offered"]
            and memory["order"]["status"] == "open"
            and bool(memory["order"]["items"])
            # Never on top of a question: "which scone?" must be answered first.
            and not reply.trace.get("awaiting_choice", False)
        )


def upsell_offered(messages: Sequence[ChatMessage]) -> bool:
    for message in reversed(messages):
        if message.role == "assistant" and message.memory is not None:
            return message.memory.get("upsell_offered") is True
    return False


def elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
