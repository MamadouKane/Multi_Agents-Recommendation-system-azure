"""Typed configuration, read from environment variables and, locally, from `.env`.

In Azure the Container App injects these variables (infra/modules/containerapp.bicep).
Locally, `make env` writes `.env` from the deployment outputs. The code is the same in both.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_chat_deployment: str = Field(
        default="gpt-5.4-mini", alias="AZURE_OPENAI_CHAT_DEPLOYMENT"
    )
    azure_openai_embedding_deployment: str = Field(
        default="text-embedding-3-small", alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    )

    azure_cosmos_endpoint: str = Field(default="", alias="AZURE_COSMOS_ENDPOINT")
    azure_cosmos_database: str = Field(default="coffeeshop", alias="AZURE_COSMOS_DATABASE")
    cosmos_products_container: str = Field(
        default="products", alias="AZURE_COSMOS_PRODUCTS_CONTAINER"
    )

    azure_search_endpoint: str = Field(default="", alias="AZURE_SEARCH_ENDPOINT")
    azure_search_index: str = Field(default="coffee-knowledge", alias="AZURE_SEARCH_INDEX_NAME")

    azure_storage_blob_endpoint: str = Field(default="", alias="AZURE_STORAGE_BLOB_ENDPOINT")
    images_container: str = Field(default="product-images", alias="AZURE_STORAGE_IMAGES_CONTAINER")

    cosmos_conversations_container: str = Field(
        default="conversations", alias="AZURE_COSMOS_CONVERSATIONS_CONTAINER"
    )
    # Content Safety is served by the same AI Services account as the models.
    azure_content_safety_endpoint: str = Field(default="", alias="AZURE_CONTENT_SAFETY_ENDPOINT")
    # The published model (`make publish-model`), in the model-artefacts container. Set it empty
    # to load RECOMMENDATIONS_PATH instead, such as the legacy conversion during development.
    recommendations_blob: str = Field(
        default="coffee-reco-apriori/current/recommendations.json", alias="RECOMMENDATIONS_BLOB"
    )
    models_container: str = Field(default="model-artefacts", alias="AZURE_STORAGE_MODELS_CONTAINER")
    recommendations_path: Path = Field(
        default=Path("data/processed/recommendations.json"), alias="RECOMMENDATIONS_PATH"
    )

    # USD per million tokens. OpenAI list prices on 2026-09-30, to confirm for Data Zone EU.
    chat_input_usd_per_million: Decimal = Field(
        default=Decimal("0.75"), alias="CHAT_INPUT_USD_PER_MILLION"
    )
    chat_output_usd_per_million: Decimal = Field(
        default=Decimal("4.50"), alias="CHAT_OUTPUT_USD_PER_MILLION"
    )

    # Names the Application Insights resource; writing requires an Entra ID identity.
    applicationinsights_connection_string: str = Field(
        default="", alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )

    # Comma separated. Never "*": the prototype's wildcard was debt D5.
    cors_allowed_origins: str = Field(default="http://localhost:3000", alias="CORS_ALLOWED_ORIGINS")
    # A turn is one user message and one answer (ADR-005).
    max_history_turns: int = Field(default=20, ge=1, le=50, alias="MAX_HISTORY_TURNS")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip() and o != "*"]

    @property
    def content_safety_endpoint(self) -> str:
        return self.azure_content_safety_endpoint or self.azure_openai_endpoint

    def require(self, *names: str) -> None:
        """Fail early, with the variable names, instead of on the first network call."""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            aliases = [type(self).model_fields[n].alias for n in missing]
            raise RuntimeError(f"missing configuration {aliases}: run `make env` first")


@lru_cache
def get_settings() -> Settings:
    return Settings()
