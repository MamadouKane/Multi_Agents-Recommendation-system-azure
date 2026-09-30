"""Telemetry: every span of a turn carries the conversation id, model calls follow the GenAI
conventions, prompts never leave the process, and the bill is re-checked against the catalogue."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.api.app.telemetry import configure_telemetry, order_is_consistent
from src.api.core.catalog import Catalog
from src.api.core.schemas import ChatMessage, RouteDecision
from src.api.core.search import Retriever, Strategy
from src.api.core.settings import Settings
from src.api.core.tracing import ConversationSpanProcessor, conversation
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from tests.unit.test_assistant import assistant
from tests.unit.test_chat_client import MESSAGES, chat, response
from tests.unit.test_search import FakeBackend, fake_embed, result

EXPORTER = InMemorySpanExporter()


def install_provider():
    provider = TracerProvider()
    provider.add_span_processor(ConversationSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(provider)  # global, once per process


install_provider()


@pytest.fixture
def spans():
    EXPORTER.clear()
    yield lambda: {s.name: s for s in EXPORTER.get_finished_spans()}


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


def test_every_span_of_a_turn_carries_the_conversation_id(spans, catalog):
    with conversation("conv_trace123"):
        assistant(catalog).respond([ChatMessage(role="user", content="when do you open?")])
    finished = spans()
    assert {"chat.turn", "guard", "router", "agent details"} <= set(finished)
    assert all(s.attributes["conversation_id"] == "conv_trace123" for s in finished.values())


def test_steps_are_children_of_the_turn(spans, catalog):
    assistant(catalog).respond([ChatMessage(role="user", content="when do you open?")])
    finished = spans()
    turn = finished["chat.turn"]
    assert finished["guard"].parent.span_id == turn.context.span_id
    assert turn.attributes["chat.route"] == "details"
    assert turn.attributes["gen_ai.usage.input_tokens"] == 330


def test_a_model_call_follows_the_genai_conventions(spans):
    client, _ = chat([response(parsed=RouteDecision(route="order"))])
    client.structured(MESSAGES, RouteDecision)
    span = spans()["chat gpt-5.4-mini"]
    attributes = span.attributes
    assert span.kind == trace.SpanKind.CLIENT
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.provider.name"] == "azure.ai.openai"
    assert attributes["gen_ai.usage.input_tokens"] == 120
    assert attributes["gen_ai.usage.output_tokens"] == 30
    assert attributes["app.llm.output_schema"] == "RouteDecision"


def test_no_prompt_or_answer_is_ever_put_on_a_span(spans, catalog):
    secret = "my card is 4970 1234"
    with conversation("conv_trace123"):
        assistant(catalog).respond([ChatMessage(role="user", content=secret)])
    client, _ = chat([response(parsed=RouteDecision(route="order"))])
    client.structured([{"role": "user", "content": secret}], RouteDecision)
    for span in spans().values():
        assert all(secret not in str(value) for value in span.attributes.values())


def test_the_search_span_measures_nfr2_and_the_gate(spans):
    backend = FakeBackend([result("a", 0.8, 1.2)])
    Retriever(backend, fake_embed).search("parking?", Strategy.VECTOR_GATED)
    attributes = spans()["search.query"].attributes
    assert attributes["search.abstained"] is True
    assert attributes["search.best_reranker_score"] == 1.2
    assert "search.search_ms" in attributes


class TestOrderConsistency:
    def trace(self, unit_price="4.75", total="9.50"):
        order = [
            {"product_id": "latte", "quantity": 2, "unit_price": unit_price, "line_total": total}
        ]
        return {"order": order, "order_total": total}

    def test_a_correct_bill_passes(self, catalog):
        assert order_is_consistent(self.trace(), catalog)

    def test_a_wrong_unit_price_is_caught(self, catalog):
        assert not order_is_consistent(self.trace(unit_price="4.00", total="8.00"), catalog)

    def test_a_wrong_total_is_caught(self, catalog):
        assert not order_is_consistent(self.trace(total="9.49"), catalog)


def test_without_a_connection_string_nothing_is_exported():
    assert configure_telemetry(Settings(APPLICATIONINSIGHTS_CONNECTION_STRING="")) is False
