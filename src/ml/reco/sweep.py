"""Step 2, evaluate: sweep min_support, choose on validation, report on test (tasks 4.7, 4.8).

    validation   train 04-01..04-14   evaluate 04-15..04-21   -> chooses min_support
    test         train 04-01..04-21   evaluate 04-22..04-29   -> the reported numbers

Choosing min_support on the test week and reporting that same week would flatter the model: the
choice is made on validation, the test week is only looked at once, for the chosen value.
Selection criterion: hit_rate@5 of the model the API serves (rules completed by best sellers).

python -m reco.sweep --baskets_dir <baskets> --min_supports 0.005,0.01,... --output_dir <out>
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import tracking
from .artifacts import build_artifacts
from .data import CATEGORIES, Basket, popularity, split_by_date
from .evaluate import K, evaluate, hits_per_basket, models, paired_bootstrap
from .io import read_baskets, write_json
from .rules import mine_rules

VALIDATION_START = "2019-04-15"
TEST_START = "2019-04-22"
SERVED = "apriori_fill"
BASELINE = "popularity"
SELECTED_FILE = "selected.json"


def score(train: list[Basket], test: list[Basket], min_support: float) -> dict[str, Any]:
    rules = mine_rules(train, min_support)
    artifacts = build_artifacts(rules, popularity(train), source="evaluation")
    candidates = models(artifacts["rules"], artifacts["popularity"], CATEGORIES)
    scores = {name: evaluate(rec, test, CATEGORIES) for name, rec in candidates.items()}
    return {"rules": len(rules), "scores": scores, "models": candidates}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baskets_dir", type=Path, required=True)
    parser.add_argument("--min_supports", default="0.005,0.01,0.02,0.03,0.05,0.08")
    parser.add_argument("--output_dir", type=Path, required=True)
    args = parser.parse_args()
    supports = [float(v) for v in args.min_supports.split(",")]

    baskets = read_baskets(args.baskets_dir)
    train_v, rest = split_by_date(baskets, VALIDATION_START)
    validation, _ = split_by_date(rest, TEST_START)
    train_t, test = split_by_date(baskets, TEST_START)

    with tracking.run("evaluate"):
        tracking.params(
            {
                "min_supports": args.min_supports,
                "k": K,
                "validation_start": VALIDATION_START,
                "test_start": TEST_START,
            }
        )
        sweep = []
        for min_support in supports:
            with tracking.run(f"min_support={min_support}", nested=True):
                result = score(train_v, validation, min_support)
                served = result["scores"][SERVED]
                tracking.params({"min_support": min_support})
                tracking.metrics({"rules": result["rules"]})
                for name, scores in result["scores"].items():
                    tracking.metrics(scores.as_metrics(f"validation.{name}"))
                sweep.append(
                    {
                        "min_support": min_support,
                        "rules": result["rules"],
                        "validation_hit_rate": served.hit_rate,
                        "validation_mrr": served.mrr,
                    }
                )
                print(
                    f"min_support={min_support}: {result['rules']} rules, "
                    f"validation hit@{K}={served.hit_rate:.3f}"
                )

        # Best hit rate, then the higher min_support on a tie: fewer, sturdier rules.
        best = max(sweep, key=lambda s: (round(s["validation_hit_rate"], 3), s["min_support"]))
        chosen = best["min_support"]

        final = score(train_t, test, chosen)
        test_metrics: dict[str, float] = {}
        for name, scores in final["scores"].items():
            test_metrics.update(scores.as_metrics(f"test.{name}"))
        baseline_hits = hits_per_basket(final["models"][BASELINE], test)
        for name in ("apriori", SERVED):
            ci = paired_bootstrap(hits_per_basket(final["models"][name], test), baseline_hits)
            for key, value in ci.items():
                test_metrics[f"test.{name}_vs_{BASELINE}.hit_rate_{key}"] = value
        tracking.params({"chosen_min_support": chosen})
        tracking.metrics(test_metrics)

        served_test = final["scores"][SERVED]
        verdict = (
            "beats the popularity baseline"
            if test_metrics[f"test.{SERVED}_vs_{BASELINE}.hit_rate_ci_low"] > 0
            else "does not beat the popularity baseline"
        )
        summary = {
            "chosen_min_support": chosen,
            "sweep": sweep,
            "test": test_metrics,
            "test_cases": served_test.cases,
            "verdict": f"rules completed by best sellers {verdict} on the test week",
        }
        path = write_json(summary, args.output_dir / SELECTED_FILE)
        tracking.artifact(path)
        print(f"chosen min_support={chosen}; test hit@{K}={served_test.hit_rate:.3f}")
        print(summary["verdict"])


if __name__ == "__main__":
    main()
