"""Build the `coffee-knowledge` Azure AI Search index (roadmap tasks 2.4 to 2.6).

    catalogue + data/knowledge  ->  43 documents  ->  embeddings  ->  AI Search index

The index serves three retrieval strategies from one definition, which is what the day 2
ablation compares (ADR-002, superseded by ADR-008):

- **keyword**: BM25 on `title` and `content`, English analyzer (stemming, stop words)
- **vector**: HNSW on `content_vector`, cosine, 1536 dimensions
- **semantic**: the reranker reads `title` and `content` of the fused top results

Like the catalogue ingestion, it is keyless, idempotent (upsert by id) and synchronising
(documents no longer in the corpus are removed).

Run from the repository root:
    python -m src.data_pipelines.build_index --dry-run
    python -m src.data_pipelines.build_index
    python -m src.data_pipelines.build_index --recreate   # after a schema change
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable, Sequence
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)
from openai import OpenAI

from src.api.core.schemas import KnowledgeDoc
from src.api.core.settings import Settings, get_settings
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.knowledge import KNOWLEDGE_DIR, build_corpus

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small, verified by scripts/smoke_ai.py
VECTOR_PROFILE = "hnsw-cosine"
SEMANTIC_CONFIG = "default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def index_definition(name: str) -> SearchIndex:
    """The schema of docs/01-requirements.md section 6.3. Pure: no network call."""
    fields = [
        SearchField(name="id", type=SearchFieldDataType.STRING, key=True, filterable=True),
        SearchField(
            name="title",
            type=SearchFieldDataType.STRING,
            searchable=True,
            analyzer_name="en.microsoft",
        ),
        SearchField(
            name="content",
            type=SearchFieldDataType.STRING,
            searchable=True,
            analyzer_name="en.microsoft",
        ),
        SearchField(
            name="content_vector",
            type="Collection(Edm.Single)",
            searchable=True,
            # Never returned in results: 1536 floats per hit would dwarf the useful payload.
            retrievable=False,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        SearchField(
            name="doc_type", type=SearchFieldDataType.STRING, filterable=True, facetable=True
        ),
        SearchField(name="product_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchField(
            name="category", type=SearchFieldDataType.STRING, filterable=True, facetable=True
        ),
        SearchField(name="price", type=SearchFieldDataType.DOUBLE, filterable=True, sortable=True),
        SearchField(name="source", type=SearchFieldDataType.STRING),
    ]
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw",
                # m: links per node, ef_construction: candidates while building. Small m is plenty
                # for 43 documents; a large ef_construction costs nothing at this size.
                parameters=HnswParameters(
                    m=4, ef_construction=400, metric=VectorSearchAlgorithmMetric.COSINE
                ),
            )
        ],
        profiles=[VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="hnsw")],
    )
    semantic_search = SemanticSearch(
        default_configuration_name=SEMANTIC_CONFIG,
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ],
    )
    return SearchIndex(
        name=name, fields=fields, vector_search=vector_search, semantic_search=semantic_search
    )


def to_search_document(doc: KnowledgeDoc, vector: Sequence[float]) -> dict[str, Any]:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"{doc.id}: expected {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}")
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "content_vector": list(vector),
        "doc_type": doc.doc_type,
        "product_id": doc.product_id,
        "category": doc.category,
        # A filter and sort field only. The price the assistant states always comes from the
        # catalogue (ADR-003), so float precision here cannot reach a bill.
        "price": float(doc.price) if doc.price is not None else None,
        "source": doc.source,
    }


def batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def embed(client: OpenAI, deployment: str, texts: Sequence[str]) -> tuple[list[list[float]], int]:
    """Embed in batches, and return the vectors with the number of tokens billed."""
    vectors: list[list[float]] = []
    tokens = 0
    for batch in batched(texts, 64):
        response = client.embeddings.create(model=deployment, input=list(batch))
        vectors.extend(item.embedding for item in response.data)
        tokens += response.usage.total_tokens
    return vectors, tokens


def openai_client(settings: Settings, credential: DefaultAzureCredential) -> OpenAI:
    # A token lasts about an hour, far longer than this pipeline runs.
    token = credential.get_token(COGNITIVE_SCOPE).token
    return OpenAI(
        base_url=f"{settings.azure_openai_endpoint.rstrip('/')}/openai/v1/", api_key=token
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the coffee-knowledge search index")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.add_argument("--recreate", action="store_true", help="drop the index first")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.require("azure_search_endpoint", "azure_openai_endpoint")
    docs = build_corpus(build_catalog(read_jsonl(RAW_PATH)), KNOWLEDGE_DIR)
    print(f"Corpus: {len(docs)} documents")
    if args.dry_run:
        print(
            f"DRY RUN: would embed {len(docs)} documents and upload them to "
            f"'{settings.azure_search_index}' on {settings.azure_search_endpoint}"
        )
        return 0

    credential = DefaultAzureCredential()
    index_client = SearchIndexClient(settings.azure_search_endpoint, credential)
    if args.recreate:
        try:
            index_client.delete_index(settings.azure_search_index)
            print("Index dropped")
        except ResourceNotFoundError:
            pass
    index_client.create_or_update_index(index_definition(settings.azure_search_index))
    print(f"Index '{settings.azure_search_index}' is in place")

    started = time.perf_counter()
    vectors, tokens = embed(
        openai_client(settings, credential),
        settings.azure_openai_embedding_deployment,
        [d.content for d in docs],
    )
    print(
        f"Embedded {len(vectors)} documents, {tokens} tokens, "
        f"{int((time.perf_counter() - started) * 1000)} ms"
    )

    search_client = SearchClient(
        settings.azure_search_endpoint, settings.azure_search_index, credential
    )
    results = search_client.merge_or_upload_documents(
        [to_search_document(d, v) for d, v in zip(docs, vectors, strict=True)]
    )
    failed = [r.key for r in results if not r.succeeded]
    if failed:
        print(f"Upload failed for {failed}", file=sys.stderr)
        return 1

    keep = {d.id for d in docs}
    indexed = {r["id"] for r in search_client.search("*", select=["id"], top=1000)}
    stale = sorted(indexed - keep)
    if stale:
        search_client.delete_documents([{"id": doc_id} for doc_id in stale])
    print(f"Uploaded {len(docs)} documents, removed {len(stale)} stale ones {stale or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
