"""Shared by every agent: the reply shape, the standard refusal, and what history a model sees."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from src.api.core.llm import Message, Usage
from src.api.core.schemas import ChatMessage

REFUSAL = "Sorry, I can't help with that. Can I help you with your order?"


@dataclass(frozen=True)
class AgentReply:
    agent: str
    content: str
    # Sent back to the client, which returns it on the next turn (ADR-005).
    memory: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    # For tracing only, never sent to the client: retrieved documents, guard reason, route.
    trace: dict[str, Any] = field(default_factory=dict)


def recent_turns(messages: Sequence[ChatMessage], turns: int = 3) -> list[Message]:
    """The last messages as a model sees them: role and content only.

    `memory` is left out on purpose. It comes from the client, so anything a model should know from
    it is extracted, validated and written into the prompt by the agent itself.
    """
    return [{"role": m.role, "content": m.content} for m in messages[-turns:]]


def last_user_message(messages: Sequence[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    raise ValueError("the conversation has no user message")
