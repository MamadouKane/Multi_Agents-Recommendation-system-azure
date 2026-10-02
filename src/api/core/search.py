"""Retrieval over the `coffee-knowledge` index (roadmap task 2.7, ADR-008).

Five strategies share one entry point, so the day 2 ablation and the details agent run exactly
the same code:

    vector            the prototype's behaviour: nearest neighbours on the embedding
    hybrid            BM25 and vector in one query, merged by Reciprocal Rank Fusion
    hybrid_semantic   hybrid, then the semantic reranker, then a relevance threshold
    vector_semantic   vector only, then the semantic reranker, then a relevance threshold
    vector_gated      the ADR-008 decision, used by the details agent: vector ranking, and the
                      best reranker score decides whether anything relevant exists at all

Only the reranker score is on an absolute scale (0 to 4), so only the two semantic strategies
can say "nothing relevant was found" and let the agent answer "I do not know".
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, get_args

from azure.search.documents.models import VectorizedQuery
from opentelemetry.trace import SpanKind

from src.api.core.schemas import Category, DocType
from src.api.core.tracing import tracer

Embed = Callable[[str], list[float]]


class Strategy(StrEnum):
    VECTOR = "vector"
    HYBRID = "hybrid"
    HYBRID_SEMANTIC = "hybrid_semantic"
    VECTOR_SEMANTIC = "vector_semantic"
    VECTOR_GATED = "vector_gated"

    @property
    def reranked(self) -> bool:
        return self in (Strategy.HYBRID_SEMANTIC, Strategy.VECTOR_SEMANTIC, Strategy.VECTOR_GATED)

    @property
    def uses_keywords(self) -> bool:
        return self in (Strategy.HYBRID, Strategy.HYBRID_SEMANTIC)


# Vector candidates handed to the fusion. Larger than top_k, otherwise RRF has little to merge.
VECTOR_CANDIDATES = 50
SEMANTIC_CONFIGURATION = "default"
SELECT = ["id", "doc_type", "title", "content", "product_id", "category", "source"]
# Seconds to wait before asking again when the semantic ranker skipped a query.
RERANK_RETRY_DELAYS = (0.3, 0.8)
DEFAULT_MIN_RERANKER_SCORE = (
    1.7  # ADR-008: between unanswerable (max 1.68) and answerable (min 1.77)
)


class SearchBackend(Protocol):
    """The part of azure.search.documents.SearchClient this module uses. Tests pass a fake."""

    def search(self, search_text: str | None, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Hit:
    id: str
    doc_type: str
    title: str
    content: str
    score: float
    reranker_score: float | None = None
    product_id: str | None = None
    category: str | None = None
    captions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Retrieval:
    query: str
    strategy: Strategy
    hits: list[Hit] = field(default_factory=list)
    # Every hit returned by AI Search, before the relevance threshold. The ablation re-applies
    # other thresholds to these without sending new queries.
    candidates: list[Hit] = field(default_factory=list)
    embed_ms: int = 0
    search_ms: int = 0
    below_threshold: int = 0
    # False when the semantic ranker was asked for but did not run (throttled): the gate then
    # had no score to decide on, which is not the same as "nothing relevant".
    reranked: bool = True
    attempts: int = 1

    @property
    def latency_ms(self) -> int:
        return self.embed_ms + self.search_ms

    @property
    def ids(self) -> list[str]:
        return [h.id for h in self.hits]

    @property
    def found_nothing_relevant(self) -> bool:
        return not self.hits

    @property
    def best_reranker_score(self) -> float:
        return max((h.reranker_score or 0.0 for h in self.candidates), default=0.0)


def odata_literal(value: str) -> str:
    """A string literal for an OData filter: single quotes are doubled, as the grammar requires."""
    return "'" + value.replace("'", "''") + "'"


def build_filter(
    doc_types: Sequence[DocType] | None = None, category: Category | None = None
) -> str | None:
    """Filters come from an agent, so indirectly from a user: validate, then escape."""
    clauses = []
    if doc_types:
        unknown = set(doc_types) - set(get_args(DocType))
        if unknown:
            raise ValueError(f"unknown doc_type {sorted(unknown)}")
        # search.in is faster than a chain of `or` and takes the whole list in one literal.
        clauses.append(f"search.in(doc_type, {odata_literal(','.join(doc_types))}, ',')")
    if category is not None:
        if category not in get_args(Category):
            raise ValueError(f"unknown category {category!r}")
        clauses.append(f"category eq {odata_literal(category)}")
    return " and ".join(clauses) or None


def build_search_kwargs(
    strategy: Strategy,
    query: str,
    vector: Sequence[float],
    top_k: int,
    odata_filter: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """What is sent to AI Search for each strategy. Pure, so the differences are testable."""
    vector_query = VectorizedQuery(
        vector=list(vector),
        # Plain vector search needs only top_k neighbours. Fusion and reranking need a wider pool.
        k_nearest_neighbors=top_k if strategy is Strategy.VECTOR else VECTOR_CANDIDATES,
        fields="content_vector",
    )
    kwargs: dict[str, Any] = {
        "vector_queries": [vector_query],
        # The gated strategy re-sorts by vector score, so it needs every candidate the reranker
        # saw, not only the reranker's own top_k.
        "top": VECTOR_CANDIDATES if strategy is Strategy.VECTOR_GATED else top_k,
        "select": SELECT,
        "filter": odata_filter,
    }
    if strategy.reranked:
        kwargs |= {
            "query_type": "semantic",
            "semantic_configuration_name": SEMANTIC_CONFIGURATION,
            "query_caption": "extractive",
        }
    if strategy in (Strategy.VECTOR_SEMANTIC, Strategy.VECTOR_GATED):
        # No search text, so no BM25 at all. The reranker still needs the question, which is
        # what semantic_query carries.
        kwargs["semantic_query"] = query
    # Search text is what switches BM25 on.
    return (query if strategy.uses_keywords else None), kwargs


def to_hit(result: dict[str, Any]) -> Hit:
    captions = tuple(
        c.text for c in (result.get("@search.captions") or []) if getattr(c, "text", None)
    )
    return Hit(
        id=result["id"],
        doc_type=result["doc_type"],
        title=result["title"],
        content=result["content"],
        score=float(result["@search.score"]),
        reranker_score=result.get("@search.reranker_score"),
        product_id=result.get("product_id"),
        category=result.get("category"),
        captions=captions,
    )


class Retriever:
    def __init__(
        self,
        backend: SearchBackend,
        embed: Embed,
        min_reranker_score: float = DEFAULT_MIN_RERANKER_SCORE,
    ) -> None:
        self._backend = backend
        self._embed = embed
        self._min_reranker_score = min_reranker_score

    def search(
        self,
        query: str,
        strategy: Strategy = Strategy.HYBRID_SEMANTIC,
        top_k: int = 5,
        doc_types: Sequence[DocType] | None = None,
        category: Category | None = None,
        vector: Sequence[float] | None = None,
    ) -> Retrieval:
        """`vector` lets a caller embed once and compare strategies on the very same input."""
        # The span NFR2 is measured on (p95 < 300 ms). The question itself is not recorded.
        with tracer.start_as_current_span("search.query", kind=SpanKind.CLIENT) as span:
            retrieval = self._search(query, strategy, top_k, doc_types, category, vector)
            span.set_attributes(
                {
                    "search.strategy": strategy.value,
                    "search.top_k": top_k,
                    "search.candidates": len(retrieval.candidates),
                    "search.hits": len(retrieval.hits),
                    "search.best_reranker_score": retrieval.best_reranker_score,
                    "search.abstained": retrieval.found_nothing_relevant,
                    "search.embed_ms": retrieval.embed_ms,
                    "search.search_ms": retrieval.search_ms,
                    "search.document_ids": retrieval.ids,
                    "search.reranked": retrieval.reranked,
                    "search.attempts": retrieval.attempts,
                }
            )
            return retrieval

    def _search(
        self,
        query: str,
        strategy: Strategy = Strategy.HYBRID_SEMANTIC,
        top_k: int = 5,
        doc_types: Sequence[DocType] | None = None,
        category: Category | None = None,
        vector: Sequence[float] | None = None,
    ) -> Retrieval:
        if not query.strip():
            raise ValueError("empty query")

        embed_ms = 0
        if vector is None:
            started = time.perf_counter()
            vector = self._embed(query)
            embed_ms = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        search_text, kwargs = build_search_kwargs(
            strategy, query, vector, top_k, build_filter(doc_types, category)
        )
        attempts = 0
        while True:
            attempts += 1
            candidates = [to_hit(r) for r in self._backend.search(search_text, **kwargs)]
            reranked = not strategy.reranked or not candidates or has_reranker_scores(candidates)
            if reranked or attempts > len(RERANK_RETRY_DELAYS):
                break
            # Measured on day 5: under concurrent queries the semantic ranker's free plan
            # silently returns results without reranking, about one query in three.
            time.sleep(RERANK_RETRY_DELAYS[attempts - 1])
        search_ms = int((time.perf_counter() - started) * 1000)

        hits = candidates
        if strategy is Strategy.VECTOR_GATED:
            hits = (
                gate_on_best_reranker(candidates, self._min_reranker_score, top_k)
                if reranked
                # No score to gate on: serve the vector ranking rather than a false "I do not
                # know". The details prompt still declines when the context lacks the answer.
                else sorted(candidates, key=lambda h: h.score, reverse=True)[:top_k]
            )
        elif strategy.reranked:
            hits = above_threshold(candidates, self._min_reranker_score)

        return Retrieval(
            query=query,
            strategy=strategy,
            hits=hits,
            candidates=candidates,
            embed_ms=embed_ms,
            search_ms=search_ms,
            below_threshold=len(candidates) - len(hits),
            reranked=reranked,
            attempts=attempts,
        )


def has_reranker_scores(hits: Sequence[Hit]) -> bool:
    return any(h.reranker_score is not None for h in hits)


def above_threshold(hits: Sequence[Hit], min_reranker_score: float) -> list[Hit]:
    return [h for h in hits if (h.reranker_score or 0.0) >= min_reranker_score]


def gate_on_best_reranker(hits: Sequence[Hit], min_reranker_score: float, top_k: int) -> list[Hit]:
    """ADR-008: all or nothing. The reranker only answers "is anything here relevant?"; when it
    says yes, the order is the vector order, which ranked better on the ablation (MRR 0.98)."""
    if max((h.reranker_score or 0.0 for h in hits), default=0.0) < min_reranker_score:
        return []
    return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
