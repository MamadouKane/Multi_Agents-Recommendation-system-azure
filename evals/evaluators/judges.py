"""LLM judges from azure-ai-evaluation (task 5.6): groundedness and relevance, scored 1 to 5.

They run on the project's own gpt-5.4-mini deployment, with Entra ID (no key). gpt-5.4-mini is
a reasoning model: `is_reasoning_model` makes the evaluators send `max_completion_tokens` and no
temperature, the two settings ADR-007 found the deployment rejects otherwise.

A judge is a model too: its scores are a measurement with noise, which is why the blocking
thresholds sit at 4.0 and not at the 5.0 a perfect answer would get.
"""

from __future__ import annotations

from typing import Any

from azure.ai.evaluation import GroundednessEvaluator, RelevanceEvaluator
from azure.identity import DefaultAzureCredential

from src.api.core.settings import Settings

API_VERSION = "2025-04-01-preview"


class Judges:
    def __init__(self, settings: Settings) -> None:
        config: Any = {
            "azure_endpoint": settings.azure_openai_endpoint,
            "azure_deployment": settings.azure_openai_chat_deployment,
            "api_version": API_VERSION,
        }
        credential = DefaultAzureCredential()
        self._groundedness = GroundednessEvaluator(
            config, credential=credential, is_reasoning_model=True
        )
        self._relevance = RelevanceEvaluator(config, credential=credential, is_reasoning_model=True)

    def score(self, query: str, context: str, response: str) -> dict[str, Any]:
        grounded = self._groundedness(query=query, context=context, response=response)
        relevant = self._relevance(query=query, response=response)
        return {
            "groundedness": float(grounded["groundedness"]),
            "groundedness_reason": grounded.get("groundedness_reason", ""),
            "relevance": float(relevant["relevance"]),
            "relevance_reason": relevant.get("relevance_reason", ""),
        }
