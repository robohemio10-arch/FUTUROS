from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning.qlib_long_economic_policy_challenger_v2 import (
    ELIGIBILITY_POLICY,
    OOS_FIELD,
    SELECTOR_FIELD,
    TARGET_NAME,
    build_qlib_long_economic_policy_challenger_v2,
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
        long_profitable = ret_5 > 0
        if side == "long":
            net_pnl = 3.0 if long_profitable else -1.0
        else:
            # Shorts receive artificially huge model scores in the predictor below,
            # but remain economically losing. This isolates structural eligibility
            # from score ranking.
            net_pnl = -2.0
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
                "net_pnl": net_pnl,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return pd.DataFrame(candles), rows


def _long_signal_with_short_boost(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id

    def score(frame: pd.DataFrame) -> np.ndarray:
        long_signal = frame["feature_ret_5_side"].to_numpy(dtype=float)
        is_long = frame["feature_side_long"].to_numpy(dtype=float) >= 0.5
        return np.where(is_long, long_signal, 1_000_000.0)

    return score(calibration_x), score(test_x)


def _reverse_long_signal(
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


def test_v2_uses_absolute_stressed_pnl_target() -> None:
    market, rows = _market_and_rows()
    observed_targets: list[np.ndarray] = []

    def predictor(
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        del train_x, fold_id
        observed_targets.append(train_y.to_numpy(dtype=float).copy())
        return _long_signal_with_short_boost(
            pd.DataFrame(),
            pd.Series(dtype=float),
            calibration_x,
            test_x,
            fold_id="ignored",
        )

    report = build_qlib_long_economic_policy_challenger_v2(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=predictor,
    )

    assert report["target"] == TARGET_NAME
    assert observed_targets
    assert all(float(np.nanmax(target)) <= 3.0 for target in observed_targets)
    assert all(float(np.nanmin(target)) >= -2.0 for target in observed_targets)


def test_v2_short_rows_are_never_treatment_eligible() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_long_economic_policy_challenger_v2(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=_long_signal_with_short_boost,
    )

    assert report["status"] == "ok"
    assert report["eligibility_policy"] == ELIGIBILITY_POLICY
    selected = [
        row
        for row in report["scored_rows"]
        if row.get(OOS_FIELD) is True and row.get(SELECTOR_FIELD) is True
    ]
    assert selected
    assert all(row["side"] == "long" for row in selected)
    assert all(fold["eligible_test_trade_count"] >= fold["selected_trade_count"] for fold in report["folds"])


def test_v2_non_finite_predictor_remains_fail_closed() -> None:
    market, rows = _market_and_rows()

    def non_finite_predictor(
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        del train_x, train_y, fold_id
        return (
            np.full(len(calibration_x), np.nan, dtype=float),
            np.full(len(test_x), np.nan, dtype=float),
        )

    report = build_qlib_long_economic_policy_challenger_v2(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=non_finite_predictor,
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["promotion_allowed"] is False
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False


def test_v2_safety_contract_is_research_only() -> None:
    market, rows = _market_and_rows()
    report = build_qlib_long_economic_policy_challenger_v2(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        additional_execution_stress_bps=0.0,
        predictor=_long_signal_with_short_boost,
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
    assert report["write_performed"] is False
    assert report["prospective_confirmation_required"] is True
