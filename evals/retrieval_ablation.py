"""Retrieval ablation (roadmap task 2.8: tests ADR-002, led to ADR-008).

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
from typing import Any

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
    def unanswerable(self) -> bool:
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
    in_scope = [c for c in cases if not c.unanswerable]
    unanswerable = [c for c in cases if c.unanswerable]
    ids = {c.id: result.retrievals[c.id].ids for c in cases}
    return {
        "recall@1": mean([recall_at_k(ids[c.id], c.relevant, 1) for c in in_scope]),
        "recall@3": mean([recall_at_k(ids[c.id], c.relevant, 3) for c in in_scope]),
        "mrr": mean([reciprocal_rank(ids[c.id], c.relevant) for c in in_scope]),
        # A correct abstention is an empty result on an unanswerable question.
        "abstention": mean([1.0 if not ids[c.id] else 0.0 for c in unanswerable]),
        # The cost of abstaining: in-scope questions that came back empty.
        "false_abstention": mean([1.0 if not ids[c.id] else 0.0 for c in in_scope]),
        "search_p50": percentile(result.search_ms, 50),
        "search_p95": percentile(result.search_ms, 95),
    }


def threshold_sweep(result: StrategyResult, cases: list[Case]) -> list[dict[str, float]]:
    """Re-apply candidate thresholds to the semantic candidates, without new queries."""
    rows = []
    in_scope = [c for c in cases if not c.unanswerable]
    unanswerable = [c for c in cases if c.unanswerable]
    for threshold in THRESHOLDS:
        kept = {
            c.id: [h.id for h in above_threshold(result.retrievals[c.id].candidates, threshold)]
            for c in cases
        }
        rows.append(
            {
                "threshold": threshold,
                "recall@3": mean([recall_at_k(kept[c.id], c.relevant, 3) for c in in_scope]),
                "abstention": mean([1.0 if not kept[c.id] else 0.0 for c in unanswerable]),
                "false_abstention": mean([1.0 if not kept[c.id] else 0.0 for c in in_scope]),
            }
        )
    return rows


VECTOR_GATES = [0.50, 0.55, 0.58, 0.60, 0.62, 0.65]


def gated(
    ranking: StrategyResult,
    gate: dict[str, float],
    threshold: float,
    cases: list[Case],
) -> dict[str, float]:
    """Keep the ranking of one configuration, and let a separate score decide to abstain.

    E and F were designed after reading the A to D results on this very dataset, so on it they
    are hypotheses, not findings. The day 5 RAG set, never looked at, is where they are validated.
    """
    in_scope = [c for c in cases if not c.unanswerable]
    unanswerable = [c for c in cases if c.unanswerable]
    ids = {c.id: (ranking.retrievals[c.id].ids if gate[c.id] >= threshold else []) for c in cases}
    return {
        "threshold": threshold,
        "recall@3": mean([recall_at_k(ids[c.id], c.relevant, 3) for c in in_scope]),
        "mrr": mean([reciprocal_rank(ids[c.id], c.relevant) for c in in_scope]),
        "abstention": mean([1.0 if not ids[c.id] else 0.0 for c in unanswerable]),
        "false_abstention": mean([1.0 if not ids[c.id] else 0.0 for c in in_scope]),
    }


def derived_configurations(
    results: dict[Strategy, StrategyResult], cases: list[Case]
) -> dict[str, list[dict[str, Any]]]:
    vector = results[Strategy.VECTOR]
    reranked = results[Strategy.VECTOR_SEMANTIC]
    # E: vector ranking, gate on the best reranker score among the candidates.
    reranker_gate = {
        c.id: max((h.reranker_score or 0.0) for h in reranked.retrievals[c.id].candidates)
        if reranked.retrievals[c.id].candidates
        else 0.0
        for c in cases
    }
    # F: vector ranking, gate on the best vector similarity. No reranker, so no Basic tier needed.
    vector_gate = {
        c.id: vector.retrievals[c.id].hits[0].score if vector.retrievals[c.id].hits else 0.0
        for c in cases
    }
    return {
        "E": [gated(vector, reranker_gate, t, cases) for t in THRESHOLDS],
        "F": [gated(vector, vector_gate, t, cases) for t in VECTOR_GATES],
        "gate_values": [
            {
                "id": c.id,
                "unanswerable": float(c.unanswerable),
                "reranker": reranker_gate[c.id],
                "vector": vector_gate[c.id],
            }
            for c in cases
        ],
    }


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
    in_scope = [c for c in cases if not c.unanswerable]
    unanswerable = [c for c in cases if c.unanswerable]

    lines = [
        "# Retrieval ablation",
        "",
        f"Generated by `python -m evals.retrieval_ablation` on "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}.",
        f"Dataset: `{DATASET}`, {len(in_scope)} answerable questions and "
        f"{len(unanswerable)} unanswerable ones.",
        f"Top {TOP_K}, {runs} search run(s) per question and strategy for latency.",
        "",
        "Unanswerable questions are about the coffee shop, so the Guard lets them through, but no",
        "document answers them (parking, phone number, caffeine content...). They replaced blatant",
        "off-topic questions, which the Guard stops before retrieval ever runs. The retrieval gate",
        "must say 'nothing answers this' although neighbouring documents look relevant.",
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

    derived = derived_configurations(results, cases)
    lines += [
        "",
        "## Derived configurations: rank with one signal, abstain with another",
        "",
        "Designed after reading the results above, on the same questions: these are hypotheses,",
        "to be confirmed on the unseen day 5 RAG set before any decision rests on them.",
        "",
        "- **E**: vector ranking (A), abstain when the best reranker score is below the threshold.",
        "- **F**: vector ranking (A), abstain when the best vector similarity is too low.",
        "  No reranker call at all, so the Free tier of AI Search would be enough.",
    ]
    for name, label in (("E", "Min reranker score"), ("F", "Min vector similarity")):
        lines += [
            "",
            f"### Configuration {name}",
            "",
            f"| {label} | Recall@3 | MRR | Correct abstention | False abstention |",
            "|---|---|---|---|---|",
        ]
        for row in derived[name]:
            lines.append(
                f"| {row['threshold']:.2f} | {row['recall@3']:.2f} | {row['mrr']:.2f} "
                f"| {row['abstention']:.0%} | {row['false_abstention']:.0%} |"
            )

    gates = derived["gate_values"]
    in_scope_vector = [g["vector"] for g in gates if not g["unanswerable"]]
    unanswerable_vector = [g["vector"] for g in gates if g["unanswerable"]]
    lines += [
        "",
        "Separation of the vector gate: in-scope questions have a best similarity between "
        f"{min(in_scope_vector):.3f} and {max(in_scope_vector):.3f}, unanswerable ones between "
        f"{min(unanswerable_vector):.3f} and {max(unanswerable_vector):.3f}. "
        + (
            "The two ranges do not overlap, so a clean threshold exists."
            if max(unanswerable_vector) < min(in_scope_vector)
            else "The two ranges overlap, so no threshold separates them without error."
        ),
    ]

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
            if case.unanswerable:
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
