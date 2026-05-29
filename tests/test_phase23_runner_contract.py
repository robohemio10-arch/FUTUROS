from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


MODULE_PATH = Path("scripts/run_phase23_anti_leakage_audit.py")


def load_runner_module():
    spec = importlib.util.spec_from_file_location("run_phase23_anti_leakage_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def clean_dataset(rows: int = 24) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT"] * rows,
            "open_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": [idx % 2 for idx in range(rows)],
            "open_1m_ret": [idx / 1000 for idx in range(rows)],
            "volume_rel_30": [1.0 + idx / 100 for idx in range(rows)],
        }
    )


def test_runner_accepts_tmp_path_and_does_not_write_default_data(tmp_path) -> None:
    module = load_runner_module()
    dataset = tmp_path / "training_dataset.parquet"
    module.read_dataset = lambda path, max_rows=None: clean_dataset()
    report_path = tmp_path / "phase23_anti_leakage_report.json"
    feature_path = tmp_path / "phase23_feature_audit.json"
    walkforward_path = tmp_path / "phase23_walkforward_clean_report.json"

    report = module.run_phase23_audit(
        dataset=dataset,
        target_column="target_win",
        time_column="open_ts",
        output_report=report_path,
        output_feature_audit=feature_path,
        output_walkforward=walkforward_path,
        folds=2,
        embargo_minutes=1,
    )

    assert report["status"] == "OK"
    assert report_path.exists()
    assert feature_path.exists()
    assert walkforward_path.exists()
    assert not (tmp_path / "data").exists()


def test_runner_generates_three_expected_reports_in_tmp_path(tmp_path) -> None:
    module = load_runner_module()
    dataset = tmp_path / "training_dataset.parquet"
    module.read_dataset = lambda path, max_rows=None: clean_dataset()
    report_path = tmp_path / "report.json"
    feature_path = tmp_path / "feature.json"
    walkforward_path = tmp_path / "walkforward.json"

    module.run_phase23_audit(
        dataset=dataset,
        output_report=report_path,
        output_feature_audit=feature_path,
        output_walkforward=walkforward_path,
        target_column="target_win",
        time_column="open_ts",
        folds=2,
        embargo_minutes=1,
    )

    assert json.loads(report_path.read_text(encoding="utf-8"))["phase"]
    assert json.loads(feature_path.read_text(encoding="utf-8"))["status"] == "OK"
    assert json.loads(walkforward_path.read_text(encoding="utf-8"))["status"] == "OK"


def test_phase23_modules_do_not_reference_exchange_or_live_flags() -> None:
    checked = [
        Path("smartcrypto/ml/anti_leakage_audit.py"),
        Path("smartcrypto/ml/walkforward_split.py"),
        Path("smartcrypto/ml/baseline_evaluation.py"),
        MODULE_PATH,
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
    ]
    assert all(token not in text for token in forbidden)
