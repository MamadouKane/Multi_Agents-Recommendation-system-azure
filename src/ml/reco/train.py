"""Step 3, train_apriori: the production artefact, trained on the whole month.

Evaluation used the past to predict the future; production uses every day available, with the
min_support chosen on validation. Writes the serving format and its training metrics.

python -m reco.train --baskets_dir <baskets> --selected <selected.json> --output_dir <model>
"""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from . import tracking
from .artifacts import build_artifacts, write_artifacts
from .data import CATEGORIES, popularity
from .io import read_baskets, read_json, write_json
from .rules import mine_rules

METRICS_FILE = "metrics.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baskets_dir", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True, help="selected.json from evaluate")
    parser.add_argument("--output_dir", type=Path, required=True)
    args = parser.parse_args()

    selection = read_json(
        args.selected / "selected.json" if args.selected.is_dir() else args.selected
    )
    min_support = float(selection["chosen_min_support"])
    baskets = read_baskets(args.baskets_dir)

    with tracking.run("train_apriori"):
        rules = mine_rules(baskets, min_support)
        artifacts = build_artifacts(
            rules,
            popularity(baskets),
            source=f"Azure ML training, April 2019 sales, min_support={min_support}",
        )
        metrics = {
            "rules": float(len(rules)),
            "antecedents": float(len(artifacts["rules"])),
            # Share of the catalogue with basket-based suggestions; the rest gets best sellers.
            "catalogue_coverage": len(artifacts["rules"]) / len(CATEGORIES),
            "mean_confidence": statistics.fmean(r.confidence for r in rules) if rules else 0.0,
            "median_lift": statistics.median(r.lift for r in rules) if rules else 0.0,
        }
        # The evaluation behind this model travels with it, into the registry tags.
        metrics.update({k: float(v) for k, v in selection["test"].items()})
        tracking.params({"min_support": min_support, "min_lift": 1.0})
        tracking.metrics({k: v for k, v in metrics.items() if not k.startswith("test.")})

        path = write_artifacts(artifacts, args.output_dir)
        write_json(
            {"min_support": min_support, "verdict": selection["verdict"], **metrics},
            args.output_dir / METRICS_FILE,
        )
        tracking.artifact(path, "model")
        print(f"{len(rules)} rules over {len(artifacts['rules'])} antecedents -> {path}")


if __name__ == "__main__":
    main()
