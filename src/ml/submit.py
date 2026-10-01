"""Submit the recommender pipeline to Azure ML (tasks 4.2, 4.3, 4.6).

    prep_data -> evaluate (sweep, choose, test) -> train_apriori -> register_model

The raw sales become a versioned data asset first, so every run records which data it used.
Jobs run on the scale-to-zero cluster as the training identity: no key, no person's token.

Usage: python -m src.ml.submit [--wait]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from azure.ai.ml import Input, MLClient, load_component
from azure.ai.ml.constants import AssetTypes
from azure.ai.ml.dsl import pipeline
from azure.ai.ml.entities import Data, ManagedIdentityConfiguration
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

COMPONENTS = Path(__file__).parent / "components"
SALES_DIR = Path("data/raw/sales")
DATA_NAME, DATA_VERSION = "coffee-sales", "1"
EXPERIMENT = "coffee-reco"


def ml_client(resource_group: str, workspace: str) -> MLClient:
    subscription = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return MLClient(DefaultAzureCredential(), subscription, resource_group, workspace)


def ensure_data_asset(client: MLClient) -> Data:
    try:
        existing: Data = client.data.get(DATA_NAME, version=DATA_VERSION)
        return existing
    except ResourceNotFoundError:
        asset = Data(
            name=DATA_NAME,
            version=DATA_VERSION,
            type=AssetTypes.URI_FOLDER,
            path=str(SALES_DIR),
            description="Kaggle coffee shop sample data, April 2019, three outlets (49,894 lines).",
            tags={"source": "kaggle ylchang/coffee-shop-sample-data-1113", "period": "2019-04"},
        )
        created: Data = client.data.create_or_update(asset)
        return created


def build(data: Data, min_supports: str, cluster: str):  # type: ignore[no-untyped-def]
    prep = load_component(source=COMPONENTS / "prep_data.yml")
    evaluate = load_component(source=COMPONENTS / "evaluate.yml")
    train = load_component(source=COMPONENTS / "train_apriori.yml")
    register = load_component(source=COMPONENTS / "register_model.yml")

    @pipeline(  # type: ignore[call-overload]
        name="coffee_reco_training", description="Association rules for the coffee shop assistant"
    )
    # No annotation on `sales`: with postponed annotations the SDK would read the string "Input".
    def training(sales):  # type: ignore[no-untyped-def]
        baskets = prep(sales=sales)
        selection = evaluate(baskets=baskets.outputs.baskets, min_supports=min_supports)
        model = train(baskets=baskets.outputs.baskets, selection=selection.outputs.selection)
        register(model=model.outputs.model)
        return {"model": model.outputs.model, "selection": selection.outputs.selection}

    job = training(sales=Input(type=AssetTypes.URI_FOLDER, path=data.id))
    job.settings.default_compute = cluster
    for step in job.jobs.values():
        step.identity = ManagedIdentityConfiguration()
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource_group", default="rg-coffeeai-dev")
    parser.add_argument("--workspace", default="mlw-coffeeai-dev-frc")
    parser.add_argument("--cluster", default="cpu-cluster")
    parser.add_argument("--min_supports", default="0.005,0.01,0.02,0.03,0.05,0.08")
    parser.add_argument("--wait", action="store_true", help="stream the logs until the end")
    args = parser.parse_args()

    client = ml_client(args.resource_group, args.workspace)
    data = ensure_data_asset(client)
    print(f"data asset {data.name}:{data.version}")
    job = client.jobs.create_or_update(
        build(data, args.min_supports, args.cluster), experiment_name=EXPERIMENT
    )
    print(f"submitted {job.name}\n{job.studio_url}")
    if args.wait:
        client.jobs.stream(job.name)
        status = client.jobs.get(job.name).status
        print(f"status: {status}")
        return 0 if status == "Completed" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
