"""Step 4, register_model: a quality gate, then the model registry (task 4.9).

The artefact is checked before anything is registered: every identifier belongs to the
catalogue, every confidence is a probability, and the evaluation verdict travels as tags. A run
that fails the gate registers nothing.

python -m reco.register --model_dir <model> --model_name coffee-reco-apriori
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import tracking
from .artifacts import ARTIFACT_FILE
from .data import CATEGORIES
from .io import read_json
from .train import METRICS_FILE

TAGGED_METRICS = (
    "rules",
    "catalogue_coverage",
    "test.apriori_fill.hit_rate_at_5",
    "test.popularity.hit_rate_at_5",
    "test.apriori_fill_vs_popularity.hit_rate_ci_low",
    "test.apriori_fill_vs_popularity.hit_rate_ci_high",
)


def gate(artifacts: dict[str, Any]) -> list[str]:
    """Reasons to refuse the artefact; empty when it can be served."""
    problems = []
    known = set(CATEGORIES)
    for antecedent, rules in artifacts["rules"].items():
        if antecedent not in known:
            problems.append(f"unknown antecedent {antecedent}")
        for rule in rules:
            if rule["product_id"] not in known:
                problems.append(f"unknown consequent {rule['product_id']}")
            if not 0 <= rule["confidence"] <= 1:
                problems.append(f"confidence out of range for {antecedent} -> {rule['product_id']}")
    unknown_sellers = set(artifacts["popularity"]) - known
    if unknown_sellers:
        problems.append(f"unknown products in popularity: {sorted(unknown_sellers)}")
    if not artifacts["popularity"]:
        problems.append("empty popularity: no fallback for products without rules")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model_dir", type=Path, required=True)
    parser.add_argument("--model_name", default="coffee-reco-apriori")
    args = parser.parse_args()

    artifacts = read_json(args.model_dir / ARTIFACT_FILE)
    metrics = read_json(args.model_dir / METRICS_FILE)
    problems = gate(artifacts)
    if problems:
        raise SystemExit("artefact refused:\n" + "\n".join(problems))

    tags = {key: f"{metrics[key]:.4f}" for key in TAGGED_METRICS if key in metrics}
    tags.update(
        {
            "min_support": str(metrics["min_support"]),
            "verdict": metrics["verdict"],
            "serving_format": "RecommendationArtifacts",
            "data": "coffee-sales, April 2019",
        }
    )

    with tracking.run("register_model"):
        version = register(args.model_dir, args.model_name, tags)
        print(f"registered {args.model_name}:{version}")


def register(model_dir: Path, model_name: str, tags: dict[str, str]) -> str:
    """Save the pyfunc locally, upload it as run artefacts, then create the registry version.

    `mlflow.pyfunc.log_model` would be shorter, but MLflow 3 sends it to a `logged-models`
    endpoint that the Azure ML tracking server does not serve (404). Saving, uploading and
    registering separately only uses calls both the local and the Azure ML servers support.
    """
    import contextlib
    import tempfile

    import mlflow
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    from .pyfunc import AssociationRulesModel

    with tempfile.TemporaryDirectory() as tmp:
        saved = Path(tmp) / "model"
        mlflow.pyfunc.save_model(
            path=str(saved),
            python_model=AssociationRulesModel(),
            artifacts={
                "recommendations": str(model_dir / ARTIFACT_FILE),
                "metrics": str(model_dir / METRICS_FILE),
            },
            # The model imports the reco package: it travels with it.
            code_paths=[str(Path(__file__).parent)],
        )
        mlflow.log_artifacts(str(saved), artifact_path="model")

    active = mlflow.active_run()
    if active is None:
        raise RuntimeError("register() must run inside an MLflow run")
    run_id = active.info.run_id
    client = MlflowClient()
    with contextlib.suppress(MlflowException):  # already registered: this run adds a version
        client.create_registered_model(model_name)
    # The artefact's own URI (azureml://artifacts/... on Azure ML, a path locally): the Azure ML
    # registry refuses the runs:/ shorthand.
    created = client.create_model_version(
        model_name, source=mlflow.get_artifact_uri("model"), run_id=run_id, tags=tags
    )
    return str(created.version)


if __name__ == "__main__":
    main()
