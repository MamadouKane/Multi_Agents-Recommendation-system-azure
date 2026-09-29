"""Token handling for Azure OpenAI: a long-running process must never send an expired token."""

import time

import pytest
from azure.core.credentials import AccessToken

from src.api.core.llm import AzureOpenAIClientFactory


class FakeCredential:
    def __init__(self, lifetimes):
        self.lifetimes = list(lifetimes)
        self.calls = 0

    def get_token(self, *scopes, **kwargs):
        self.calls += 1
        return AccessToken(f"token-{self.calls}", int(time.time()) + self.lifetimes.pop(0))


def test_a_valid_token_is_reused():
    credential = FakeCredential([3600])
    factory = AzureOpenAIClientFactory("https://example.cognitiveservices.azure.com/", credential)
    first = factory.client()
    assert factory.client() is first
    assert credential.calls == 1


def test_a_token_close_to_expiry_is_refreshed():
    credential = FakeCredential([60, 3600])  # the first token has one minute left
    factory = AzureOpenAIClientFactory("https://example.cognitiveservices.azure.com/", credential)
    factory.client()
    factory.client()
    assert credential.calls == 2


def test_the_v1_endpoint_is_used():
    factory = AzureOpenAIClientFactory(
        "https://example.cognitiveservices.azure.com/", FakeCredential([3600])
    )
    assert (
        str(factory.client().base_url) == "https://example.cognitiveservices.azure.com/openai/v1/"
    )


def test_an_empty_endpoint_fails_early():
    with pytest.raises(ValueError, match="make env"):
        AzureOpenAIClientFactory("", FakeCredential([3600]))
