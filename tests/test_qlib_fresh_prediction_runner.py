from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from smartcrypto.execution.signal_producer import build_active_signals
from smartcrypto.qlib_engine.fresh_prediction_runner import run_qlib_fresh_predictions


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
    frame = pd.DataFrame(
        [
            {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "tf": "5m", "ts": now - timedelta(minutes=5), "ret_1": 0.01, "market_regime": "trend"},
            {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "tf": "5m", "ts": now, "ret_1": 0.02, "market_regime": "trend"},
            {"symbol": "ETHUSDT", "pair": "ETH/USDT:USDT", "tf": "5m", "ts": now, "ret_1": -0.01, "market_regime": "range"},
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _write_model(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": DummyProbabilityModel(),
            "feature_columns": ["ret_1", "market_regime"],
            "model_version": "qlib_test",
            "model_backend": "dummy_probability_model",
        },
        path,
    )


def test_runner_generates_fresh_parquet_with_valid_schema(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    features = tmp_path / "market_features.parquet"
    model = tmp_path / "qlib_model.joblib"
    output = tmp_path / "latest_qlib_predictions.parquet"
    report_path = tmp_path / "report.json"
    _write_config(config)
    _write_market_features(features)
    _write_model(model)

    report = run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=output,
        report_path=report_path,
        config_path=config,
    )

    assert report["status"] == "ok"
    assert report["rows"] == 2
    assert sorted(report["symbols"]) == ["BTCUSDT", "ETHUSDT"]
    predictions = pd.read_parquet(output)
    expected = {"date", "generated_at", "symbol", "pair", "tf", "prob_up", "score", "predicted_direction", "model_version", "model_backend"}
    assert expected.issubset(predictions.columns)
    generated_at = pd.to_datetime(predictions["generated_at"].iloc[0], utc=True).to_pydatetime()
    assert datetime.now(timezone.utc) - generated_at < timedelta(minutes=2)
    json.dumps(json.loads(report_path.read_text(encoding="utf-8")))


def test_signal_producer_accepts_fresh_runner_predictions(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    features = tmp_path / "market_features.parquet"
    model = tmp_path / "qlib_model.joblib"
    output = tmp_path / "latest_qlib_predictions.parquet"
    _write_config(config)
    _write_market_features(features)
    _write_model(model)
    run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=output,
        report_path=tmp_path / "runner_report.json",
        config_path=config,
    )

    signal_report = build_active_signals(
        {
            "runtime_mode": "paper",
            "source": "qlib",
            "paths": {
                "predictions": str(output),
                "primary_signals": str(tmp_path / "primary.json"),
                "pinned_signals": str(tmp_path / "pinned.json"),
                "report": str(tmp_path / "signal_report.json"),
            },
            "policy": {
                "validity_minutes": 45,
                "min_abs_score": 0.0,
                "min_confidence": 0.0,
                "max_signals": 2,
                "include_top_n_when_threshold_empty": 2,
                "never_overwrite_with_empty": True,
                "max_prediction_age_minutes": 90,
            },
            "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
        },
        force_from_predictions=True,
        validity_minutes=45,
    )

    assert signal_report["status"] == "ok"
    assert signal_report["written_pinned"] is True
    assert signal_report["signals_after"] > 0
    assert signal_report["prediction_freshness"]["freshness_status"] == "fresh"


def test_stale_predictions_continue_to_block_signal_producer(tmp_path: Path) -> None:
    stale = tmp_path / "stale.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "score": 0.8,
                "confidence": 0.9,
                "generated_at": "2026-05-14T00:00:00+00:00",
                "date": "2026-05-14T00:00:00+00:00",
            }
        ]
    ).to_parquet(stale, index=False)

    report = build_active_signals(
        {
            "paths": {
                "predictions": str(stale),
                "primary_signals": str(tmp_path / "primary.json"),
                "pinned_signals": str(tmp_path / "pinned.json"),
                "report": str(tmp_path / "signal_report.json"),
            },
            "policy": {"max_prediction_age_minutes": 90},
        },
        force_from_predictions=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_predictions_stale"
    assert report["written_pinned"] is False


def test_runner_returns_clear_error_when_market_features_missing(tmp_path: Path) -> None:
    config = tmp_path / "qlib_model.yml"
    model = tmp_path / "qlib_model.joblib"
    _write_config(config)
    _write_model(model)

    report = run_qlib_fresh_predictions(
        market_features_path=tmp_path / "missing.parquet",
        model_path=model,
        output_path=tmp_path / "latest_qlib_predictions.parquet",
        report_path=tmp_path / "report.json",
        config_path=config,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "market_features_missing"
    assert report["rows"] == 0


def test_runner_does_not_enable_live_or_private_exchange_access() -> None:
    files = [
        Path("smartcrypto/qlib_engine/fresh_prediction_runner.py"),
        Path("scripts/run_qlib_fresh_predictions.py"),
        Path("smartcrypto/qlib_engine/predictor.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    forbidden = [
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        "create_order(",
        "fetch_balance(",
        "ccxt.",
        ".env",
        "docker-compose",
        "START_PAPER_24H",
    ]
    for token in forbidden:
        assert token not in combined
