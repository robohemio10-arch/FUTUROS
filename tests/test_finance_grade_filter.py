from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.finance_grade_filter import (
    BLOCKED,
    OK,
    build_finance_grade_sidecar_input,
)


MODULE_PATH = Path("scripts/build_finance_grade_sidecar_input.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["ok1", "blocked1", "ok_with_bad_flag", "extreme1"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=4, freq="min"),
            "target_win": [1, 0, 1, 0],
            "entry_price_repaired": [100.0, 100.0, 100.0, 100.0],
            "exit_price_repaired": [101.0, 99.0, 102.0, 500.0],
            "side_repaired": ["LONG", "SHORT", "LONG", "LONG"],
            "volume_repaired": [1.0, 2.0, 1.0, 1.0],
            "leverage_resolved": [10.0, None, 10.0, 20.0],
            "raw_return_resolved": [10.0, None, 20.0, None],
            "pnl_resolved": [1.0, None, 2.0, None],
            "price_return_pct": [1.0, 1.0, 2.0, 400.0],
            "leveraged_price_return_pct": [10.0, None, 20.0, 8000.0],
            "final_quality_status": [OK, BLOCKED, OK, BLOCKED],
            "final_quality_flags": [OK, "leverage_missing", "net_return_extreme", "price_return_extreme"],
        }
    )


def test_accepts_only_final_quality_status_ok() -> None:
    accepted, rejected, report = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert accepted["trade_id"].tolist() == ["ok1"]
    assert report.rows_accepted == 1
    assert report.rows_rejected == 3
    assert set(rejected["trade_id"]) == {"blocked1", "ok_with_bad_flag", "extreme1"}


def test_rejects_blocked_status() -> None:
    accepted, rejected, _ = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert "blocked1" not in set(accepted["trade_id"])
    assert "blocked1" in set(rejected["trade_id"])


def test_rejects_leverage_missing_flag() -> None:
    accepted, rejected, report = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert "blocked1" not in set(accepted["trade_id"])
    assert "leverage_missing" in report.rejected_flag_counts
    assert "blocked1" in set(rejected["trade_id"])


def test_rejects_net_return_extreme_flag_even_when_status_is_ok() -> None:
    accepted, rejected, report = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert "ok_with_bad_flag" not in set(accepted["trade_id"])
    assert "ok_with_bad_flag" in set(rejected["trade_id"])
    assert report.rejected_flag_counts["net_return_extreme"] == 1


def test_rejects_price_return_extreme_flag() -> None:
    accepted, rejected, report = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert "extreme1" not in set(accepted["trade_id"])
    assert "extreme1" in set(rejected["trade_id"])
    assert report.rejected_flag_counts["price_return_extreme"] == 1


def test_writes_accepted_and_rejected_files(tmp_path) -> None:
    from smartcrypto.ml.finance_grade_filter import write_outputs

    accepted, rejected, report = build_finance_grade_sidecar_input(
        source_frame(),
        output_path=tmp_path / "accepted.parquet",
        rejected_output_path=tmp_path / "rejected.parquet",
    )
    write_outputs(
        accepted=accepted,
        rejected=rejected,
        report=report,
        output_path=tmp_path / "accepted.parquet",
        rejected_output_path=tmp_path / "rejected.parquet",
        report_path=tmp_path / "report.json",
    )

    assert (tmp_path / "accepted.parquet").exists()
    assert (tmp_path / "rejected.parquet").exists()
    assert (tmp_path / "report.json").exists()


def test_report_is_json_serializable_and_ratio_is_correct() -> None:
    _, _, report = build_finance_grade_sidecar_input(
        source_frame(), output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert report.acceptance_ratio == 0.25
    assert json.dumps(report.to_dict(), sort_keys=True)


def test_blocks_when_accepted_is_zero() -> None:
    frame = source_frame()
    frame["final_quality_status"] = BLOCKED
    frame["final_quality_flags"] = "leverage_missing"

    accepted, rejected, report = build_finance_grade_sidecar_input(
        frame, output_path="accepted.parquet", rejected_output_path="rejected.parquet"
    )

    assert accepted.empty
    assert len(rejected) == len(frame)
    assert report.status == BLOCKED


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("build_finance_grade_sidecar_input", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_parquet", lambda path: source_frame())

    def fake_write_outputs(*, accepted, rejected, report, output_path, rejected_output_path, report_path):
        Path(output_path).write_text("accepted", encoding="utf-8")
        Path(rejected_output_path).write_text("rejected", encoding="utf-8")
        Path(report_path).write_text(json.dumps(report.to_dict()), encoding="utf-8")

    monkeypatch.setattr(module, "write_outputs", fake_write_outputs)
    rc = module.main(
        [
            "--input",
            str(tmp_path / "input.parquet"),
            "--output",
            str(tmp_path / "accepted.parquet"),
            "--rejected-output",
            str(tmp_path / "rejected.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "accepted.parquet").exists()
    assert (tmp_path / "rejected.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/finance_grade_filter.py").read_text(encoding="utf-8"),
            MODULE_PATH.read_text(encoding="utf-8"),
        ]
    )
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
