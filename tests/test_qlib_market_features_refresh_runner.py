from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from smartcrypto.execution.signal_producer import build_active_signals
from smartcrypto.qlib_engine.fresh_prediction_runner import run_qlib_fresh_predictions
from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features


class DummyProbabilityModel:
    def predict_proba(self, frame):
        return np.tile(np.array([[0.2, 0.8]], dtype=float), (len(frame), 1))


NOW = datetime(2026, 5, 28, 17, 30, tzinfo=timezone.utc)


def _raw_frame(ts_end: datetime = NOW, periods: int = 260) -> pd.DataFrame:
    rows = []
    for symbol, base in [("BTCUSDT", 94000.0), ("ETHUSDT", 3500.0)]:
        for idx in range(periods):
            ts = ts_end - timedelta(minutes=5 * (periods - idx - 1))
            close = base + idx * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "pair": symbol.replace("USDT", "/USDT:USDT"),
                    "tf": "5m",
                    "ts": ts,
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 100 + idx,
                }
            )
    return pd.DataFrame(rows)


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


def test_runner_generates_market_features_with_valid_schema(tmp_path: Path) -> None:
    source = tmp_path / "raw.parquet"
    output = tmp_path / "market_features_60d.parquet"
    report_path = tmp_path / "report.json"
    _raw_frame().to_parquet(source, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=tmp_path / "missing_existing.parquet",
        output_path=output,
        report_path=report_path,
        public_download_enabled=False,
        max_source_age_minutes=15,
        now=NOW,
    )

    assert report["status"] == "ok"
    assert report["operational_feature_schema_ok"] is True
    assert report["lookahead_columns"] == []
    assert report["market_features_age_minutes"] <= 15
    features = pd.read_parquet(output)
    assert {"symbol", "pair", "tf", "ts", "ret_1", "ema_20", "rsi_14", "market_regime"}.issubset(features.columns)
    assert not [column for column in features.columns if column.startswith("future_ret_")]
    json.dumps(json.loads(report_path.read_text(encoding="utf-8")))


def test_recent_source_passes(tmp_path: Path) -> None:
    source = tmp_path / "raw.parquet"
    _raw_frame(NOW - timedelta(minutes=5)).to_parquet(source, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=tmp_path / "none.parquet",
        output_path=tmp_path / "features.parquet",
        report_path=tmp_path / "report.json",
        public_download_enabled=False,
        max_source_age_minutes=15,
        now=NOW,
    )

    assert report["status"] == "ok"


def test_stale_source_blocks(tmp_path: Path) -> None:
    source = tmp_path / "raw.parquet"
    _raw_frame(NOW - timedelta(days=2)).to_parquet(source, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=tmp_path / "none.parquet",
        output_path=tmp_path / "features.parquet",
        report_path=tmp_path / "report.json",
        public_download_enabled=False,
        max_source_age_minutes=15,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "stale_source"


def test_missing_source_returns_clear_error(tmp_path: Path) -> None:
    report = refresh_qlib_market_features(
        source_path=tmp_path / "missing.parquet",
        existing_features_path=tmp_path / "none.parquet",
        output_path=tmp_path / "features.parquet",
        report_path=tmp_path / "report.json",
        public_download_enabled=False,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_source"


def test_invalid_schema_blocks(tmp_path: Path) -> None:
    source = tmp_path / "invalid.parquet"
    pd.DataFrame({"symbol": ["BTCUSDT"], "ts": [NOW]}).to_parquet(source, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=tmp_path / "none.parquet",
        output_path=tmp_path / "features.parquet",
        report_path=tmp_path / "report.json",
        public_download_enabled=False,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "invalid_schema"


def test_fresh_predictions_and_phase13_accept_refreshed_features(tmp_path: Path) -> None:
    current = datetime.now(timezone.utc)
    source = tmp_path / "raw.parquet"
    features = tmp_path / "market_features_60d.parquet"
    predictions = tmp_path / "latest_qlib_predictions.parquet"
    config = tmp_path / "qlib_model.yml"
    model = tmp_path / "model.joblib"
    _raw_frame(current).to_parquet(source, index=False)
    _write_config(config)
    _write_model(model)

    refresh_report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=tmp_path / "none.parquet",
        output_path=features,
        report_path=tmp_path / "refresh_report.json",
        public_download_enabled=False,
        now=current,
    )
    assert refresh_report["status"] == "ok"

    prediction_report = run_qlib_fresh_predictions(
        market_features_path=features,
        model_path=model,
        output_path=predictions,
        report_path=tmp_path / "prediction_report.json",
        config_path=config,
        max_input_data_age_minutes=15,
    )
    assert prediction_report["status"] == "ok"

    signal_report = build_active_signals(
        {
            "runtime_mode": "paper",
            "source": "qlib",
            "paths": {
                "predictions": str(predictions),
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
                "max_input_data_age_minutes": 15,
            },
            "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
        },
        force_from_predictions=True,
        validity_minutes=45,
    )

    assert signal_report["status"] == "ok"
    assert signal_report["signals_after"] > 0
    assert signal_report["written_pinned"] is True


def test_runner_does_not_enable_live_or_private_exchange_access() -> None:
    files = [
        Path("smartcrypto/qlib_engine/market_features_refresh.py"),
        Path("scripts/run_qlib_market_features_refresh.py"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
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
    assert all(token not in text for token in forbidden)
