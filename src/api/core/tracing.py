"""Tracing primitives shared by the core and the agents (roadmap 3.4, NFR8, OT4).

Only the OpenTelemetry *API* is used here. Without a configured SDK every call is a no-op, so the
core runs unchanged in tests and scripts; `src/api/app/telemetry.py` switches the export on.

The conversation id travels in OpenTelemetry baggage, set once per request. The processor below
copies it onto every span started during the turn, so no agent needs to know about it, and one
KQL filter on `conversation_id` returns the whole turn: guard, router, search, model calls.

Prompts and answers are never put on spans: they are personal data (NFR11), and the Cosmos
record already holds a masked copy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import baggage, context, trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

CONVERSATION_ID = "conversation_id"

tracer = trace.get_tracer("coffee_ai")


@contextmanager
def conversation(conversation_id: str) -> Iterator[None]:
    """Everything traced inside this block carries the conversation id."""
    token = context.attach(baggage.set_baggage(CONVERSATION_ID, conversation_id))
    try:
        yield
    finally:
        context.detach(token)


class ConversationSpanProcessor(SpanProcessor):
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        conversation_id = baggage.get_baggage(CONVERSATION_ID, parent_context)
        if conversation_id is not None:
            span.set_attribute(CONVERSATION_ID, str(conversation_id))

    def on_end(self, span: ReadableSpan) -> None:
        pass
