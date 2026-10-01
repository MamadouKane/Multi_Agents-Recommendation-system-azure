"""The serving artefact: what `src/api/core/recommender.py` loads (`RecommendationArtifacts`)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .rules import Rule, rules_by_antecedent

ARTIFACT_FILE = "recommendations.json"


def build_artifacts(
    rules: Iterable[Rule], counts: dict[str, int], source: str, version: str | None = None
) -> dict[str, Any]:
    grouped = rules_by_antecedent(rules)
    return {
        "source": source,
        "version": version,
        "rules": {
            antecedent: [
                {"product_id": r.consequent, "confidence": round(r.confidence, 6)} for r in items
            ]
            for antecedent, items in sorted(grouped.items())
        },
        "popularity": dict(sorted(counts.items())),
    }


def write_artifacts(artifacts: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ARTIFACT_FILE
    path.write_text(json.dumps(artifacts, indent=2) + "\n", encoding="utf-8")
    return path
