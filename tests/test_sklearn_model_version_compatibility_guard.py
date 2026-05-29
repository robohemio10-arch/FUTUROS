from __future__ import annotations

import json
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.exceptions import InconsistentVersionWarning

from smartcrypto.qlib_engine.fresh_prediction_runner import run_qlib_fresh_predictions
from smartcrypto.qlib_engine.sklearn_compatibility import (
    evaluate_sklearn_compatibility,
    load_sklearn_artifact,
)


class DummyProbabilityModel:
    def predict_proba(self, frame):
        return np.tile(np.array([[0.2, 0.8]], dtype=float), (len(frame), 1))


def _write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                'timeframe: "5m"',
                "target_horizon: 3",
                "min_rows_for_training: 10",
                "test_size: 0.2",
                "random_state: 42",
                'model_version: "qlib_test"',
                "prediction_threshold: 0.55",
                "max_position_usdt: 50.0",
                "leverage: 2.0",
                "signal_ttl_minutes: 10",
                "feature_columns:",
                "  - ret_1",
                "  - market_regime",
            ]
        ),
        encoding="utf-8",
    )


def _write_market_features(path: Path) -> None:
    now = datetime.now(timezone.utc)
    pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "tf": "5m",
                "ts": now - timedelta(minutes=5),
                "ret_1": 0.01,
                "market_regime": "trend",
            },
            {
                "symbol": "ETHUSDT",
                "pair": "ETH/USDT:USDT",
                "tf": "5m",
                "ts": now,
                "ret_1": -0.01,
                "market_regime": "range",
            },
        ]
    ).to_parquet(path, index=False)


def _write_model(path: Path, *, sklearn_artifact_version: str | None = None) -> None:
    payload = {
        "model": DummyProbabilityModel(),
        "feature_columns": ["ret_1", "market_regime"],
        "model_version": "qlib_test",
        "model_backend": "dummy_probability_model",
    }
    if sklearn_artifact_version is not None:
        payload["sklearn_artifact_version"] = sklearn_artifact_version
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, path)


def test_status_ok_when_versions_match() -> None:
    report = evaluate_sklearn_compatibility(
        artifact_version=sklearn.__version__,
        runtime_version=sklearn.__version__,
    )

    assert report.status == "ok"
    assert report.reason is None


def test_status_warning_when_saved_version_differs_from_runtime() -> None:
    report = evaluate_sklearn_compatibility(
        artifact_version="1.8.0",
        runtime_version="1.7.0",
    )

    assert report.status == "warning"
    assert report.reason == "sklearn_version_mismatch"


def test_status_unknown_when_metadata_missing() -> None:
    report = evaluate_sklearn_compatibility(
        artifact_version=None,
        runtime_version=sklearn.__version__,
    )

    assert report.status == "unknown"
    assert report.reason == "sklearn_artifact_version_unknown"


def test_captures_inconsistent_version_warning(monkeypatch, tmp_path: Path) -> None:
    def fake_load(path):
        warnings.warn(
            InconsistentVersionWarning(
                estimator_name="LabelEncoder",
                current_sklearn_version="1.7.0",
                original_sklearn_version="1.8.0",
            )
        )
        return {"model": "dummy"}

    monkeypatch.setattr("smartcrypto.qlib_engine.sklearn_compatibility.joblib.load", fake_load)

    _, report = load_sklearn_artifact(tmp_path / "model.joblib")

    assert report.status == "warning"
    assert report.sklearn_artifact_version == "1.8.0"
    assert report.warning_count == 1


def test_permissive_mode_does_not_block_runner(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    features = tmp_path / "market_features.parquet"
    model = tmp_path / "model.joblib"
    output = tmp_path / "latest.parquet"
    report_path = tmp_path / "report.json"
    _write_config(config)
    _write_market_features(features)
    _write_model(model, sklearn_artifact_version="1.8.0")

    report = run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=output,
        report_path=report_path,
        config_path=config,
        sklearn_strict_compatibility=False,
    )

    assert report["status"] == "ok"
    assert report["sklearn_compatibility_status"] == "warning"
    assert report["sklearn_compatibility_reason"] == "sklearn_version_mismatch"
    json.dumps(json.loads(report_path.read_text(encoding="utf-8")))


def test_strict_mode_blocks_runner(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    features = tmp_path / "market_features.parquet"
    model = tmp_path / "model.joblib"
    _write_config(config)
    _write_market_features(features)
    _write_model(model, sklearn_artifact_version="1.8.0")

    report = run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=tmp_path / "latest.parquet",
        report_path=tmp_path / "report.json",
        config_path=config,
        sklearn_strict_compatibility=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "sklearn_artifact_incompatible"
    assert report["sklearn_compatibility_status"] == "incompatible"


def test_report_contains_compatibility_fields(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    features = tmp_path / "market_features.parquet"
    model = tmp_path / "model.joblib"
    _write_config(config)
    _write_market_features(features)
    _write_model(model, sklearn_artifact_version=sklearn.__version__)

    report = run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=tmp_path / "latest.parquet",
        report_path=tmp_path / "report.json",
        config_path=config,
    )

    assert report["status"] == "ok"
    assert report["sklearn_runtime_version"] == sklearn.__version__
    assert report["sklearn_artifact_version"] == sklearn.__version__
    assert report["sklearn_compatibility_status"] == "ok"
    assert "sklearn_compatibility" in report
