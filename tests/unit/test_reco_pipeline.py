"""The four pipeline steps, end to end on the real sales, with a throwaway MLflow registry."""

import sys

import pytest

mlflow = pytest.importorskip("mlflow")
pd = pytest.importorskip("pandas")

from src.ml.reco import prep_data, register, sweep, train  # noqa: E402
from src.ml.reco.io import read_json  # noqa: E402


def run(module, monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", [module.__name__, *map(str, arguments)])
    module.main()


def test_the_pipeline_selects_trains_and_registers_a_servable_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.chdir(tmp_path)  # MLflow artefacts land here, never in the repository
    sales = pytest.importorskip("pathlib").Path(__file__).parents[2] / "data/raw/sales"

    run(prep_data, monkeypatch, "--sales_dir", sales, "--output_dir", tmp_path / "baskets")
    run(
        sweep,
        monkeypatch,
        "--baskets_dir",
        tmp_path / "baskets",
        "--min_supports",
        "0.01,0.05",
        "--output_dir",
        tmp_path / "selection",
    )
    selected = read_json(tmp_path / "selection" / "selected.json")
    assert selected["chosen_min_support"] == 0.01  # 0.660 against 0.620 on validation
    assert [s["min_support"] for s in selected["sweep"]] == [0.01, 0.05]

    run(
        train,
        monkeypatch,
        "--baskets_dir",
        tmp_path / "baskets",
        "--selected",
        tmp_path / "selection",
        "--output_dir",
        tmp_path / "model",
    )
    metrics = read_json(tmp_path / "model" / "metrics.json")
    # Share of the catalogue with at least one rule: 14 of 18 products at 0.01.
    assert metrics["catalogue_coverage"] == metrics["antecedents"] / 18 == 14 / 18

    run(register, monkeypatch, "--model_dir", tmp_path / "model", "--model_name", "test-reco")
    model = mlflow.pyfunc.load_model("models:/test-reco/1")
    [suggestions] = model.predict(pd.DataFrame({"basket": [["latte"]]}))
    assert len(suggestions) == 5 and "latte" not in suggestions


def test_a_refused_artefact_registers_nothing(tmp_path, monkeypatch):
    import json

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "recommendations.json").write_text(
        json.dumps(
            {
                "rules": {"latte": [{"product_id": "packaged-bar", "confidence": 0.4}]},
                "popularity": {"latte": 1},
            }
        )
    )
    (model_dir / "metrics.json").write_text(json.dumps({"min_support": 0.01, "verdict": "x"}))
    with pytest.raises(SystemExit, match="artefact refused"):
        run(register, monkeypatch, "--model_dir", model_dir)
