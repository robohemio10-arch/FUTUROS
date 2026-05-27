from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.final_financial_quality_resolution import (
    BLOCKED,
    OK,
    WARNING,
    resolve_final_financial_quality_blocks,
)


MODULE_PATH = Path("scripts/resolve_final_financial_quality_blocks.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2"],
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=2, freq="min"),
            "target_win": [1, 0],
            "entry_price_repaired": [100.0, 100.0],
            "exit_price_repaired": [101.0, 99.0],
            "side_repaired": ["LONG", "SHORT"],
            "volume_repaired": [2.0, 3.0],
            "leverage_consistent": [10.0, 5.0],
            "leverage_original": [10.0, 5.0],
            "raw_return_consistent": [10.0, 5.0],
            "pnl_consistent": [2.0, 3.0],
        }
    )


def test_resolves_negative_leverage_to_abs_with_warning() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "leverage_consistent"] = None
    frame.loc[0, "leverage_original"] = -20.0

    resolved, report = resolve_final_financial_quality_blocks(frame, output_path="out.parquet")

    assert resolved.loc[0, "final_quality_status"] == WARNING
    assert resolved.loc[0, "leverage_resolved"] == 20.0
    assert "leverage_negative_abs_resolved" in resolved.loc[0, "final_quality_flags"]
    assert report.leverage_resolution_summary["negative_abs_resolved"] == 1


def test_does_not_resolve_missing_leverage_without_evidence() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "leverage_consistent"] = None
    frame.loc[0, "leverage_original"] = None

    resolved, report = resolve_final_financial_quality_blocks(frame, output_path="out.parquet")

    assert resolved.loc[0, "final_quality_status"] == BLOCKED
    assert "leverage_missing" in resolved.loc[0, "final_quality_flags"]
    assert report.leverage_resolution_summary["missing_unresolved"] == 1


def test_blocks_zero_and_above_max_leverage() -> None:
    frame = pd.concat([source_frame().iloc[:1].copy()] * 2, ignore_index=True)
    frame["trade_id"] = ["zero", "above"]
    frame["leverage_consistent"] = [None, None]
    frame["leverage_original"] = [0.0, 200.0]

    resolved, report = resolve_final_financial_quality_blocks(frame, output_path="out.parquet", max_leverage=125)

    assert resolved["final_quality_status"].tolist() == [BLOCKED, BLOCKED]
    assert report.final_quality_flag_counts["leverage_zero"] == 1
    assert report.final_quality_flag_counts["leverage_above_max"] == 1


def test_blocks_net_return_extreme_when_price_is_impossible() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "exit_price_repaired"] = 1000.0

    resolved, report = resolve_final_financial_quality_blocks(frame, output_path="out.parquet")

    assert resolved.loc[0, "final_quality_status"] == BLOCKED
    assert "price_return_extreme" in resolved.loc[0, "final_quality_flags"]
    assert "net_return_extreme" in resolved.loc[0, "final_quality_flags"]
    assert report.extreme_return_summary["net_return_extreme"] == 1


def test_recalculates_raw_return_when_inputs_are_valid() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "raw_return_consistent"] = None

    resolved, _ = resolve_final_financial_quality_blocks(frame, output_path="out.parquet")

    assert resolved.loc[0, "final_quality_status"] == WARNING
    assert resolved.loc[0, "raw_return_resolved"] == 10.0
    assert "raw_return_recalculated_from_price" in resolved.loc[0, "final_quality_flags"]


def test_keeps_ok_when_no_final_blocks_remain() -> None:
    resolved, report = resolve_final_financial_quality_blocks(source_frame().iloc[:1].copy(), output_path="out.parquet")

    assert resolved.loc[0, "final_quality_status"] == OK
    assert resolved.loc[0, "final_quality_flags"] == OK
    assert report.status == OK


def test_report_is_json_serializable() -> None:
    _, report = resolve_final_financial_quality_blocks(source_frame(), output_path="out.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("resolve_final_financial_quality_blocks", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_parquet", lambda path: source_frame())

    def fake_write_outputs(*, resolved, report, output_path, report_path):
        Path(output_path).write_text("placeholder", encoding="utf-8")
        Path(report_path).write_text(json.dumps(report.to_dict()), encoding="utf-8")

    monkeypatch.setattr(module, "write_outputs", fake_write_outputs)
    rc = module.main(
        [
            "--input",
            str(tmp_path / "input.parquet"),
            "--output",
            str(tmp_path / "resolved.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "resolved.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/final_financial_quality_resolution.py").read_text(encoding="utf-8"),
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
