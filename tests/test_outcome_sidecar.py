from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.ml.outcome_sidecar import (
    OutcomeSidecarError,
    build_outcome_sidecar,
    write_sidecar_outputs,
)


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "target_win": [1, 0, 1],
            "return_pct": [0.02, -0.01, 0.03],
            "mfe_pct": [0.03, 0.01, 0.04],
            "mae_pct": [-0.01, -0.02, -0.01],
            "pnl": [10.0, -5.0, 15.0],
            "open_1m_ret": [0.001, -0.002, 0.003],
            "close_1m_ret": [0.004, -0.005, 0.006],
            "close_5m_ret": [0.007, -0.008, 0.009],
        }
    )


def test_sidecar_contains_only_expected_outcomes_when_present() -> None:
    sidecar, report = build_outcome_sidecar(
        source_frame(),
        input_path="input.parquet",
        output_path="sidecar.parquet",
    )

    assert {"trade_id", "symbol", "target_win", "return_pct", "mfe_pct", "mae_pct"}.issubset(
        set(sidecar.columns)
    )
    assert "pnl" in sidecar.columns
    assert report.status == "OK"


def test_sidecar_does_not_contain_open_or_close_features() -> None:
    sidecar, _ = build_outcome_sidecar(
        source_frame(),
        input_path="input.parquet",
        output_path="sidecar.parquet",
    )

    assert "open_1m_ret" not in sidecar.columns
    assert "close_1m_ret" not in sidecar.columns
    assert "close_5m_ret" not in sidecar.columns
    assert "open_1m_ts" in sidecar.columns


def test_sidecar_fails_with_missing_trade_id() -> None:
    with pytest.raises(OutcomeSidecarError, match="id_column_missing"):
        build_outcome_sidecar(
            source_frame().drop(columns=["trade_id"]),
            input_path="input.parquet",
            output_path="sidecar.parquet",
        )


def test_sidecar_fails_with_duplicate_trade_id() -> None:
    frame = source_frame()
    frame.loc[1, "trade_id"] = "t1"

    with pytest.raises(OutcomeSidecarError, match="duplicates"):
        build_outcome_sidecar(frame, input_path="input.parquet", output_path="sidecar.parquet")


def test_sidecar_report_is_json_serializable() -> None:
    _, report = build_outcome_sidecar(
        source_frame(),
        input_path="input.parquet",
        output_path="sidecar.parquet",
    )

    assert json.dumps(report.to_dict(), sort_keys=True)


def test_sidecar_builder_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    sidecar, report = build_outcome_sidecar(
        source_frame(),
        input_path=tmp_path / "input.parquet",
        output_path=tmp_path / "sidecar.parquet",
    )

    def fake_to_parquet(self, path, index=False):
        Path(path).write_text("parquet-placeholder", encoding="utf-8")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fake_to_parquet)
    write_sidecar_outputs(
        sidecar=sidecar,
        report=report,
        output_path=tmp_path / "sidecar.parquet",
        report_path=tmp_path / "report.json",
    )

    assert (tmp_path / "sidecar.parquet").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_sidecar_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/outcome_sidecar.py").read_text(encoding="utf-8"),
            Path("scripts/build_outcome_sidecar.py").read_text(encoding="utf-8"),
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
