from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.data.data_quality_report import build_data_quality_report
from smartcrypto.data.dataset_manifest import build_dataset_manifest


ROOT = Path(__file__).resolve().parents[1]


def _frame(**overrides):
    rows = pd.DataFrame(
        {
            "order_id": ["1", "2", "3"],
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:01:00Z",
                    "2026-01-01T00:02:00Z",
                ],
                utc=True,
            ),
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "side": ["long", "short", "long"],
            "price": [100.0, 200.0, 101.0],
            "volume": [1.0, 2.0, 1.5],
            "enriched": [True, True, False],
            "open_candle_timestamp": pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:01:00Z",
                    "2026-01-01T00:02:00Z",
                ],
                utc=True,
            ),
            "close_candle_timestamp": pd.to_datetime(
                [
                    "2026-01-01T00:01:00Z",
                    "2026-01-01T00:02:00Z",
                    "2026-01-01T00:03:00Z",
                ],
                utc=True,
            ),
        }
    )
    for key, value in overrides.items():
        rows[key] = value
    return rows


def _write(path: Path, frame: pd.DataFrame) -> Path:
    if path.suffix == ".csv":
        frame.to_csv(path, index=False)
    elif path.suffix == ".jsonl":
        frame.to_json(path, orient="records", lines=True, date_format="iso")
    else:
        frame.to_parquet(path, index=False)
    return path


def _quality(path: Path, *, strict: bool = True, **safety):
    return build_data_quality_report(
        datasets={"trades_master": path},
        report_path=None,
        strict=strict,
        safety_overrides=safety or None,
    )


def test_data_quality_blocks_missing_required_input_in_strict_mode(tmp_path):
    report = _quality(tmp_path / "missing.parquet", strict=True)

    assert report["status"] == "blocked"
    assert any("missing_input" in item for item in report["validation_errors"])


def test_data_quality_blocks_empty_dataset(tmp_path):
    path = _write(tmp_path / "empty.parquet", _frame().iloc[0:0])

    report = _quality(path, strict=True)

    assert report["status"] == "blocked"
    assert report["datasets"]["trades_master"]["rows"] == 0
    assert "empty_dataset:trades_master" in report["validation_errors"]


def test_data_quality_detects_duplicate_order_ids(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame(order_id=["1", "1", "2"]))

    report = _quality(path, strict=True)

    metrics = report["datasets"]["trades_master"]
    assert report["status"] == "blocked"
    assert metrics["duplicate_order_id_rows"] == 1


def test_data_quality_detects_missing_prices(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame(price=[100.0, np.nan, 101.0]))

    report = _quality(path, strict=True)

    assert report["status"] == "blocked"
    assert report["datasets"]["trades_master"]["missing_price_rows"] == 1


def test_data_quality_detects_missing_timestamps(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame(timestamp=[pd.NaT, "bad", "2026-01-01T00:02:00Z"]))

    report = _quality(path, strict=True)

    metrics = report["datasets"]["trades_master"]
    assert report["status"] == "blocked"
    assert metrics["missing_time_rows"] == 1
    assert metrics["invalid_timestamp_rows"] == 2


def test_data_quality_detects_invalid_symbols_and_sides(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame(symbol=["BTCUSDT", "BAD SYMBOL!", ""], side=["long", "banana", ""]))

    report = _quality(path, strict=True)

    metrics = report["datasets"]["trades_master"]
    assert report["status"] == "blocked"
    assert metrics["invalid_symbol_rows"] == 2
    assert metrics["invalid_side_rows"] == 2


def test_data_quality_detects_nan_and_infinite_values(tmp_path):
    frame = _frame()
    frame["feature_a"] = [1.0, np.nan, np.inf]
    path = _write(tmp_path / "trades.parquet", frame)

    report = _quality(path, strict=True)

    metrics = report["datasets"]["trades_master"]
    assert report["status"] == "blocked"
    assert metrics["rows_with_nan"] == 1
    assert metrics["rows_with_infinite_values"] == 1


def test_data_quality_detects_temporal_gaps(tmp_path):
    path = _write(
        tmp_path / "trades.parquet",
        _frame(
            timestamp=pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:01:00Z",
                    "2026-01-01T01:00:00Z",
                ],
                utc=True,
            )
        ),
    )

    report = _quality(path, strict=False)

    metrics = report["datasets"]["trades_master"]
    assert metrics["temporal_gaps_count"] == 1
    assert metrics["largest_temporal_gap_seconds"] >= 3540


def test_data_quality_reports_enriched_and_unenriched_rows(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame(enriched=[True, False, False]))

    report = _quality(path, strict=False)

    metrics = report["datasets"]["trades_master"]
    assert metrics["enriched_rows"] == 1
    assert metrics["unenriched_rows"] == 2


def test_data_quality_blocks_unsafe_safety_flags(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame())

    report = build_data_quality_report(
        datasets={"trades_master": path},
        report_path=None,
        strict=True,
        safety_overrides={"live_trading_enabled": True, "order_submission_enabled": True},
    )

    assert report["status"] == "blocked"
    assert any(item.startswith("unsafe_safety_flag:") for item in report["validation_errors"])


def test_dataset_manifest_hashes_files_deterministically(tmp_path):
    path = _write(tmp_path / "trades.csv", _frame())

    first = build_dataset_manifest(inputs=[path], output_path=None, timestamp_column="timestamp", strict=True)
    second = build_dataset_manifest(inputs=[path], output_path=None, timestamp_column="timestamp", strict=True)

    assert first["files"][0]["sha256"] == second["files"][0]["sha256"]
    assert first["files"][0]["schema_hash"] == second["files"][0]["schema_hash"]


def test_dataset_manifest_detects_missing_files(tmp_path):
    manifest = build_dataset_manifest(inputs=[tmp_path / "missing.parquet"], output_path=None, strict=True)

    assert manifest["status"] == "blocked"
    assert manifest["files"][0]["exists"] is False


def test_dataset_manifest_records_schema_hash(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame())

    manifest = build_dataset_manifest(inputs=[path], output_path=None, timestamp_column="timestamp", strict=True)

    assert manifest["files"][0]["schema_hash"]


def test_dataset_manifest_records_rows_columns_and_timestamps(tmp_path):
    path = _write(tmp_path / "trades.parquet", _frame())

    manifest = build_dataset_manifest(inputs=[path], output_path=None, timestamp_column="timestamp", strict=True)

    item = manifest["files"][0]
    assert item["rows"] == 3
    assert item["columns"] >= 8
    assert item["min_timestamp_utc"].startswith("2026-01-01T00:00:00")
    assert item["max_timestamp_utc"].startswith("2026-01-01T00:02:00")


def test_cli_build_data_quality_report_runs_successfully(tmp_path):
    input_path = _write(tmp_path / "trades.parquet", _frame())
    report_path = tmp_path / "quality.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_data_quality_report.py"),
            "--trades-master",
            str(input_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert report_path.exists()


def test_cli_build_dataset_manifest_runs_successfully(tmp_path):
    input_path = _write(tmp_path / "trades.parquet", _frame())
    output_path = tmp_path / "manifest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_dataset_manifest.py"),
            "--inputs",
            str(input_path),
            "--output",
            str(output_path),
            "--dataset-role",
            "trades_master",
            "--timestamp-column",
            "timestamp",
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert output_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path):
    trades_master = tmp_path / "trades_master.parquet"
    training_dataset = tmp_path / "training_dataset.parquet"
    _write(trades_master, _frame())
    _write(training_dataset, _frame())
    before_master = trades_master.read_bytes()
    before_training = training_dataset.read_bytes()

    build_data_quality_report(
        datasets={"microbatch": _write(tmp_path / "microbatch.parquet", _frame())},
        report_path=None,
        strict=True,
    )

    assert trades_master.read_bytes() == before_master
    assert training_dataset.read_bytes() == before_training


def test_does_not_touch_registry_models_signal_producer_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}
    input_path = _write(tmp_path / "trades.parquet", _frame())

    build_dataset_manifest(inputs=[input_path], output_path=None, timestamp_column="timestamp", strict=True)
    build_data_quality_report(datasets={"trades_master": input_path}, report_path=None, strict=True)

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before
