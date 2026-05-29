from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.leverage_pnl_return_consistency import (
    BLOCKED,
    OK,
    WARNING,
    repair_leverage_pnl_return_consistency,
)


MODULE_PATH = Path("scripts/repair_leverage_pnl_return_consistency.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2"],
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=2, freq="min"),
            "target_win": [1, 1],
            "entry_price_repaired": [100.0, 100.0],
            "exit_price_repaired": [110.0, 90.0],
            "side_repaired": ["LONG", "SHORT"],
            "volume_repaired": [1.0, 2.0],
            "leverage_repaired": [2.0, 3.0],
            "raw_return_repaired": [20.0, 30.0],
            "pnl_repaired": [10.0, 20.0],
            "pnl_fechado": [10.0, 20.0],
            "taxa_lucros_perdas_fechados_pct": [20.0, 30.0],
        }
    )


def test_keeps_ok_line_when_inputs_are_coherent() -> None:
    repaired, report = repair_leverage_pnl_return_consistency(source_frame().iloc[:1].copy(), output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == OK
    assert repaired.loc[0, "consistency_flags"] == OK
    assert report.status == OK


def test_blocks_missing_zero_negative_and_above_max_leverage() -> None:
    frame = pd.concat([source_frame().iloc[:1].copy()] * 4, ignore_index=True)
    frame["trade_id"] = ["missing", "zero", "negative", "above"]
    frame["leverage_repaired"] = [None, 0, -2, 200]

    repaired, report = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet", max_leverage=125)

    assert repaired["consistency_status"].tolist() == [BLOCKED, BLOCKED, BLOCKED, BLOCKED]
    assert report.consistency_flag_counts["leverage_missing"] == 1
    assert report.consistency_flag_counts["leverage_zero"] == 1
    assert report.consistency_flag_counts["leverage_negative"] == 1
    assert report.consistency_flag_counts["leverage_above_max"] == 1


def test_detects_raw_return_sentinel() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "raw_return_repaired"] = 1999.999999

    repaired, report = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == WARNING
    assert "raw_return_sentinel" in repaired.loc[0, "consistency_flags"]
    assert report.raw_return_semantics_summary["sentinel_or_ocr_error"] == 1


def test_detects_raw_return_discrepant_but_warns_when_price_and_pnl_are_coherent() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "raw_return_repaired"] = 0.5

    repaired, _ = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == WARNING
    assert repaired.loc[0, "raw_return_consistent"] == 20.0
    assert "raw_return_discrepant" in repaired.loc[0, "consistency_flags"]


def test_recalculates_long_and_short_returns() -> None:
    repaired, _ = repair_leverage_pnl_return_consistency(source_frame(), output_path="out.parquet")

    assert repaired.loc[0, "price_return_pct"] == 10.0
    assert repaired.loc[1, "price_return_pct"] == 10.0


def test_recalculates_leveraged_return_and_expected_pnl() -> None:
    repaired, _ = repair_leverage_pnl_return_consistency(source_frame(), output_path="out.parquet")

    assert repaired.loc[0, "leveraged_price_return_pct"] == 20.0
    assert repaired.loc[1, "leveraged_price_return_pct"] == 30.0
    assert repaired.loc[0, "expected_pnl_from_price"] == 10.0
    assert repaired.loc[1, "expected_pnl_from_price"] == 20.0


def test_expected_pnl_does_not_multiply_by_leverage() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "entry_price_repaired"] = 94029.9814
    frame.loc[0, "exit_price_repaired"] = 94052.01391
    frame.loc[0, "volume_repaired"] = 0.10613
    frame.loc[0, "leverage_repaired"] = 20.0
    frame.loc[0, "pnl_repaired"] = 2.33831
    frame.loc[0, "raw_return_repaired"] = 0.46862

    repaired, _ = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == OK
    assert abs(repaired.loc[0, "expected_pnl_from_price"] - 2.33831) < 0.001
    assert repaired.loc[0, "expected_pnl_from_price"] != repaired.loc[0, "expected_pnl_from_price"] * 20.0


def test_raw_return_can_match_leveraged_roi_while_pnl_matches_unleveraged_price_delta() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "entry_price_repaired"] = 100.0
    frame.loc[0, "exit_price_repaired"] = 101.0
    frame.loc[0, "volume_repaired"] = 5.0
    frame.loc[0, "leverage_repaired"] = 10.0
    frame.loc[0, "raw_return_repaired"] = 10.0
    frame.loc[0, "pnl_repaired"] = 5.0

    repaired, _ = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == OK
    assert repaired.loc[0, "price_return_pct"] == 1.0
    assert repaired.loc[0, "leveraged_price_return_pct"] == 10.0
    assert repaired.loc[0, "expected_pnl_from_price"] == 5.0
    assert repaired.loc[0, "raw_return_consistent"] == 10.0


def test_detects_pnl_incompatible() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "pnl_repaired"] = 1.0

    repaired, report = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == WARNING
    assert "pnl_incompatible" in repaired.loc[0, "consistency_flags"]
    assert repaired.loc[0, "pnl_warning_only"]
    assert repaired.loc[0, "pnl_consistent"] == 10.0
    assert repaired.loc[0, "pnl_semantics_guess"] == "mixed_or_untrusted_pnl"
    assert report.pnl_consistency_summary["incompatible"] == 1
    assert report.warning_summary["pnl_incompatible"] == 1


def test_blocks_pnl_incompatible_when_critical_field_is_invalid() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "pnl_repaired"] = 1.0
    frame.loc[0, "leverage_repaired"] = None

    repaired, report = repair_leverage_pnl_return_consistency(frame, output_path="out.parquet")

    assert repaired.loc[0, "consistency_status"] == BLOCKED
    assert "pnl_incompatible" in repaired.loc[0, "consistency_flags"]
    assert "leverage_missing" in repaired.loc[0, "consistency_flags"]
    assert not repaired.loc[0, "pnl_warning_only"]
    assert report.blocked_summary["leverage_missing"] == 1


def test_report_is_json_serializable() -> None:
    _, report = repair_leverage_pnl_return_consistency(source_frame(), output_path="out.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("repair_leverage_pnl_return_consistency", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_parquet", lambda path: source_frame())

    def fake_write_outputs(*, repaired, report, output_path, report_path):
        Path(output_path).write_text("placeholder", encoding="utf-8")
        Path(report_path).write_text(json.dumps(report.to_dict()), encoding="utf-8")

    monkeypatch.setattr(module, "write_outputs", fake_write_outputs)
    rc = module.main(
        [
            "--input",
            str(tmp_path / "financial_inputs.parquet"),
            "--output",
            str(tmp_path / "consistency.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "consistency.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/leverage_pnl_return_consistency.py").read_text(encoding="utf-8"),
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
