"""Ask one question to the three retrieval strategies and compare what comes back.

python scripts/search_demo.py "Does the chocolate chip biscotti contain nuts?"
python scripts/search_demo.py "which pastries do you have" --category Bakery
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from src.api.core.llm import AzureOpenAIClientFactory, Embedder
from src.api.core.search import Retriever, Strategy
from src.api.core.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--category", default=None)
    args = parser.parse_args()

    settings = get_settings()
    settings.require("azure_search_endpoint", "azure_openai_endpoint")
    credential = DefaultAzureCredential()
    embedder = Embedder(
        AzureOpenAIClientFactory(settings.azure_openai_endpoint, credential),
        settings.azure_openai_embedding_deployment,
    )
    backend = SearchClient(settings.azure_search_endpoint, settings.azure_search_index, credential)
    retriever = Retriever(backend, embedder)

    print(f"\nQ: {args.question}\n")
    for strategy in Strategy:
        result = retriever.search(args.question, strategy, top_k=args.top, category=args.category)
        print(f"== {strategy.value:16} {result.latency_ms:4} ms")
        for rank, hit in enumerate(result.hits, start=1):
            rerank = f"  rerank {hit.reranker_score:.2f}" if hit.reranker_score is not None else ""
            print(f"   {rank}. {hit.id:44} score {hit.score:.4f}{rerank}")
        if result.below_threshold:
            print(f"   ({result.below_threshold} hits dropped below the relevance threshold)")
        if result.found_nothing_relevant:
            print("   nothing relevant: the agent would answer 'I do not know'")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
