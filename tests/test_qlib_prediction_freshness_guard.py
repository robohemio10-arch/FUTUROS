from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.execution.signal_producer import build_active_signals
from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness


NOW = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def prediction_frame(timestamp: datetime | str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [timestamp],
            "generated_at": [timestamp],
            "symbol": ["BTCUSDT"],
            "pair": ["BTC/USDT:USDT"],
            "score": [0.7],
            "confidence": [0.7],
            "side": ["long"],
            "prob_up": [0.85],
            "model_version": ["qlib_lgbm_v1"],
        }
    )


def write_predictions(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def producer_config(tmp_path: Path, predictions_path: Path) -> dict:
    return {
        "runtime_mode": "paper",
        "source": "qlib",
        "model_version_default": "qlib_lgbm_v1",
        "paths": {
            "predictions": str(predictions_path),
            "primary_signals": str(tmp_path / "primary.json"),
            "pinned_signals": str(tmp_path / "pinned.json"),
            "report": str(tmp_path / "report.json"),
            "summary": str(tmp_path / "summary.json"),
            "decision_log": str(tmp_path / "decision_log.jsonl"),
        },
        "policy": {
            "validity_minutes": 30,
            "min_abs_score": 0.0,
            "min_confidence": 0.0,
            "max_signals": 2,
            "include_top_n_when_threshold_empty": 2,
            "never_overwrite_with_empty": True,
            "require_risk_approved": True,
            "max_prediction_age_minutes": 90,
        },
        "risk": {
            "max_position_usdt": 50.0,
            "leverage": 2.0,
        },
    }


def test_fresh_prediction_allows_signal(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    write_predictions(predictions, prediction_frame(NOW - timedelta(minutes=10)))
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "ok"
    assert report["reason"] is None
    assert report["written_pinned"] is True
    assert report["prediction_freshness"]["freshness_status"] == "fresh"
    assert Path(producer_config(tmp_path, predictions)["paths"]["pinned_signals"]).exists()


def test_stale_prediction_blocks_signal_and_does_not_rewrite_pinned(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    write_predictions(predictions, prediction_frame(NOW - timedelta(days=14)))
    pinned = tmp_path / "pinned.json"
    pinned.write_text(json.dumps({"generated_at": "keep-me", "signals": [{"pair": "ETH/USDT:USDT", "side": "short"}]}), encoding="utf-8")
    config = producer_config(tmp_path, predictions)
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(config, force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_predictions_stale"
    assert report["written_pinned"] is False
    assert json.loads(pinned.read_text(encoding="utf-8"))["generated_at"] == "keep-me"
    assert report["prediction_freshness"]["freshness_status"] == "stale"


def test_fresh_generated_at_blocks_when_input_data_is_old(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    frame = prediction_frame(NOW - timedelta(days=14))
    frame["generated_at"] = NOW - timedelta(minutes=5)
    write_predictions(predictions, frame)
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_input_data_stale"
    assert report["written_pinned"] is False
    assert report["prediction_freshness"]["freshness_status"] == "fresh"
    assert report["prediction_freshness"]["input_data_status"] == "input_data_stale"


def test_missing_input_data_timestamp_blocks(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    frame = prediction_frame(NOW - timedelta(minutes=5)).drop(columns=["date"])
    write_predictions(predictions, frame)
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_input_data_missing"
    assert report["prediction_freshness"]["freshness_status"] == "fresh"
    assert report["prediction_freshness"]["input_data_status"] == "missing"


def test_invalid_input_data_timestamp_blocks(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    frame = prediction_frame(NOW - timedelta(minutes=5))
    frame["date"] = "not-a-date"
    write_predictions(predictions, frame)
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_input_data_invalid"
    assert report["prediction_freshness"]["freshness_status"] == "fresh"
    assert report["prediction_freshness"]["input_data_status"] == "invalid"


def test_missing_prediction_file_blocks_with_clear_reason(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "missing.parquet"
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_predictions_missing"
    assert report["written_pinned"] is False
    assert not Path(producer_config(tmp_path, predictions)["paths"]["pinned_signals"]).exists()


def test_invalid_generated_at_blocks(tmp_path, monkeypatch) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    write_predictions(predictions, prediction_frame("not-a-date"))
    monkeypatch.setattr("smartcrypto.execution.signal_producer.utc_now", lambda: NOW)

    report = build_active_signals(producer_config(tmp_path, predictions), force_from_predictions=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_predictions_timestamp_invalid"
    assert report["written_pinned"] is False
    assert report["prediction_freshness"]["freshness_status"] == "invalid"


def test_freshness_report_is_serializable(tmp_path) -> None:
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    write_predictions(predictions, prediction_frame(NOW - timedelta(minutes=5)))

    report = inspect_qlib_prediction_freshness(predictions, max_allowed_age_minutes=90, now=NOW)

    assert report["freshness_status"] == "fresh"
    assert json.dumps(report, sort_keys=True)


def test_dashboard_and_guard_do_not_reference_exchange_or_runtime_mutations() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/qlib_engine/prediction_freshness.py").read_text(encoding="utf-8"),
            Path("smartcrypto/execution/signal_producer.py").read_text(encoding="utf-8"),
            Path("smartcrypto/dashboard/app.py").read_text(encoding="utf-8"),
        ]
    )
    assert "input_data_status" in text
    assert "input_data_timestamp" in text
    assert "max_input_data_age_minutes" in text
    forbidden = [
        "ccxt",
        "create_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
        "docker-compose",
        "START_PAPER_24H",
    ]
    assert all(token not in text for token in forbidden)
