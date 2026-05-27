from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.trade_financial_input_repair import (
    BLOCKED,
    OK,
    WARNING,
    parse_numeric_value,
    repair_trade_financial_inputs,
)


MODULE_PATH = Path("scripts/repair_trade_financial_inputs.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "target_win": [1, 0, 1],
            "entry_price": [100.0, 100.0, 100.0],
            "exit_price": [110.0, 90.0, 101.0],
            "fechar_side": ["long", "short", "mystery"],
            "volume_posicao": [1.0, "2,5", 1.0],
            "leverage": ["20x", 3.0, 1.0],
            "return_pct": [10.0, 10.0, 999.0],
            "pnl": [10.0, 25.0, 1.0],
            "pnl_fechado": [10.0, 25.0, 1.0],
            "taxa_lucros_perdas_fechados_pct": [10.0, 10.0, 999.0],
        }
    )


def test_normalizes_leverage_string_and_numeric() -> None:
    repaired, _ = repair_trade_financial_inputs(source_frame().iloc[:2].copy(), output_path="out.parquet")

    assert repaired.loc[0, "leverage_repaired"] == 20.0
    assert repaired.loc[1, "leverage_repaired"] == 3.0


def test_blocks_leverage_zero_and_above_max() -> None:
    frame = source_frame().iloc[:2].copy()
    frame.loc[0, "leverage"] = 0
    frame.loc[1, "leverage"] = 200

    repaired, report = repair_trade_financial_inputs(frame, output_path="out.parquet", max_leverage=125)

    assert repaired["repair_status"].tolist() == [BLOCKED, BLOCKED]
    assert report.repair_flag_counts["leverage_invalid"] == 1
    assert report.repair_flag_counts["leverage_above_max"] == 1


def test_normalizes_volume_with_decimal_comma() -> None:
    assert parse_numeric_value("2,5") == 2.5
    repaired, _ = repair_trade_financial_inputs(source_frame().iloc[:2].copy(), output_path="out.parquet")

    assert repaired.loc[1, "volume_repaired"] == 2.5


def test_blocks_zero_and_negative_volume() -> None:
    frame = source_frame().iloc[:2].copy()
    frame.loc[0, "volume_posicao"] = 0
    frame.loc[1, "volume_posicao"] = -1

    repaired, report = repair_trade_financial_inputs(frame, output_path="out.parquet")

    assert repaired["repair_status"].tolist() == [BLOCKED, BLOCKED]
    assert report.repair_flag_counts["volume_invalid"] == 2


def test_validates_entry_and_exit_price_positive() -> None:
    frame = source_frame().iloc[:2].copy()
    frame.loc[0, "entry_price"] = 0
    frame.loc[1, "exit_price"] = -1

    repaired, report = repair_trade_financial_inputs(frame, output_path="out.parquet")

    assert repaired["repair_status"].tolist() == [BLOCKED, BLOCKED]
    assert report.repair_flag_counts["entry_price_invalid"] == 1
    assert report.repair_flag_counts["exit_price_invalid"] == 1


def test_calculates_long_and_short_returns_correctly() -> None:
    repaired, _ = repair_trade_financial_inputs(source_frame().iloc[:2].copy(), output_path="out.parquet")

    assert repaired.loc[0, "price_return_pct"] == 10.0
    assert repaired.loc[1, "price_return_pct"] == 10.0


def test_marks_side_unknown_when_evidence_is_missing() -> None:
    repaired, _ = repair_trade_financial_inputs(source_frame(), output_path="out.parquet")

    assert repaired.loc[2, "side_repaired"] == "UNKNOWN"
    assert "side_unknown" in repaired.loc[2, "repair_flags"]
    assert repaired.loc[2, "repair_status"] == BLOCKED


def test_detects_raw_return_discrepant() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "return_pct"] = 50.0

    repaired, report = repair_trade_financial_inputs(frame, output_path="out.parquet")

    assert "raw_return_discrepant" in repaired.loc[0, "repair_flags"]
    assert report.repair_flag_counts["raw_return_discrepant"] == 1


def test_classifies_ok_warning_and_blocked_rows() -> None:
    frame = source_frame()
    frame.loc[1, "return_pct"] = 50.0

    repaired, report = repair_trade_financial_inputs(frame, output_path="out.parquet")

    assert repaired.loc[0, "repair_status"] == OK
    assert repaired.loc[1, "repair_status"] == WARNING
    assert repaired.loc[2, "repair_status"] == BLOCKED
    assert report.status == WARNING


def test_report_is_json_serializable() -> None:
    _, report = repair_trade_financial_inputs(source_frame(), output_path="out.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("repair_trade_financial_inputs", MODULE_PATH)
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
            str(tmp_path / "trade_enriched.parquet"),
            "--output",
            str(tmp_path / "repaired.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "repaired.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/trade_financial_input_repair.py").read_text(encoding="utf-8"),
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
