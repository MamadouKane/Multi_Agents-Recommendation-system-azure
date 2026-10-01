"""MLflow, when it is there. On Azure ML a job is already an MLflow run, so the calls below land
in the workspace; locally they land in `./mlruns`; without MLflow they are skipped."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

mlflow: Any
try:
    import mlflow
except ImportError:  # pragma: no cover - MLflow is in the ml extra and on Azure ML
    mlflow = None


@contextmanager
def run(name: str, nested: bool = False) -> Iterator[None]:
    if mlflow is None:
        yield
        return
    with mlflow.start_run(run_name=name, nested=nested):
        yield


def params(values: dict[str, Any]) -> None:
    if mlflow is not None:
        mlflow.log_params(values)


def metrics(values: dict[str, float]) -> None:
    if mlflow is not None:
        mlflow.log_metrics(values)


def tags(values: dict[str, str]) -> None:
    if mlflow is not None:
        mlflow.set_tags(values)


def artifact(path: Path, folder: str | None = None) -> None:
    if mlflow is not None:
        mlflow.log_artifact(str(path), artifact_path=folder)
