from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.normalized_return import BLOCKED, bps_to_percent, build_normalized_return_sidecar


MODULE_PATH = Path("scripts/build_normalized_return_sidecar.py")


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "target_win": [1, 1, 0],
            "entry_price": [100.0, 100.0, 0.0],
            "exit_price": [110.0, 90.0, 101.0],
            "fechar_side": ["long", "short", "mystery"],
            "volume_posicao": [1.0, 2.0, 0.0],
            "leverage": [2.0, 3.0, 0.0],
            "return_pct": [999.0, 999.0, 999.0],
            "pnl": [20.0, 60.0, 0.0],
        }
    )


def test_calculates_gross_return_for_long_and_short() -> None:
    sidecar, _ = build_normalized_return_sidecar(source_frame(), output_path="out.parquet")

    assert sidecar.loc[0, "gross_return_pct"] == 10.0
    assert sidecar.loc[1, "gross_return_pct"] == 10.0


def test_applies_leverage_and_bps_costs_to_net_return() -> None:
    sidecar, report = build_normalized_return_sidecar(
        source_frame(),
        output_path="out.parquet",
        fee_bps=8,
        slippage_bps=5,
        spread_bps=3,
    )

    assert bps_to_percent(8) == 0.08
    assert report.cost_assumptions["total_cost_pct"] == 0.16
    assert sidecar.loc[0, "leveraged_return_pct"] == 20.0
    assert sidecar.loc[0, "net_return_pct"] == 19.84


def test_flags_invalid_entry_exit_leverage_side_and_volume() -> None:
    frame = source_frame()
    frame.loc[1, "exit_price"] = -1.0
    sidecar, report = build_normalized_return_sidecar(frame, output_path="out.parquet")

    flags = ";".join(sidecar["quality_flags"].tolist())
    assert "entry_price_invalid" in flags
    assert "exit_price_invalid" in flags
    assert "leverage_invalid_defaulted_to_1" in flags
    assert "side_unknown" in flags
    assert "volume_invalid" in flags
    assert report.status == BLOCKED


def test_detects_extreme_net_return() -> None:
    frame = source_frame()
    frame.loc[0, "exit_price"] = 10_000.0

    sidecar, report = build_normalized_return_sidecar(
        frame,
        output_path="out.parquet",
        max_abs_net_return_pct=100,
    )

    assert "net_return_extreme" in sidecar.loc[0, "quality_flags"]
    assert report.outlier_summary["net_return_extreme"] >= 1


def test_report_is_json_serializable() -> None:
    _, report = build_normalized_return_sidecar(source_frame(), output_path="out.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_builder_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("build_normalized_return_sidecar", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_parquet", lambda path: source_frame())

    def fake_write_outputs(*, sidecar, report, output_path, report_path):
        Path(output_path).write_text("placeholder", encoding="utf-8")
        Path(report_path).write_text(json.dumps(report.to_dict()), encoding="utf-8")

    monkeypatch.setattr(module, "write_outputs", fake_write_outputs)
    rc = module.main(
        [
            "--input",
            str(tmp_path / "input.parquet"),
            "--output",
            str(tmp_path / "normalized.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "normalized.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/normalized_return.py").read_text(encoding="utf-8"),
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
