"""Everything the API needs, built once at startup (lifespan) and shared by every request.

Built here and nowhere else, so a test can hand the app fakes instead: `create_app(build=...)`.
Every client authenticates with Entra ID: `DefaultAzureCredential` is the managed identity in the
Container App and `az login` on a laptop. No key anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from azure.ai.contentsafety import ContentSafetyClient
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient

from src.api.agents.assistant import Assistant
from src.api.agents.details import Details
from src.api.agents.guard import Guard
from src.api.agents.order import Order
from src.api.agents.recommendation import Recommendation
from src.api.agents.router import Router
from src.api.core.catalog import Catalog
from src.api.core.content_safety import ContentSafetyGate
from src.api.core.conversations import ConversationStore
from src.api.core.cost import ModelPrice
from src.api.core.llm import AzureOpenAIClientFactory, BearerToken, ChatClient, Embedder
from src.api.core.recommender import RecommendationArtifacts, Recommender
from src.api.core.search import Retriever
from src.api.core.settings import Settings


class ImageStore(Protocol):
    def read(self, name: str) -> bytes: ...


class BlobImageStore:
    def __init__(self, service: BlobServiceClient, container: str) -> None:
        self._container = service.get_container_client(container)

    def read(self, name: str) -> bytes:
        data: Any = self._container.download_blob(name).readall()
        return bytes(data)


@dataclass(frozen=True)
class Services:
    settings: Settings
    assistant: Assistant
    catalog: Catalog
    images: ImageStore
    conversations: ConversationStore
    price: ModelPrice
    # Which registered model answers, for /health (task 4.11). None for a local file.
    recommender_version: str | None = None


@dataclass(frozen=True)
class Components:
    """Every part of the assistant, built once. The API wraps them in `Services`; the evaluation
    harness (`evals/run_eval.py`) uses them directly, so it measures the code that is served."""

    settings: Settings
    catalog: Catalog
    chat: ChatClient
    retriever: Retriever
    guard: Guard
    router: Router
    details: Details
    order: Order
    recommendation: Recommendation
    recommender: Recommender
    assistant: Assistant
    price: ModelPrice
    blob_service: BlobServiceClient
    database: Any


def build_components(settings: Settings) -> Components:
    settings.require(
        "azure_openai_endpoint",
        "azure_cosmos_endpoint",
        "azure_search_endpoint",
        "azure_storage_blob_endpoint",
    )
    credential = DefaultAzureCredential()

    factory = AzureOpenAIClientFactory(settings.azure_openai_endpoint, credential)
    chat = ChatClient(factory, settings.azure_openai_chat_deployment)
    embedder = Embedder(factory, settings.azure_openai_embedding_deployment)

    database = CosmosClient(settings.azure_cosmos_endpoint, credential).get_database_client(
        settings.azure_cosmos_database
    )
    catalog = Catalog.from_cosmos(database.get_container_client(settings.cosmos_products_container))

    safety_endpoint = settings.content_safety_endpoint
    safety = ContentSafetyGate(
        safety_endpoint,
        # The SDK waits up to 300 s by default; a customer should not.
        ContentSafetyClient(
            safety_endpoint, credential, connection_timeout=5, read_timeout=10, retry_total=1
        ),
        BearerToken(credential),
    )
    retriever = Retriever(
        SearchClient(settings.azure_search_endpoint, settings.azure_search_index, credential),
        embedder,
    )
    blob_service = BlobServiceClient(settings.azure_storage_blob_endpoint, credential)
    recommender = Recommender(load_recommendations(settings, blob_service), catalog)

    guard = Guard(safety, chat)
    router = Router(chat)
    details = Details(retriever, chat, catalog)
    order = Order(chat, catalog)
    recommendation = Recommendation(chat, catalog, recommender)
    assistant = Assistant(
        guard=guard,
        router=router,
        agents={"details": details, "order": order, "recommendation": recommendation},
        recommender=recommender,
    )
    price = ModelPrice(settings.chat_input_usd_per_million, settings.chat_output_usd_per_million)
    return Components(
        settings, catalog, chat, retriever, guard, router, details, order, recommendation,
        recommender, assistant, price, blob_service, database,
    )  # fmt: skip


def build_services(settings: Settings) -> Services:
    parts = build_components(settings)
    images = BlobImageStore(parts.blob_service, settings.images_container)
    conversations = ConversationStore(
        parts.database.get_container_client(settings.cosmos_conversations_container)
    )
    return Services(
        settings,
        parts.assistant,
        parts.catalog,
        images,
        conversations,
        parts.price,
        parts.recommender.version,
    )


def load_recommendations(
    settings: Settings, blob_service: BlobServiceClient
) -> RecommendationArtifacts:
    """The published model from Blob Storage, or a local file when no blob is configured."""
    if not settings.recommendations_blob:
        return RecommendationArtifacts.load(settings.recommendations_path)
    blob = blob_service.get_blob_client(settings.models_container, settings.recommendations_blob)
    data: Any = blob.download_blob().readall()
    return RecommendationArtifacts.from_json(bytes(data))
