"""Retrieval ablation (roadmap task 2.8, ADR-002).

Three configurations, each adding one component to the previous one:

    A. vector            embeddings only, the prototype's behaviour
    B. hybrid            + BM25, merged by Reciprocal Rank Fusion
    C. hybrid_semantic   + semantic reranker and relevance threshold
    D. vector_semantic   C without BM25: isolates the reranker from the keyword search

Every question is embedded once, and the three configurations receive that same vector, so the
only thing that differs between two columns is the component under test.

    python -m evals.retrieval_ablation            # writes evals/retrieval_ablation.md
    python -m evals.retrieval_ablation --runs 3   # repeat searches for steadier latencies
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from evals.metrics import mean, percentile, recall_at_k, reciprocal_rank
from src.api.core.llm import AzureOpenAIClientFactory, Embedder
from src.api.core.search import Retrieval, Retriever, Strategy, above_threshold
from src.api.core.settings import get_settings

DATASET = Path("evals/datasets/retrieval.jsonl")
REPORT = Path("evals/retrieval_ablation.md")
TOP_K = 5
THRESHOLDS = [0.0, 1.0, 1.5, 2.0, 2.5]


@dataclass
class Case:
    id: str
    kind: str
    question: str
    relevant: list[str]

    @property
    def off_topic(self) -> bool:
        return not self.relevant


@dataclass
class StrategyResult:
    strategy: Strategy
    retrievals: dict[str, Retrieval] = field(default_factory=dict)
    search_ms: list[int] = field(default_factory=list)


def load_cases(path: Path = DATASET) -> list[Case]:
    with path.open(encoding="utf-8") as handle:
        return [Case(**json.loads(line)) for line in handle if line.strip()]


def score(result: StrategyResult, cases: list[Case]) -> dict[str, float]:
    in_scope = [c for c in cases if not c.off_topic]
    off_topic = [c for c in cases if c.off_topic]
    ids = {c.id: result.retrievals[c.id].ids for c in cases}
    return {
        "recall@1": mean([recall_at_k(ids[c.id], c.relevant, 1) for c in in_scope]),
        "recall@3": mean([recall_at_k(ids[c.id], c.relevant, 3) for c in in_scope]),
        "mrr": mean([reciprocal_rank(ids[c.id], c.relevant) for c in in_scope]),
        # A correct abstention is an empty result on an off-topic question.
        "abstention": mean([1.0 if not ids[c.id] else 0.0 for c in off_topic]),
        # The cost of abstaining: in-scope questions that came back empty.
        "false_abstention": mean([1.0 if not ids[c.id] else 0.0 for c in in_scope]),
        "search_p50": percentile(result.search_ms, 50),
        "search_p95": percentile(result.search_ms, 95),
    }


def threshold_sweep(result: StrategyResult, cases: list[Case]) -> list[dict[str, float]]:
    """Re-apply candidate thresholds to the semantic candidates, without new queries."""
    rows = []
    in_scope = [c for c in cases if not c.off_topic]
    off_topic = [c for c in cases if c.off_topic]
    for threshold in THRESHOLDS:
        kept = {
            c.id: [h.id for h in above_threshold(result.retrievals[c.id].candidates, threshold)]
            for c in cases
        }
        rows.append(
            {
                "threshold": threshold,
                "recall@3": mean([recall_at_k(kept[c.id], c.relevant, 3) for c in in_scope]),
                "abstention": mean([1.0 if not kept[c.id] else 0.0 for c in off_topic]),
                "false_abstention": mean([1.0 if not kept[c.id] else 0.0 for c in in_scope]),
            }
        )
    return rows


def run(runs: int) -> tuple[list[Case], dict[Strategy, StrategyResult], list[int]]:
    settings = get_settings()
    settings.require("azure_search_endpoint", "azure_openai_endpoint")
    credential = DefaultAzureCredential()
    embedder = Embedder(
        AzureOpenAIClientFactory(settings.azure_openai_endpoint, credential),
        settings.azure_openai_embedding_deployment,
    )
    backend = SearchClient(settings.azure_search_endpoint, settings.azure_search_index, credential)
    retriever = Retriever(backend, embedder)
    cases = load_cases()

    # Warm-up: the first call pays for TLS and token acquisition, which is not retrieval latency.
    retriever.search("warm up", Strategy.HYBRID_SEMANTIC)

    embed_ms: list[int] = []
    results = {s: StrategyResult(s) for s in Strategy}
    for case in cases:
        started = time.perf_counter()
        vector = embedder(case.question)
        embed_ms.append(int((time.perf_counter() - started) * 1000))
        for strategy in Strategy:
            for _ in range(runs):
                retrieval = retriever.search(case.question, strategy, TOP_K, vector=vector)
                results[strategy].search_ms.append(retrieval.search_ms)
            results[strategy].retrievals[case.id] = retrieval
    return cases, results, embed_ms


def render(
    cases: list[Case], results: dict[Strategy, StrategyResult], embed_ms: list[int], runs: int
) -> str:
    letters = {
        Strategy.VECTOR: "A",
        Strategy.HYBRID: "B",
        Strategy.HYBRID_SEMANTIC: "C",
        Strategy.VECTOR_SEMANTIC: "D",
    }
    labels = {
        Strategy.VECTOR: "A. vector only",
        Strategy.HYBRID: "B. hybrid (BM25 + vector, RRF)",
        Strategy.HYBRID_SEMANTIC: "C. hybrid + semantic reranker",
        Strategy.VECTOR_SEMANTIC: "D. vector + semantic reranker",
    }
    scores = {s: score(r, cases) for s, r in results.items()}
    in_scope = [c for c in cases if not c.off_topic]
    off_topic = [c for c in cases if c.off_topic]

    lines = [
        "# Retrieval ablation",
        "",
        f"Generated by `python -m evals.retrieval_ablation` on "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}.",
        f"Dataset: `{DATASET}`, {len(in_scope)} in-scope questions and {len(off_topic)} off-topic "
        f"ones. Top {TOP_K}, {runs} search run(s) per question and strategy for latency.",
        "",
        "## Results",
        "",
        "| Configuration | Recall@1 | Recall@3 | MRR | Correct abstention | False abstention "
        "| Search p50 | Search p95 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for strategy, s in scores.items():
        lines.append(
            f"| {labels[strategy]} | {s['recall@1']:.2f} | {s['recall@3']:.2f} | {s['mrr']:.2f} "
            f"| {s['abstention']:.0%} | {s['false_abstention']:.0%} "
            f"| {s['search_p50']:.0f} ms | {s['search_p95']:.0f} ms |"
        )
    lines += [
        "",
        f"Embedding the question adds a p50 of {percentile(embed_ms, 50):.0f} ms and a p95 of "
        f"{percentile(embed_ms, 95):.0f} ms, paid once per question whatever the strategy.",
        "",
        "## Recall@3 by question family",
        "",
        "Keyword queries are where BM25 is expected to help; natural language is where it may not.",
        "",
        "| Family | Questions | " + " | ".join(letters[s] for s in Strategy) + " |",
        "|---|---|" + "---|" * len(Strategy),
    ]
    for kind in sorted({c.kind for c in in_scope}):
        family = [c for c in in_scope if c.kind == kind]
        cells = []
        for strategy in Strategy:
            ids = results[strategy].retrievals
            cells.append(f"{mean([recall_at_k(ids[c.id].ids, c.relevant, 3) for c in family]):.2f}")
        lines.append(f"| {kind} | {len(family)} | " + " | ".join(cells) + " |")

    for strategy in (s for s in Strategy if s.reranked):
        lines += [
            "",
            f"## Threshold sweep for configuration {letters[strategy]}",
            "",
            "| Min reranker score | Recall@3 | Correct abstention | False abstention |",
            "|---|---|---|---|",
        ]
        for row in threshold_sweep(results[strategy], cases):
            lines.append(
                f"| {row['threshold']:.1f} | {row['recall@3']:.2f} | {row['abstention']:.0%} "
                f"| {row['false_abstention']:.0%} |"
            )

    lines += [
        "",
        "## Per question",
        "",
        "| Id | Kind | Question | " + " | ".join(letters[s] for s in Strategy) + " |",
        "|---|---|---|" + "---|" * len(Strategy),
    ]
    for case in cases:
        cells = []
        for strategy in Strategy:
            ids = results[strategy].retrievals[case.id].ids
            if case.off_topic:
                cells.append("abstained" if not ids else f"{len(ids)} hits")
            else:
                rank = next((i for i, d in enumerate(ids, 1) if d in case.relevant), None)
                cells.append(f"rank {rank}" if rank else "**miss**")
        lines.append(f"| {case.id} | {case.kind} | {case.question} | " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval ablation")
    parser.add_argument("--runs", type=int, default=1, help="search repetitions for latency")
    args = parser.parse_args()
    cases, results, embed_ms = run(args.runs)
    report = render(cases, results, embed_ms, args.runs)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"Report written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
