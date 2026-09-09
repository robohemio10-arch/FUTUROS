from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    CERTIFIED_DEV_COMMIT,
    build_qlib_v2_prospective_paper_confirmation_v1,
)


def _market_and_rows(count: int = 450) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle_count = 500 + count * 10 + 30
    candles: list[dict[str, object]] = []
    price = 50000.0
    closes: list[float] = []
    for index in range(candle_count):
        timestamp = base + timedelta(minutes=5 * index)
        wave = 20.0 * math.sin(index / 13.0) + 8.0 * math.sin(index / 5.0)
        close = max(100.0, price + wave)
        candles.append(
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "tf": "5m",
                "ts": timestamp,
                "open": price,
                "high": max(price, close) + 10.0,
                "low": min(price, close) - 10.0,
                "close": close,
                "volume": 1000.0 + 100.0 * math.sin(index / 7.0) + 10.0 * (index % 9),
            }
        )
        closes.append(close)
        price = close

    rows: list[dict[str, object]] = []
    for index in range(count):
        candle_index = 500 + index * 10
        open_time = base + timedelta(minutes=5 * (candle_index + 1))
        close_time = open_time + timedelta(minutes=20)
        side = "long" if index % 2 == 0 else "short"
        ret_5 = closes[candle_index] / closes[candle_index - 5] - 1.0
        net_pnl = (3.0 if ret_5 > 0 else -1.0) if side == "long" else -2.0
        rows.append(
            {
                "event_id": f"event-{index:04d}",
                "trade_id": f"trade-{index:04d}",
                "is_closed": True,
                "validation_status": "ok",
                "symbol_norm": "BTCUSDT",
                "side": side,
                "open_time_utc": open_time.isoformat(),
                "close_time_utc": close_time.isoformat(),
                "entry_price": 50000.0,
                "quantity": 0.002,
                "notional": 100.0,
                "leverage": 2.0,
                "net_pnl": net_pnl,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return pd.DataFrame(candles), rows


def _predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id

    def score(frame: pd.DataFrame) -> np.ndarray:
        signal = frame["feature_ret_5_side"].to_numpy(dtype=float)
        is_long = frame["feature_side_long"].to_numpy(dtype=float) >= 0.5
        return np.where(is_long, signal, 1_000_000.0)

    return score(calibration_x), score(test_x)


def _boundary(rows: list[dict[str, object]], index: int = 330) -> datetime:
    return datetime.fromisoformat(str(rows[index]["open_time_utc"]))


def _certified_provenance(boundary: datetime) -> dict[str, object]:
    return {
        "certified_implementation_commit": "a" * 40,
        "certified_ci_run_id": 123456789,
        "certified_ci_completed_at_utc": boundary - timedelta(seconds=1),
        "freeze_materialized_at_utc": boundary,
    }


def test_freeze_uses_only_pre_boundary_and_scores_future_closed_trades() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["status"] == "observing"
    assert report["freeze_spec"]
    assert report["prospective_closed_trade_count"] == len(rows) - 331
    assert report["prospective_observations"]
    assert all(
        datetime.fromisoformat(item["open_time_utc"]) > boundary
        for item in report["prospective_observations"]
    )
    selected = [item for item in report["prospective_observations"] if item["selected"]]
    assert selected
    assert all(item["side"] == "long" for item in selected)
    assert report["prospective_labels_used_for_training"] is False
    assert report["prospective_labels_used_for_calibration"] is False
    assert report["freeze_provenance_complete"] is False


def test_future_outcome_mutation_does_not_change_frozen_policy_or_selection() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    first = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    mutated = [dict(row) for row in rows]
    for row in mutated[331:]:
        row["net_pnl"] = float(row["net_pnl"]) * -100.0
    second = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=mutated,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        expected_freeze_spec=first["freeze_spec"],
        predictor=_predictor,
    )
    assert second["status"] == "observing"
    assert second["freeze_spec_verified"] is True
    assert first["freeze_spec"]["policy_sha256"] == second["freeze_spec"]["policy_sha256"]
    first_selected = [
        item["trade_id"] for item in first["prospective_observations"] if item["selected"]
    ]
    second_selected = [
        item["trade_id"] for item in second["prospective_observations"] if item["selected"]
    ]
    assert first_selected == second_selected
    assert first["economic_evidence"] != second["economic_evidence"]


def test_pre_boundary_mutation_is_blocked_by_freeze_fingerprint() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    first = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    mutated = [dict(row) for row in rows]
    mutated[100]["net_pnl"] = float(mutated[100]["net_pnl"]) + 0.25
    second = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=mutated,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        expected_freeze_spec=first["freeze_spec"],
        predictor=_predictor,
    )
    assert second["status"] == "blocked"
    assert second["reason"] == "frozen_policy_fingerprint_mismatch"
    assert "pre_boundary_dataset_sha256" in second["freeze_mismatch_fields"]


def test_duplicate_trade_id_fails_closed() -> None:
    market, rows = _market_and_rows()
    rows[200]["trade_id"] = rows[199]["trade_id"]
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=_boundary(rows),
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "duplicate_trade_id_detected"


def test_safety_contract_never_promotes_or_changes_runtime() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=_boundary(rows),
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["prospective_profit_certified"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False


def test_certified_freeze_provenance_binds_dev_commit_ci_and_boundary() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    provenance = _certified_provenance(boundary)
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
        **provenance,
    )
    freeze = report["freeze_spec"]
    assert report["status"] == "observing"
    assert report["freeze_provenance_complete"] is True
    assert freeze["freeze_provenance_complete"] is True
    assert freeze["certified_dev_commit"] == CERTIFIED_DEV_COMMIT
    assert freeze["certified_implementation_commit"] == "a" * 40
    assert freeze["certified_ci_run_id"] == 123456789
    assert freeze["prospective_start_utc"] == boundary.isoformat()
    assert freeze["freeze_materialized_at_utc"] == boundary.isoformat()


def test_freeze_provenance_rejects_boundary_before_ci_completion() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        certified_implementation_commit="b" * 40,
        certified_ci_run_id=987654321,
        certified_ci_completed_at_utc=boundary + timedelta(seconds=1),
        freeze_materialized_at_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["status"] == "observing"
    assert report["freeze_provenance_complete"] is False
    assert report["freeze_spec"]["freeze_provenance_complete"] is False
