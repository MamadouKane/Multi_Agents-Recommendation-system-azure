"""Azure OpenAI access with Entra ID tokens, refreshed before they expire.

Day 1 scripts fetch one token and use it for a single run. A long-running API cannot: a token
lasts about an hour, so the client is rebuilt whenever the cached token is close to expiry.
Day 3 extends this module with chat completions.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from azure.core.credentials import AccessToken, TokenCredential
from openai import OpenAI

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
REFRESH_MARGIN_SECONDS = 300


class AzureOpenAIClientFactory:
    """Hands out an OpenAI client whose bearer token is always valid for a few more minutes."""

    def __init__(self, endpoint: str, credential: TokenCredential) -> None:
        if not endpoint:
            raise ValueError("Azure OpenAI endpoint is empty: run `make env` first")
        self._base_url = f"{endpoint.rstrip('/')}/openai/v1/"
        self._credential = credential
        self._token: AccessToken | None = None
        self._client: OpenAI | None = None

    def client(self) -> OpenAI:
        now = time.time()
        if self._token is None or self._token.expires_on - now < REFRESH_MARGIN_SECONDS:
            self._token = self._credential.get_token(COGNITIVE_SCOPE)
            self._client = OpenAI(base_url=self._base_url, api_key=self._token.token)
        assert self._client is not None
        return self._client


class Embedder:
    """Turns text into a vector with the embedding deployment."""

    def __init__(self, factory: AzureOpenAIClientFactory, deployment: str) -> None:
        self._factory = factory
        self._deployment = deployment

    def __call__(self, text: str) -> list[float]:
        return self.many([text])[0]

    def many(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._factory.client().embeddings.create(
            model=self._deployment, input=list(texts)
        )
        return [item.embedding for item in response.data]
