"""The golden evaluation suite (tasks 5.5 to 5.8): `make eval`.

Five suites, on the real Azure services and the very components the API serves:

    routing           30 messages            Router alone                 accuracy, confusion
    guard             20 messages            Guard alone                  recall, false positives
    rag               25 questions           Details alone                recall@3, abstention,
                                                                          groundedness, relevance
    orders            25 conversations       the whole assistant          exact match, total error,
                                                                          latency, cost
    recommendations   10 requests            Recommendation alone         products outside the menu

Each component is evaluated in isolation where a single metric is about it, and the orders run
through the full assistant (guard, router, agents, upsell), so system latency and cost per
conversation are measured on real conversations.

The run fails (exit code 1) when a blocking threshold of `evals/thresholds.yaml` is breached:
that is what makes it a CI gate.

Usage: python -m evals.run_eval [--suites routing,guard] [--baseline] [--workers 8]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from evals.evaluators.deterministic import (
    abstention_scores,
    declined,
    guard_scores,
    menu_hallucination,
    order_scores,
    router_accuracy,
)
from evals.metrics import mean, percentile, recall_at_k
from src.api.app.services import Components, build_components
from src.api.core.schemas import ChatMessage
from src.api.core.settings import get_settings

DATASETS = Path("evals/datasets")
REPORTS = Path("evals/reports")
THRESHOLDS = Path("evals/thresholds.yaml")
SUITES = ("routing", "guard", "rag", "orders", "recommendations")


def load(name: str) -> list[dict[str, Any]]:
    with (DATASETS / f"{name}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def messages_of(raw: Iterable[Mapping[str, Any]]) -> list[ChatMessage]:
    return [ChatMessage(**m) for m in raw]


def parallel(
    function: Callable[[dict[str, Any]], dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    workers: int,
) -> list[dict[str, Any]]:
    """Runs every case, keeps the dataset order, and turns an exception into a failed case."""

    def guarded(case: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = function(case)
        except Exception as exc:  # a crash is a result to report, not a reason to stop
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "id": case["id"],
            "kind": case.get("kind"),
            "seconds": round(time.perf_counter() - started, 2),
            **result,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(guarded, cases))


# ---- Suites ---------------------------------------------------------------------------------


def run_routing(parts: Components, workers: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = load("routing")

    def one(case: dict[str, Any]) -> dict[str, Any]:
        outcome = parts.router.route(messages_of(case["messages"]))
        return {"expected": case["expected"], "predicted": outcome.route}

    results = parallel(one, cases, workers)
    errors = sum("error" in r for r in results)
    return router_accuracy(results) | {"routing_errors": errors}, results


def run_guard(parts: Components, workers: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = load("guard")

    def one(case: dict[str, Any]) -> dict[str, Any]:
        outcome = parts.guard.check(messages_of(case["messages"]))
        return {
            "expected_allowed": case["expected_allowed"],
            "allowed": outcome.allowed,
            "reason": outcome.reason,
            "layer": outcome.layer,
        }

    results = parallel(one, cases, workers)
    for r, case in zip(results, cases, strict=True):
        r.setdefault("expected_allowed", case["expected_allowed"])
    errors = sum("error" in r for r in results)
    return guard_scores(results) | {"guard_errors": errors}, results


def run_rag(parts: Components, workers: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from evals.evaluators.judges import Judges

    cases = load("rag")
    judges = Judges(parts.settings)

    def one(case: dict[str, Any]) -> dict[str, Any]:
        reply = parts.details.answer([ChatMessage(role="user", content=case["question"])])
        documents = reply.trace["documents"]
        result: dict[str, Any] = {
            "relevant": case["relevant"],
            "documents": documents,
            "best_reranker_score": round(reply.trace["best_reranker_score"], 3),
            "retrieval_abstained": reply.trace["abstained"],
            "answer": reply.content,
            "declined": declined(reply.content),
        }
        if case["relevant"]:
            result["recall_at_3"] = recall_at_k(documents, case["relevant"], 3)
            if not result["declined"] and reply.trace.get("context"):
                result |= judges.score(case["question"], reply.trace["context"], reply.content)
        return result

    results = parallel(one, cases, workers)
    for r, case in zip(results, cases, strict=True):
        r.setdefault("relevant", case["relevant"])
    scored = [r for r in results if "answer" in r]
    answerable = [r for r in results if r["relevant"]]
    judged = [r for r in answerable if "groundedness" in r]
    metrics = {
        "rag_recall_at_3": mean([r.get("recall_at_3", 0.0) for r in answerable]),
        "rag_groundedness": mean([r["groundedness"] for r in judged]),
        "rag_relevance": mean([r["relevance"] for r in judged]),
        # A judge scores on a noisy 1-5 scale and gives 4 to a short, correct answer: a mean
        # sitting at 4.0 fails on a single 3. The share of answers at 4 or more is the stable gate.
        "rag_groundedness_pass_rate": mean([float(r["groundedness"] >= 4) for r in judged]),
        "rag_relevance_pass_rate": mean([float(r["relevance"] >= 4) for r in judged]),
        "rag_judged_answers": len(judged),
        "rag_errors": len(results) - len(scored),
    } | abstention_scores(results)
    # ADR-008 asked for the 1.7 gate to be checked on questions it was not chosen on.
    unanswerable = [r["best_reranker_score"] for r in scored if not r["relevant"]]
    metrics["rag_gate_max_unanswerable_score"] = max(unanswerable, default=0.0)
    metrics["rag_gate_min_answerable_score"] = min(
        (r["best_reranker_score"] for r in answerable), default=0.0
    )
    return metrics, results


def run_orders(parts: Components, workers: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = load("orders")

    def one(case: dict[str, Any]) -> dict[str, Any]:
        history: list[ChatMessage] = []
        turns = []
        for text in case["turns"]:
            history.append(ChatMessage(role="user", content=text))
            turn = parts.assistant.respond(history)
            history.append(ChatMessage(role="assistant", content=turn.content, memory=turn.memory))
            turns.append(
                {
                    "user": text,
                    "agent": turn.agent,
                    "content": turn.content,
                    "latency_ms": turn.latency_ms,
                    "usage": asdict(turn.usage),
                    "cost_usd": str(parts.price.cost(turn.usage)),
                    "trace": {k: v for k, v in turn.trace.items() if k != "context"},
                }
            )
        order = (
            history[-1].memory["order"] if history[-1].memory else {"items": [], "status": "open"}
        )
        return {
            "expected_items": case["expected_items"],
            "expected_status": case["expected_status"],
            "final_items": {i["product_id"]: i["quantity"] for i in order["items"]},
            "final_status": order["status"],
            "turns": turns,
        }

    # Two conversations at a time at most: latency is measured as a customer lives it, not
    # inflated by the evaluation's own load on the deployment's request limit.
    results = parallel(one, cases, min(workers, 2))
    for r, case in zip(results, cases, strict=True):
        r.setdefault("expected_items", case["expected_items"])
        r.setdefault("expected_status", case["expected_status"])
    scored = [r for r in results if "turns" in r]
    latencies = [t["latency_ms"] for r in scored for t in r["turns"]]
    costs = [sum(float(t["cost_usd"]) for t in r["turns"]) for r in scored]
    metrics = order_scores(results, parts.catalog) | {
        "system_latency_p50_ms": percentile(latencies, 50),
        "system_latency_p95_ms": percentile(latencies, 95),
        "cost_per_conversation_usd": mean(costs),
        "cost_per_conversation_max_usd": max(costs, default=0.0),
        "orders_errors": len(results) - len(scored),
    }
    return metrics, results


def run_recommendations(
    parts: Components, workers: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = [c for c in load("routing") if c["expected"] == "recommendation"]

    def one(case: dict[str, Any]) -> dict[str, Any]:
        reply = parts.recommendation.answer(messages_of(case["messages"]))
        return {
            "recommended": reply.trace["recommended"],
            "kind_served": reply.trace["kind"],
            "answer": reply.content,
        }

    results = parallel(one, cases, workers)
    scored = [r for r in results if "recommended" in r]
    lengths = [len(r["recommended"]) for r in scored]
    metrics = {
        "recommendation_min_items": min(lengths, default=0),
        "recommendation_max_items": max(lengths, default=0),
        "recommendation_errors": len(results) - len(scored),
    }
    return metrics, results


RUNNERS = {
    "routing": run_routing,
    "guard": run_guard,
    "rag": run_rag,
    "orders": run_orders,
    "recommendations": run_recommendations,
}


# ---- Thresholds and report --------------------------------------------------------------------


def hallucination(parts: Components, details: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Products and prices outside the catalogue, across every suite that names products."""
    ids: list[str] = []
    answers: list[str] = []
    by_name = {p.name: p.product_id for p in parts.catalog.products}
    for r in details.get("recommendations", []):
        ids += r.get("recommended", [])
    for r in details.get("orders", []):
        for t in r.get("turns", []):
            ids += [line["product_id"] for line in t["trace"].get("order", [])]
            ids += [by_name.get(name, f"unknown:{name}") for name in t["trace"].get("upsell", [])]
    answers += [r["answer"] for r in details.get("rag", []) if "answer" in r]
    return menu_hallucination(ids, answers, parts.catalog)


def check(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, rule in thresholds.items():
        if name not in metrics:
            continue  # suite not run
        value = metrics[name]
        if "min" in rule:
            passed = value >= rule["min"]
            bound = f">= {rule['min']}"
        elif "max" in rule:
            passed = value <= rule["max"]
            bound = f"<= {rule['max']}"
        else:
            passed = value == rule["equals"]
            bound = f"== {rule['equals']}"
        rows.append(
            {
                "metric": name,
                "value": value,
                "threshold": bound,
                "target": rule.get("target"),
                "blocking": rule.get("blocking", True),
                "passed": passed,
            }
        )
    return rows


def render(report: Mapping[str, Any]) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"{report['started_at']}, {report['duration_s']} s, model `{report['model']}`, "
        f"suites: {', '.join(report['suites'])}.",
        "",
        "| Metric | Value | Threshold | Target | Blocking | Result |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["checks"]:
        value = row["value"]
        shown = f"{value:.3f}" if isinstance(value, float) else str(value)
        lines.append(
            f"| {row['metric']} | {shown} | {row['threshold']} | {row['target'] or ''} | "
            f"{'yes' if row['blocking'] else 'no'} | {'pass' if row['passed'] else '**FAIL**'} |"
        )
    lines += ["", "## Other measurements", ""]
    for key, value in sorted(report["metrics"].items()):
        if key not in {r["metric"] for r in report["checks"]}:
            lines.append(f"- `{key}`: {value}")
    failures = failed_cases(report["details"])
    if failures:
        lines += ["", "## Failed cases", ""] + [f"- {f}" for f in failures]
    return "\n".join(lines) + "\n"


def failed_cases(details: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    out = []
    for r in details.get("routing", []):
        if r.get("predicted") != r.get("expected"):
            out.append(
                f"routing {r['id']} ({r['kind']}): expected {r.get('expected')}, "
                f"got {r.get('predicted', r.get('error'))}"
            )
    for r in details.get("guard", []):
        if r.get("allowed") != r.get("expected_allowed"):
            out.append(
                f"guard {r['id']} ({r['kind']}): expected allowed={r.get('expected_allowed')},"
                f" got {r.get('allowed', r.get('error'))} ({r.get('layer')}, {r.get('reason')})"
            )
    for r in details.get("rag", []):
        if "error" in r:
            out.append(f"rag {r['id']}: {r['error']}")
        elif r["relevant"] and (r.get("recall_at_3") == 0 or r["declined"]):
            out.append(
                f"rag {r['id']}: recall@3={r.get('recall_at_3')}, declined={r['declined']}, "
                f"documents={r['documents']}"
            )
        elif not r["relevant"] and not r["declined"]:
            out.append(f"rag {r['id']} (unanswerable) answered: {r['answer'][:120]!r}")
        elif r.get("groundedness", 5) < 4 or r.get("relevance", 5) < 4:
            out.append(
                f"rag {r['id']}: groundedness={r.get('groundedness')}, "
                f"relevance={r.get('relevance')}: {r['answer'][:120]!r}"
            )
    for r in details.get("orders", []):
        if "error" in r:
            out.append(f"orders {r['id']}: {r['error']}")
        elif r["final_items"] != r["expected_items"] or r["final_status"] != r["expected_status"]:
            out.append(
                f"orders {r['id']} ({r['kind']}): expected {r['expected_items']} "
                f"{r['expected_status']}, got {r['final_items']} {r['final_status']}"
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suites", default=",".join(SUITES))
    # 4, not more: the deployment allows one request per minute per 1k tokens of capacity, and
    # 8 parallel conversations of fast calls went over it on the first run (day 5).
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--baseline", action="store_true", help="also write reports/baseline.*")
    args = parser.parse_args()
    suites = [s for s in args.suites.split(",") if s]

    started = time.perf_counter()
    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = build_components(get_settings())

    metrics: dict[str, Any] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    for suite in suites:
        suite_started = time.perf_counter()
        suite_metrics, results = RUNNERS[suite](parts, args.workers)
        metrics |= suite_metrics
        details[suite] = results
        print(f"{suite}: {time.perf_counter() - suite_started:.0f} s")
    if {"recommendations", "orders", "rag"} & set(suites):
        metrics |= hallucination(parts, details)

    thresholds = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))
    checks = check(metrics, thresholds)
    report = {
        "started_at": started_at,
        "duration_s": round(time.perf_counter() - started),
        "model": parts.settings.azure_openai_chat_deployment,
        "recommender": parts.recommender.version,
        "suites": suites,
        "metrics": metrics,
        "checks": checks,
        "details": details,
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = started_at.replace(":", "").replace("-", "")
    names = [f"run-{stamp}"] + (["baseline"] if args.baseline else [])
    for name in names:
        (REPORTS / f"{name}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        (REPORTS / f"{name}.md").write_text(render(report))
    print(render(report))

    breached = [c for c in checks if c["blocking"] and not c["passed"]]
    if breached:
        print(
            f"{len(breached)} blocking threshold(s) breached: "
            + ", ".join(c["metric"] for c in breached),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
