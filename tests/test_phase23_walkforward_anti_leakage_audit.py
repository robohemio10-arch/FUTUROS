from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.walkforward_anti_leakage_audit import audit_walkforward_anti_leakage


REPO_ROOT = Path(__file__).resolve().parents[1]


def clean_frame(rows: int = 24) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min")
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT"] * rows,
            "open_ts": timestamps,
            "decision_ts": timestamps,
            "split": ["train"] * (rows // 2) + ["test"] * (rows - rows // 2),
            "target_win": [idx % 2 for idx in range(rows)],
            "feature_open_ret": [idx / 1000 for idx in range(rows)],
            "feature_volume": [1.0 + idx / 100 for idx in range(rows)],
        }
    )


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_audit_blocks_future_ret_features() -> None:
    frame = clean_frame().assign(future_ret_3=0.01)

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert report["lookahead_columns"] == ["future_ret_3"]
    assert "prohibited_feature:future_ret_3:future_return_feature" in report["blocking_findings"]


def test_audit_blocks_target_columns_as_features() -> None:
    frame = clean_frame().assign(target_leak=1)

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert "target_leak" in report["prohibited_feature_columns"]


def test_audit_blocks_label_columns_as_features_when_not_target() -> None:
    frame = clean_frame().assign(label_future=1)

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert "label_future" in report["prohibited_feature_columns"]


def test_audit_blocks_temporal_overlap() -> None:
    frame = clean_frame()
    frame.loc[0:12, "split"] = "train"
    frame.loc[12:, "split"] = "test"
    frame.loc[12, "open_ts"] = frame.loc[11, "open_ts"]

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert report["overlap_detected"] is True
    assert "temporal_train_test_overlap" in report["blocking_findings"]


def test_audit_blocks_feature_timestamp_after_decision_timestamp() -> None:
    frame = clean_frame()
    frame["feature_generated_at"] = frame["decision_ts"] + pd.Timedelta(minutes=1)

    report = audit_walkforward_anti_leakage(
        frame=frame,
        report_path=None,
        decision_time_column="decision_ts",
    )

    assert report["status"] == "blocked"
    assert any(
        finding["reason"] == "feature_timestamp_after_decision_timestamp"
        for finding in report["leakage_findings"]
    )


def test_audit_warns_or_blocks_suspicious_perfect_metrics(tmp_path: Path) -> None:
    walkforward = tmp_path / "walkforward.json"
    write_json(walkforward, {"metrics": {"accuracy": 1.0, "f1": 1.0, "roc_auc": 1.0}})

    report = audit_walkforward_anti_leakage(
        frame=clean_frame(),
        walkforward_report_path=walkforward,
        report_path=None,
    )

    assert report["status"] == "warning"
    assert report["suspicious_perfect_metrics"]
    assert "suspicious_perfect_metrics_without_explanation" in report["warnings"]


def test_audit_reports_missing_baselines(tmp_path: Path) -> None:
    walkforward = tmp_path / "walkforward.json"
    write_json(walkforward, {"baselines": {"random": {"accuracy": 0.5}}})

    report = audit_walkforward_anti_leakage(
        frame=clean_frame(),
        walkforward_report_path=walkforward,
        report_path=None,
    )

    assert report["status"] == "warning"
    assert report["missing_baselines"] == ["always_long", "always_short", "no_trade"]


def test_audit_accepts_clean_temporal_dataset() -> None:
    report = audit_walkforward_anti_leakage(frame=clean_frame(), report_path=None)

    assert report["status"] == "ok"
    assert report["temporal_split_valid"] is True
    assert report["prohibited_feature_columns"] == []


def test_audit_blocks_missing_dataset(tmp_path: Path) -> None:
    report = audit_walkforward_anti_leakage(
        dataset_path=tmp_path / "missing.parquet",
        report_path=None,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_dataset"


def test_audit_blocks_empty_dataset() -> None:
    report = audit_walkforward_anti_leakage(frame=clean_frame(0), report_path=None)

    assert report["status"] == "blocked"
    assert "empty_dataset" in report["blocking_findings"]


def test_audit_blocks_missing_timestamp_column() -> None:
    frame = clean_frame().drop(columns=["open_ts"])

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert "missing_timestamp_column:open_ts" in report["blocking_findings"]


def test_audit_blocks_missing_target_column() -> None:
    frame = clean_frame().drop(columns=["target_win"])

    report = audit_walkforward_anti_leakage(frame=frame, report_path=None)

    assert report["status"] == "blocked"
    assert "missing_target_column:target_win" in report["blocking_findings"]


def test_audit_enforces_min_train_test_rows() -> None:
    report = audit_walkforward_anti_leakage(
        frame=clean_frame(8),
        report_path=None,
        min_train_rows=10,
        min_test_rows=10,
    )

    assert report["status"] == "blocked"
    assert "insufficient_train_rows" in report["blocking_findings"]
    assert "insufficient_test_rows" in report["blocking_findings"]


def test_audit_requires_embargo_when_configured() -> None:
    report = audit_walkforward_anti_leakage(
        frame=clean_frame(),
        report_path=None,
        require_embargo=True,
        embargo_minutes=10,
    )

    assert report["status"] == "blocked"
    assert report["embargo_required"] is True
    assert report["embargo_present"] is False
    assert "embargo_missing_or_too_small" in report["blocking_findings"]


def test_cli_phase23_runs_successfully(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.parquet"
    report_path = tmp_path / "phase23_anti_leakage_report.json"
    clean_frame().to_parquet(dataset, index=False)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase23_anti_leakage_audit.py",
            "--dataset",
            str(dataset),
            "--report",
            str(report_path),
            "--timestamp-column",
            "open_ts",
            "--target-column",
            "target_win",
            "--decision-time-column",
            "decision_ts",
            "--min-train-rows",
            "5",
            "--min-test-rows",
            "5",
            "--strict",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master = tmp_path / "trades_master.xlsx"
    training_dataset.write_bytes(b"training")
    trades_master.write_bytes(b"master")

    report = audit_walkforward_anti_leakage(
        frame=clean_frame(),
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "ok"
    assert training_dataset.read_bytes() == b"training"
    assert trades_master.read_bytes() == b"master"


def test_does_not_touch_registry_models_or_signal_producer(tmp_path: Path) -> None:
    registry = tmp_path / "model_registry.json"
    model = tmp_path / "model.joblib"
    signals = tmp_path / "active_freqtrade_signals.json"
    registry.write_text('{"registry": true}', encoding="utf-8")
    model.write_bytes(b"model")
    signals.write_text('{"signals":[]}', encoding="utf-8")

    report = audit_walkforward_anti_leakage(
        frame=clean_frame(),
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "ok"
    assert registry.read_text(encoding="utf-8") == '{"registry": true}'
    assert model.read_bytes() == b"model"
    assert signals.read_text(encoding="utf-8") == '{"signals":[]}'


def test_safety_flags_are_paper_shadow_only() -> None:
    report = audit_walkforward_anti_leakage(frame=clean_frame(), report_path=None)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
