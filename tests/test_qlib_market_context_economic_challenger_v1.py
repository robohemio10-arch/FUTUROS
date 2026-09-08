from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning.qlib_market_context_economic_challenger import (
    FOLD_COUNT,
    MODEL_FEATURE_COLUMNS,
    OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
    _InMemoryQlibDataset,
    build_qlib_market_context_economic_challenger_v1,
)


def _market_and_rows(count: int = 360) -> tuple[pd.DataFrame, list[dict[str, object]]]:
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
        side_sign = 1.0 if side == "long" else -1.0
        ret_5 = closes[candle_index] / closes[candle_index - 5] - 1.0
        profitable = ret_5 * side_sign > 0
        rows.append(
            {
                "event_id": f"event-{index:04d}",
                "trade_id": f"trade-{index:04d}",
                "order_id": f"order-{index:04d}",
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
                "net_pnl": 3.0 if profitable else -1.0,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return pd.DataFrame(candles), rows


def _profitable_predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id
    return (
        calibration_x["feature_ret_5_side"].to_numpy(dtype=float),
        test_x["feature_ret_5_side"].to_numpy(dtype=float),
    )


def _losing_predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id
    return (
        -calibration_x["feature_ret_5_side"].to_numpy(dtype=float),
        -test_x["feature_ret_5_side"].to_numpy(dtype=float),
    )



def test_native_qlib_dataset_keeps_economic_calibration_out_of_model_validation() -> None:
    train_x = pd.DataFrame({"f": [0.0, 1.0, 2.0]})
    train_y = pd.Series([0.0, 1.0, 2.0], dtype=float)
    model_valid_x = pd.DataFrame({"f": [3.0, 4.0]})
    model_valid_y = pd.Series([3.0, 4.0], dtype=float)
    calibration_x = pd.DataFrame({"f": [5.0, 6.0]})
    test_x = pd.DataFrame({"f": [7.0, 8.0]})

    dataset = _InMemoryQlibDataset(
        train_x=train_x,
        train_y=train_y,
        model_valid_x=model_valid_x,
        model_valid_y=model_valid_y,
        calibration_x=calibration_x,
        test_x=test_x,
        fold_id="fold_01",
    )

    assert dataset.segments == {"train": "train", "valid": "valid"}
    assert len(dataset.prepare("valid", col_set="label")) == 2
    assert len(dataset.prepare("calibration", col_set="feature")) == 2
    assert "calibration" not in dataset.segments


def test_market_context_test_double_proves_causal_economic_path() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_market_context_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=_profitable_predictor,
    )

    assert report["status"] == "ok"
    assert report["decision"] == "TEST_DOUBLE_MARKET_CONTEXT_ECONOMIC_PATH_VALIDATED"
    assert report["native_qlib_used"] is False
    assert report["point_in_time_alignment"]["coverage"] == 1.0
    assert len(report["folds"]) == FOLD_COUNT
    assert all(fold["status"] == "ok" for fold in report["folds"])
    assert all(fold["chronological_boundary_valid"] for fold in report["folds"])
    assert all(
        fold["outcome_availability_embargo_seconds"]
        == OUTCOME_AVAILABILITY_EMBARGO_SECONDS
        for fold in report["folds"]
    )

    economic = report["economic_evaluation"]
    assert economic["status"] == "ok"
    assert economic["aggregate_treatment_metrics"]["stressed_net_pnl_total"] > 0
    assert economic["aggregate_delta_stressed_net_pnl"] > 0
    assert economic["positive_treatment_net_pnl_fold_count"] == FOLD_COUNT


def test_reverse_score_is_fail_closed() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_market_context_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=_losing_predictor,
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "one_or_more_model_folds_blocked" in report["blockers"]


def test_insufficient_history_blocks_before_predictor_execution() -> None:
    market, rows = _market_and_rows(120)
    called = False

    def predictor(
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        nonlocal called
        called = True
        del train_x, train_y, calibration_x, test_x, fold_id
        return np.asarray([]), np.asarray([])

    report = build_qlib_market_context_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        predictor=predictor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "min_total_trades_not_met"
    assert called is False


def test_missing_recent_candle_blocks_point_in_time_coverage() -> None:
    market, rows = _market_and_rows()
    # Remove the last half of market history; later Paper entries must not be filled
    # forward from stale candles across a 5m gap.
    cutoff = pd.Timestamp(rows[len(rows) // 2]["open_time_utc"])
    market = market.loc[pd.to_datetime(market["ts"], utc=True).lt(cutoff)].copy()

    report = build_qlib_market_context_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        predictor=_profitable_predictor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "point_in_time_market_feature_coverage_not_met"
    assert report["point_in_time_alignment"]["coverage"] < 0.99


def test_predictive_contract_excludes_realized_and_sizing_fields() -> None:
    forbidden = {
        "net_pnl",
        "profit_ratio",
        "trading_fee",
        "funding_fee",
        "notional",
        "quantity",
        "leverage",
        "close_time_utc",
    }
    assert not forbidden.intersection(MODEL_FEATURE_COLUMNS)


def test_safety_contract_has_no_operational_authority() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_market_context_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=_profitable_predictor,
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["write_performed"] is False
