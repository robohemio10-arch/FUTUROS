from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.return_scale_audit import (
    BLOCKED,
    OK,
    WARNING,
    audit_return_scale,
)


MODULE_PATH = Path("scripts/audit_return_pct_scale.py")


def base_frame(returns: list[float]) -> pd.DataFrame:
    rows = len(returns)
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT", "ETHUSDT"] * (rows // 2) + ["BTCUSDT"] * (rows % 2),
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": [1 if value > 0 else 0 for value in returns],
            "return_pct": returns,
            "pnl": [value * 10 for value in returns],
        }
    )


def test_detects_decimal_return_scale() -> None:
    report = audit_return_scale(
        base_frame([0.01, -0.02, 0.03, -0.01]),
        input_path="input.parquet",
    )

    assert report.status == OK
    assert report.scale_hypothesis == "decimal_fraction"


def test_detects_percentage_point_return_scale() -> None:
    report = audit_return_scale(
        base_frame([1.0, -2.0, 3.0, -1.5]),
        input_path="input.parquet",
    )

    assert report.status == OK
    assert report.scale_hypothesis == "percentage_points"


def test_detects_return_multiplied_by_100_unit_mismatch() -> None:
    report = audit_return_scale(
        base_frame([120.0, -140.0, 250.0, -180.0]),
        input_path="input.parquet",
        max_abs_return_pct=100.0,
    )

    assert report.status in {WARNING, BLOCKED}
    assert report.scale_hypothesis == "percentage_multiplied_by_100_or_unit_mismatch"


def test_detects_extreme_outlier() -> None:
    report = audit_return_scale(
        base_frame([1.0, 2.0, 50_000.0]),
        input_path="input.parquet",
        max_abs_return_pct=100.0,
    )

    assert report.status == BLOCKED
    assert report.outlier_summary["return_abs_above_limit"] == 1


def test_detects_invalid_entry_and_exit_prices() -> None:
    frame = base_frame([1.0, -1.0])
    frame["entry_price"] = [0.0, 100.0]
    frame["exit_price"] = [101.0, -1.0]

    report = audit_return_scale(frame, input_path="input.parquet")

    assert report.status == BLOCKED
    assert report.outlier_summary["entry_price_invalid"] == 1
    assert report.outlier_summary["exit_price_invalid"] == 1


def test_detects_invalid_volume_and_leverage_when_present() -> None:
    frame = base_frame([1.0, -1.0])
    frame["volume_posicao"] = [0.0, 10.0]
    frame["leverage"] = [5.0, 0.0]

    report = audit_return_scale(frame, input_path="input.parquet")

    assert report.status == WARNING
    assert report.outlier_summary["volume_invalid"] == 1
    assert report.outlier_summary["leverage_invalid"] == 1


def test_recomputes_approximate_return_with_entry_exit() -> None:
    frame = base_frame([10.0, -10.0])
    frame["entry_price"] = [100.0, 100.0]
    frame["exit_price"] = [110.0, 90.0]
    frame["side"] = ["long", "long"]

    report = audit_return_scale(frame, input_path="input.parquet")

    assert report.recomputation_summary["available"] is True
    assert report.recomputation_summary["abs_error_mean"] == 0.0


def test_report_is_json_serializable() -> None:
    report = audit_return_scale(base_frame([1.0, -1.0]), input_path="input.parquet")

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_runner_accepts_tmp_path_and_does_not_write_data(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("audit_return_pct_scale", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_parquet", lambda path: base_frame([1.0, -1.0]))

    rc = module.main(
        [
            "--input",
            str(tmp_path / "input.parquet"),
            "--sidecar",
            str(tmp_path / "missing_sidecar.parquet"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert rc == 0
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/return_scale_audit.py").read_text(encoding="utf-8"),
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
