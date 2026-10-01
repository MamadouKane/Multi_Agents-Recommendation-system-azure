"""The registered model: an MLflow pyfunc wrapping the serving artefact.

The API reads `recommendations.json` directly; this wrapper is what makes the registry entry a
model rather than a folder: it can be loaded and queried from the registry, for a smoke test or
a batch job, with the same ranking the API serves.

    model = mlflow.pyfunc.load_model("models:/coffee-reco-apriori/1")
    model.predict(pandas.DataFrame({"basket": [["latte"], ["croissant", "latte"]]}))
"""

from __future__ import annotations

import json
from typing import Any

import mlflow.pyfunc

from .data import CATEGORIES
from .serve import for_basket, popular

TOP_K = 5


class AssociationRulesModel(mlflow.pyfunc.PythonModel):  # type: ignore[misc,name-defined]
    def load_context(self, context: Any) -> None:
        with open(context.artifacts["recommendations"], encoding="utf-8") as f:
            artifacts = json.load(f)
        self.rules = artifacts["rules"]
        self.popularity = artifacts["popularity"]

    def recommend(self, basket: list[str]) -> list[str]:
        chosen = for_basket(self.rules, CATEGORIES, basket, top_k=TOP_K)
        for product_id in popular(
            self.popularity, CATEGORIES, exclude=[*basket, *chosen], top_k=TOP_K
        ):
            if len(chosen) == TOP_K:
                break
            chosen.append(product_id)
        return chosen

    # No type hints here: MLflow reads them as an input schema and expects list[...] wrappers.
    def predict(self, context, model_input, params=None):  # type: ignore[no-untyped-def]
        return [self.recommend(list(basket)) for basket in model_input["basket"]]
