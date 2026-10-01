"""Promote a registered model version to the API (task 4.11).

    registry  coffee-reco-apriori:N
      -> model-artefacts/coffee-reco-apriori/N/recommendations.json         kept, for rollback
      -> model-artefacts/coffee-reco-apriori/current/recommendations.json   what the API loads

The artefact is validated against the API's own schema before anything is uploaded, and the
version is written into it, so `/health` says which model answers. Rolling back is publishing
an older version: the API picks it up at its next start, no redeployment.

Usage: python -m src.ml.publish [--version N]   (default: the latest version)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from src.api.core.recommender import RecommendationArtifacts
from src.api.core.settings import get_settings
from src.ml.submit import ml_client

MODEL_NAME = "coffee-reco-apriori"
CONTAINER = "model-artefacts"
ARTIFACT = "recommendations.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="registered version; default: the latest")
    parser.add_argument("--resource_group", default="rg-coffeeai-dev")
    parser.add_argument("--workspace", default="mlw-coffeeai-dev-frc")
    args = parser.parse_args()

    client = ml_client(args.resource_group, args.workspace)
    model = (
        client.models.get(MODEL_NAME, version=args.version)
        if args.version
        else client.models.get(MODEL_NAME, label="latest")
    )
    with tempfile.TemporaryDirectory() as tmp:
        client.models.download(MODEL_NAME, version=model.version, download_path=tmp)
        found = sorted(Path(tmp).rglob(ARTIFACT))
        if not found:
            print(f"no {ARTIFACT} in {MODEL_NAME}:{model.version}", file=sys.stderr)
            return 1
        document = json.loads(found[0].read_text(encoding="utf-8"))

    document["version"] = f"{MODEL_NAME}:{model.version}"
    artifacts = RecommendationArtifacts.model_validate(document)  # the API's own contract
    body = artifacts.model_dump_json(indent=2).encode("utf-8")

    settings = get_settings()
    settings.require("azure_storage_blob_endpoint")
    blobs = BlobServiceClient(settings.azure_storage_blob_endpoint, DefaultAzureCredential())
    container = blobs.get_container_client(CONTAINER)
    content = ContentSettings(content_type="application/json")
    for folder in (model.version, "current"):
        name = f"{MODEL_NAME}/{folder}/{ARTIFACT}"
        container.upload_blob(name, body, overwrite=True, content_settings=content)
        print(f"uploaded {CONTAINER}/{name}")
    print(f"{artifacts.version}: {len(artifacts.rules)} antecedents, now served from 'current'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
