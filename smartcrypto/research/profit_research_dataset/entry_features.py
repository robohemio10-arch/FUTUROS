"""Leakage-safe entry-time features from completed candles."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .candle_alignment import timeframe_seconds
from .contracts import ENTRY_FEATURE_COLUMNS


def attach_entry_features(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    *,
    timeframe: str,
) -> pd.DataFrame:
    output = trades.copy()
    for column in ENTRY_FEATURE_COLUMNS:
        output[column] = pd.NA
    output["entry_feature_timestamp_utc"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["entry_feature_complete"] = False
    output["entry_feature_missing_reason"] = pd.Series(
        pd.NA, index=output.index, dtype="string"
    )
    seconds = timeframe_seconds(timeframe)
    grouped = {
        str(symbol): _with_availability(group, seconds)
        for symbol, group in candles.groupby("symbol", sort=True)
    }
    for index, trade in output.iterrows():
        if str(trade.get("candle_alignment_status")) != "aligned":
            output.at[index, "entry_feature_missing_reason"] = "trade_not_candle_aligned"
            continue
        source = grouped.get(str(trade["symbol"]))
        if source is None:
            output.at[index, "entry_feature_missing_reason"] = "symbol_history_missing"
            continue
        open_time = pd.Timestamp(trade["open_time_utc"])
        stop = int(source["availability_ts"].searchsorted(open_time, side="right"))
        history = source.iloc[max(0, stop - 101) : stop]
        if history.empty:
            output.at[index, "entry_feature_missing_reason"] = "completed_candle_missing"
            continue
        values = compute_entry_feature_row(history, trade)
        output.at[index, "entry_feature_timestamp_utc"] = history.iloc[-1]["availability_ts"]
        for column, value in values.items():
            output.at[index, column] = value
        required = [
            "entry_return_24",
            "entry_rolling_volatility_24",
            "entry_atr_normalized_14",
            "entry_volume_relative_20",
            "entry_distance_from_ma20",
        ]
        complete = all(pd.notna(values.get(column)) for column in required)
        output.at[index, "entry_feature_complete"] = complete
        if not complete:
            output.at[index, "entry_feature_missing_reason"] = "insufficient_completed_lookback"
    violations = output["entry_feature_timestamp_utc"].gt(output["open_time_utc"]).fillna(False)
    if bool(violations.any()):
        raise ValueError("entry_feature_temporal_leakage_detected")
    return output


def compute_entry_feature_row(history: pd.DataFrame, trade: pd.Series) -> dict[str, Any]:
    close = pd.to_numeric(history["close"], errors="coerce")
    high = pd.to_numeric(history["high"], errors="coerce")
    low = pd.to_numeric(history["low"], errors="coerce")
    open_price = pd.to_numeric(history["open"], errors="coerce")
    volume = pd.to_numeric(history["volume"], errors="coerce")
    current_close = float(close.iloc[-1])
    result: dict[str, Any] = {}
    for lookback in (1, 3, 6, 12, 24):
        result[f"entry_return_{lookback}"] = (
            current_close / float(close.iloc[-lookback - 1]) - 1.0
            if len(close) > lookback and float(close.iloc[-lookback - 1]) != 0
            else None
        )
    returns = close.pct_change(fill_method=None)
    result["entry_rolling_volatility_24"] = (
        float(returns.tail(24).std(ddof=1)) if returns.tail(24).notna().sum() >= 2 else None
    )
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.tail(14).mean() if len(true_range) >= 14 else np.nan
    result["entry_atr_normalized_14"] = float(atr / current_close) if pd.notna(atr) and current_close else None
    current_range = float(high.iloc[-1] - low.iloc[-1])
    result["entry_relative_range"] = current_range / current_close if current_close else None
    volume_mean = volume.tail(20).mean() if len(volume) >= 20 else np.nan
    result["entry_volume_relative_20"] = (
        float(volume.iloc[-1] / volume_mean)
        if pd.notna(volume.iloc[-1]) and pd.notna(volume_mean) and volume_mean != 0
        else None
    )
    ma20 = close.tail(20).mean() if len(close) >= 20 else np.nan
    result["entry_distance_from_ma20"] = (
        float(current_close / ma20 - 1.0) if pd.notna(ma20) and ma20 != 0 else None
    )
    result["entry_slope_6"] = _normalized_slope(close.tail(6))
    result["entry_momentum_6"] = result["entry_return_6"]
    slope = result["entry_slope_6"]
    result["entry_trend_regime"] = (
        "uptrend" if slope is not None and slope > 0.0002 else "downtrend" if slope is not None and slope < -0.0002 else "sideways"
    )
    current_vol = result["entry_rolling_volatility_24"]
    historical_vol = returns.rolling(24).std().dropna().tail(100)
    median_vol = float(historical_vol.median()) if not historical_vol.empty else None
    result["entry_volatility_regime"] = _volatility_regime(current_vol, median_vol)
    candle_open = float(open_price.iloc[-1])
    candle_high = float(high.iloc[-1])
    candle_low = float(low.iloc[-1])
    result["entry_candle_direction"] = (
        "bullish" if current_close > candle_open else "bearish" if current_close < candle_open else "flat"
    )
    result["entry_body_ratio"] = abs(current_close - candle_open) / current_range if current_range else 0.0
    result["entry_upper_wick_ratio"] = (
        (candle_high - max(candle_open, current_close)) / current_range if current_range else 0.0
    )
    result["entry_lower_wick_ratio"] = (
        (min(candle_open, current_close) - candle_low) / current_range if current_range else 0.0
    )
    local_high = high.tail(20).max() if len(high) >= 20 else np.nan
    local_low = low.tail(20).min() if len(low) >= 20 else np.nan
    result["entry_distance_local_high_20"] = (
        float(current_close / local_high - 1.0) if pd.notna(local_high) and local_high else None
    )
    result["entry_distance_local_low_20"] = (
        float(current_close / local_low - 1.0) if pd.notna(local_low) and local_low else None
    )
    open_time = pd.Timestamp(trade["open_time_utc"])
    result["entry_hour_utc"] = int(open_time.hour)
    result["entry_day_of_week"] = open_time.day_name()
    return result


def leakage_violation_count(frame: pd.DataFrame) -> int:
    if "entry_feature_timestamp_utc" not in frame.columns:
        return 0
    return int(
        frame["entry_feature_timestamp_utc"].gt(frame["open_time_utc"]).fillna(False).sum()
    )


def _with_availability(frame: pd.DataFrame, seconds: int) -> pd.DataFrame:
    output = frame.sort_values("ts").reset_index(drop=True).copy()
    output["availability_ts"] = output["ts"] + pd.Timedelta(seconds=seconds)
    return output


def _normalized_slope(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 2 or float(numeric.mean()) == 0:
        return None
    x: np.ndarray = np.arange(len(numeric), dtype=float)
    slope = float(np.polyfit(x, numeric.to_numpy(dtype=float), 1)[0])
    return slope / float(numeric.mean())


def _volatility_regime(current: float | None, median: float | None) -> str:
    if current is None or median is None or median <= 0:
        return "unknown"
    if current >= median * 1.25:
        return "high"
    if current <= median * 0.75:
        return "low"
    return "normal"
