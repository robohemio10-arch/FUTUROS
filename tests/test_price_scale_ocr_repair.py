from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.price_scale_ocr_repair import (
    BLOCKED,
    OK,
    repair_price_scale_ocr_anomalies,
)


MODULE_PATH = Path("scripts/repair_price_scale_ocr_anomalies.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3"],
            "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "entry_price": [100.0, 1000.0, 10.0],
            "exit_price": [101.0, 1010.0, 10.1],
            "open_1m_close": [100.0, 100.0, 100.0],
            "close_1m_close": [101.0, 101.0, 101.0],
            "open_5m_close": [100.5, 100.5, 100.5],
            "close_5m_close": [101.5, 101.5, 101.5],
            "fechar_side": ["long", "long", "long"],
            "volume_posicao": [1.0, 1.0, 1.0],
            "leverage": [20.0, 20.0, 20.0],
        }
    )


def test_keeps_plausible_price_unchanged() -> None:
    repaired, report = repair_price_scale_ocr_anomalies(source_frame().iloc[:1].copy(), output_path="out.parquet")

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "exit_price_repaired"] == 101.0
    assert repaired.loc[0, "entry_price_scale_factor"] == 1.0
    assert repaired.loc[0, "price_scale_repair_status"] == OK
    assert report.status == OK


def test_repairs_price_10x_above() -> None:
    repaired, _ = repair_price_scale_ocr_anomalies(source_frame().iloc[[1]].copy(), output_path="out.parquet")

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "entry_price_scale_factor"] == 0.1
    assert "entry_price_scale_corrected" in repaired.loc[0, "price_scale_repair_flags"]


def test_repairs_price_10x_below() -> None:
    repaired, _ = repair_price_scale_ocr_anomalies(source_frame().iloc[[2]].copy(), output_path="out.parquet")

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "entry_price_scale_factor"] == 10.0


def test_repairs_price_100x_above() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "entry_price"] = 10_000.0
    frame.loc[0, "exit_price"] = 10_100.0

    repaired, _ = repair_price_scale_ocr_anomalies(frame, output_path="out.parquet")

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "entry_price_scale_factor"] == 0.01


def test_repairs_price_100x_below() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "entry_price"] = 1.0
    frame.loc[0, "exit_price"] = 1.01

    repaired, _ = repair_price_scale_ocr_anomalies(frame, output_path="out.parquet")

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "entry_price_scale_factor"] == 100.0


def test_blocks_price_without_reference() -> None:
    frame = source_frame().iloc[:1].copy()
    frame["open_1m_close"] = None
    frame["open_5m_close"] = None

    repaired, report = repair_price_scale_ocr_anomalies(frame, output_path="out.parquet")

    assert repaired.loc[0, "price_scale_repair_status"] == BLOCKED
    assert "entry_reference_missing" in repaired.loc[0, "price_scale_repair_flags"]
    assert report.status == BLOCKED


def test_blocks_ambiguous_reference() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "open_1m_close"] = 100.0
    frame.loc[0, "open_5m_close"] = 200.0

    repaired, _ = repair_price_scale_ocr_anomalies(frame, output_path="out.parquet")

    assert repaired.loc[0, "price_scale_repair_status"] == BLOCKED
    assert "entry_reference_ambiguous" in repaired.loc[0, "price_scale_repair_flags"]


def test_blocks_correction_that_still_generates_absurd_return() -> None:
    frame = source_frame().iloc[:1].copy()
    frame.loc[0, "entry_price"] = 1000.0
    frame.loc[0, "exit_price"] = 2000.0
    frame.loc[0, "open_1m_close"] = 100.0
    frame.loc[0, "open_5m_close"] = 100.0
    frame.loc[0, "close_1m_close"] = 200.0
    frame.loc[0, "close_5m_close"] = 200.0

    repaired, _ = repair_price_scale_ocr_anomalies(
        frame,
        output_path="out.parquet",
        max_corrected_price_return_pct=20,
    )

    assert repaired.loc[0, "entry_price_repaired"] == 100.0
    assert repaired.loc[0, "exit_price_repaired"] == 200.0
    assert repaired.loc[0, "price_scale_repair_status"] == BLOCKED
    assert "corrected_price_return_extreme" in repaired.loc[0, "price_scale_repair_flags"]


def test_calculates_distance_before_after_and_factor() -> None:
    repaired, _ = repair_price_scale_ocr_anomalies(source_frame().iloc[[1]].copy(), output_path="out.parquet")

    assert repaired.loc[0, "entry_price_distance_pct_before"] > 800
    assert repaired.loc[0, "entry_price_distance_pct_after"] < 1
    assert repaired.loc[0, "entry_price_scale_factor"] == 0.1


def test_report_is_json_serializable() -> None:
    _, report = repair_price_scale_ocr_anomalies(source_frame(), output_path="out.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("repair_price_scale_ocr_anomalies", MODULE_PATH)
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
            str(tmp_path / "price_repaired.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "price_repaired.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/price_scale_ocr_repair.py").read_text(encoding="utf-8"),
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
