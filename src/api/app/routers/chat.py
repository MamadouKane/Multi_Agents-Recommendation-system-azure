"""POST /api/v1/chat: one turn of the conversation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Annotated

import httpx
import openai
from azure.core.exceptions import AzureError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from opentelemetry import trace

from src.api.agents.assistant import Turn
from src.api.app.contracts import ChatRequest, ChatResponse
from src.api.app.dependencies import get_services
from src.api.app.services import Services
from src.api.app.telemetry import record_turn
from src.api.core.conversations import GuardRecord, Tokens, TurnRecord, now, scrub
from src.api.core.llm import LLMError
from src.api.core.schemas import ChatMessage
from src.api.core.tracing import CONVERSATION_ID, conversation

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

# A dependency failing (model, search, safety) is a 503: the request was fine, try again later.
UPSTREAM_ERRORS = (LLMError, openai.OpenAIError, AzureError, httpx.HTTPError)
UNAVAILABLE = "The assistant is temporarily unavailable. Please try again in a moment."


@router.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    background: BackgroundTasks,
    services: Annotated[Services, Depends(get_services)],
) -> ChatResponse:
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="the last message must come from the user")

    conversation_id = body.conversation_id or new_conversation_id()
    history = cap_history(body.messages, services.settings.max_history_turns)
    # The request span already exists: tag it here, the processor tags every span below it.
    trace.get_current_span().set_attribute(CONVERSATION_ID, conversation_id)
    with conversation(conversation_id):
        try:
            turn = services.assistant.respond(history)
        except UPSTREAM_ERRORS:
            logger.exception("turn failed", extra={"conversation_id": conversation_id})
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from None

    record = to_record(conversation_id, body.messages, turn, services)
    record_turn(turn, record.cost_usd, services.catalog)
    # Runs after the response is sent: the customer never waits for Cosmos.
    background.add_task(services.conversations.save, record)

    return ChatResponse(
        conversation_id=conversation_id,
        agent=turn.agent,
        output=ChatMessage(role="assistant", content=turn.content, memory=turn.memory),
    )


def cap_history(messages: Sequence[ChatMessage], turns: int) -> list[ChatMessage]:
    """The last `turns` exchanges. Nothing is lost: every answer carries the order state."""
    return list(messages[-(2 * turns) :])


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex}"


def to_record(
    conversation_id: str, messages: Sequence[ChatMessage], turn: Turn, services: Services
) -> TurnRecord:
    number = sum(1 for m in messages if m.role == "user")
    trace = turn.trace
    return TurnRecord(
        id=f"{conversation_id}:{number}",
        conversation_id=conversation_id,
        turn=number,
        user_message=scrub(messages[-1].content),
        assistant_message=scrub(turn.content),
        agent=turn.agent,
        route=turn.route,
        guard=GuardRecord(
            allowed=turn.guard.allowed, reason=turn.guard.reason, layer=turn.guard.layer
        ),
        order=trace.get("order", []),
        order_total=trace.get("order_total"),
        retrieved_doc_ids=trace.get("documents", []),
        upsell=trace.get("upsell", []),
        tokens=Tokens(
            input=turn.usage.input_tokens,
            output=turn.usage.output_tokens,
            reasoning=turn.usage.reasoning_tokens,
        ),
        cost_usd=services.price.cost(turn.usage),
        latency_ms=turn.latency_ms,
        model=services.settings.azure_openai_chat_deployment,
        app_version=services.settings.app_version,
        created_at=now(),
    )
