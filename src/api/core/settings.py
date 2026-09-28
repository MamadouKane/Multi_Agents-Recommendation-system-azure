"""Typed configuration, read from environment variables and, locally, from `.env`.

In Azure the Container App injects these variables (infra/modules/containerapp.bicep).
Locally, `make env` writes `.env` from the deployment outputs. The code is the same in both.
"""

from __future__ import annotations

from functools import lru_cache

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

    def require(self, *names: str) -> None:
        """Fail early, with the variable names, instead of on the first network call."""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            aliases = [type(self).model_fields[n].alias for n in missing]
            raise RuntimeError(f"missing configuration {aliases}: run `make env` first")


@lru_cache
def get_settings() -> Settings:
    return Settings()
