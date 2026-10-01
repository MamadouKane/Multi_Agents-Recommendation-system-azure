"""Run the four pipeline steps on this machine, as Azure ML would.

MLflow writes to a local SQLite database (`mlruns/mlflow.db`, git-ignored), which has a model
registry, so the register step runs for real too. Browse it with:
    mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db

Usage: python -m src.ml.run_local [--output_dir data/processed/ml]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ML_ROOT = Path(__file__).parent
REPO_ROOT = ML_ROOT.parent.parent
LOCAL_TRACKING = f"sqlite:///{REPO_ROOT / 'mlruns' / 'mlflow.db'}"


def step(module: str, *arguments: str) -> None:
    # From the repository root, so MLflow artefacts land in ./mlruns and not inside src/ml, which
    # is the code snapshot Azure ML uploads. PYTHONPATH makes `reco` importable as on Azure ML.
    env = {
        **os.environ,
        "PYTHONPATH": str(ML_ROOT),
        "MLFLOW_TRACKING_URI": os.environ.get("MLFLOW_TRACKING_URI", LOCAL_TRACKING),
    }
    env.setdefault("MLFLOW_EXPERIMENT_NAME", "coffee-reco")
    subprocess.run(
        [sys.executable, "-m", f"reco.{module}", *arguments], cwd=REPO_ROOT, check=True, env=env
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sales_dir", type=Path, default=Path("data/raw/sales"))
    parser.add_argument("--output_dir", type=Path, default=Path("data/processed/ml"))
    parser.add_argument("--min_supports", default="0.005,0.01,0.02,0.03,0.05,0.08")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    (REPO_ROOT / "mlruns").mkdir(exist_ok=True)

    step(
        "prep_data",
        "--sales_dir",
        str(args.sales_dir.resolve()),
        "--output_dir",
        str(out / "baskets"),
    )
    step(
        "sweep",
        "--baskets_dir",
        str(out / "baskets"),
        "--min_supports",
        args.min_supports,
        "--output_dir",
        str(out / "selection"),
    )
    step(
        "train",
        "--baskets_dir",
        str(out / "baskets"),
        "--selected",
        str(out / "selection"),
        "--output_dir",
        str(out / "model"),
    )
    step("register", "--model_dir", str(out / "model"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
