"""ChatClient: sampling rule, retries, refusals and truncation, with a fake OpenAI client."""

from types import SimpleNamespace

import httpx
import openai
import pytest
from tenacity import wait_none

from src.api.core.llm import (
    DECISION,
    PROSE,
    ChatClient,
    LLMContentFilterError,
    LLMRefusalError,
    LLMTruncatedError,
    Sampling,
)
from src.api.core.schemas import RouteDecision

REQUEST = httpx.Request("POST", "https://example.cognitiveservices.azure.com/openai/v1/chat")


def http_error(cls, status, body=None):
    return cls("boom", response=httpx.Response(status, request=REQUEST), body=body)


def response(parsed=None, content=None, refusal=None, finish_reason="stop"):
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
    )
    message = SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-5.4-mini")


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def _next(self, kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def parse(self, **kwargs):
        return self._next(kwargs)

    def create(self, **kwargs):
        return self._next(kwargs)


class FakeFactory:
    def __init__(self, outcomes):
        self.completions = FakeCompletions(outcomes)

    def client(self):
        return SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


def chat(outcomes, attempts=3):
    factory = FakeFactory(outcomes)
    return ChatClient(factory, "gpt-5.4-mini", max_attempts=attempts, wait=wait_none()), factory


MESSAGES = [{"role": "user", "content": "I want a latte"}]


class TestSampling:
    def test_reasoning_effort_and_temperature_cannot_be_combined(self):
        # Measured on the model in ADR-007: the combination is rejected with a 400.
        with pytest.raises(ValueError, match="ADR-007"):
            Sampling(reasoning_effort="minimal", temperature=0)

    def test_decision_uses_reasoning_effort_only(self):
        assert DECISION.kwargs() == {"max_completion_tokens": 1000, "reasoning_effort": "minimal"}

    def test_prose_uses_temperature_only(self):
        assert PROSE.kwargs() == {"max_completion_tokens": 1500, "temperature": 0.0}

    def test_max_tokens_is_never_sent(self):
        # The model rejects the legacy name: max_completion_tokens is the only one used.
        assert "max_tokens" not in DECISION.kwargs() and "max_tokens" not in PROSE.kwargs()


class TestStructured:
    def test_returns_the_parsed_decision_and_the_usage(self):
        client, factory = chat([response(parsed=RouteDecision(route="order"))])
        result = client.structured(MESSAGES, RouteDecision)
        assert result.value.route == "order"
        assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 30)
        assert result.usage.reasoning_tokens == 12
        assert factory.completions.calls[0]["response_format"] is RouteDecision

    def test_a_refusal_is_an_explicit_error(self):
        client, _ = chat([response(refusal="I cannot help with that.")])
        with pytest.raises(LLMRefusalError, match="cannot help"):
            client.structured(MESSAGES, RouteDecision)

    def test_truncation_is_an_explicit_error(self):
        truncated = openai.LengthFinishReasonError(completion=response())
        client, _ = chat([truncated])
        with pytest.raises(LLMTruncatedError):
            client.structured(MESSAGES, RouteDecision)


class TestRetries:
    def test_a_rate_limit_is_retried(self):
        client, factory = chat(
            [
                http_error(openai.RateLimitError, 429),
                response(parsed=RouteDecision(route="details")),
            ]
        )
        assert client.structured(MESSAGES, RouteDecision).value.route == "details"
        assert len(factory.completions.calls) == 2

    def test_a_server_error_is_retried(self):
        client, factory = chat(
            [http_error(openai.InternalServerError, 503), response(content="Hello")]
        )
        assert client.text(MESSAGES).text == "Hello"
        assert len(factory.completions.calls) == 2

    def test_a_bad_request_is_not_retried(self):
        # A 400 will fail the same way every time: retrying only adds latency and cost.
        client, factory = chat([http_error(openai.BadRequestError, 400)])
        with pytest.raises(openai.BadRequestError):
            client.structured(MESSAGES, RouteDecision)
        assert len(factory.completions.calls) == 1

    def test_retries_stop_after_the_last_attempt(self):
        client, factory = chat([http_error(openai.RateLimitError, 429)] * 3, attempts=3)
        with pytest.raises(openai.RateLimitError):
            client.structured(MESSAGES, RouteDecision)
        assert len(factory.completions.calls) == 3


class TestText:
    def test_returns_stripped_text(self):
        client, factory = chat([response(content="  A latte is espresso and steamed milk.  ")])
        result = client.text(MESSAGES)
        assert result.text == "A latte is espresso and steamed milk."
        assert factory.completions.calls[0]["temperature"] == 0.0

    def test_a_cut_off_answer_is_an_error_not_a_half_sentence(self):
        client, _ = chat([response(content="A latte is", finish_reason="length")])
        with pytest.raises(LLMTruncatedError):
            client.text(MESSAGES)


FILTERED = {"code": "content_filter", "message": "filtered", "param": "prompt"}


class TestContentFilter:
    def test_a_filtered_prompt_is_a_typed_refusal_not_a_crash(self):
        client, factory = chat([http_error(openai.BadRequestError, 400, FILTERED)])
        with pytest.raises(LLMContentFilterError):
            client.structured(MESSAGES, RouteDecision)
        assert len(factory.completions.calls) == 1  # a refusal is never retried

    def test_a_filtered_prompt_in_text_mode_too(self):
        client, _ = chat([http_error(openai.BadRequestError, 400, FILTERED)])
        with pytest.raises(LLMContentFilterError):
            client.text(MESSAGES)

    def test_a_filtered_answer_is_not_returned_half_written(self):
        client, _ = chat([response(content="Sure, here is", finish_reason="content_filter")])
        with pytest.raises(LLMContentFilterError):
            client.text(MESSAGES)
