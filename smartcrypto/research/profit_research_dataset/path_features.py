"""Post-entry path and outcome diagnostics."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd


PATH_REQUIRED_COLUMNS = (
    "mfe_absolute",
    "mfe_pct",
    "mae_absolute",
    "mae_pct",
    "time_to_mfe_seconds",
    "time_to_mae_seconds",
    "retracement_after_mfe_absolute",
    "retracement_pct_of_mfe",
)


def attach_path_features(
    trades: pd.DataFrame,
    paths_by_trade: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    output = trades.copy()
    for column in PATH_REQUIRED_COLUMNS:
        output[column] = pd.NA
    for column in (
        "mfe_price",
        "mae_price",
        "mfe_retracement_velocity",
        "positive_candle_count",
        "negative_candle_count",
        "initial_adverse_move_pct",
        "initial_favorable_move_pct",
        "intratrade_close_relative_range",
        "winner_to_loser_conversion",
        "fee_burden",
        "gross_result_supported",
        "path_feature_complete",
    ):
        output[column] = pd.NA
    output["duration_bucket"] = pd.Series(pd.NA, index=output.index, dtype="string")
    output["path_feature_missing_reason"] = pd.Series(pd.NA, index=output.index, dtype="string")
    for index, trade in output.iterrows():
        stable_id = str(trade.get("stable_trade_id"))
        path = paths_by_trade.get(stable_id)
        output.at[index, "duration_bucket"] = duration_bucket(trade.get("duration_seconds"))
        if path is None or path.empty:
            output.at[index, "path_feature_complete"] = False
            output.at[index, "path_feature_missing_reason"] = "intratrade_path_missing"
            continue
        values = compute_path_feature_row(trade, path)
        for column, value in values.items():
            output.at[index, column] = value
        complete = all(pd.notna(values.get(column)) for column in PATH_REQUIRED_COLUMNS)
        output.at[index, "path_feature_complete"] = complete
        if not complete:
            output.at[index, "path_feature_missing_reason"] = "path_metric_incomplete"
    return output


def compute_path_feature_row(trade: pd.Series, path: pd.DataFrame) -> dict[str, Any]:
    entry = float(trade["entry_price"])
    quantity = float(trade["quantity"])
    contract_size = float(trade["contract_size"])
    multiplier = quantity * contract_size
    side = str(trade["side"])
    if side == "long":
        mfe_index = path["high"].idxmax()
        mae_index = path["low"].idxmin()
        mfe_price = float(path.loc[mfe_index, "high"])
        mae_price = float(path.loc[mae_index, "low"])
        favorable_delta = mfe_price - entry
        adverse_delta = mae_price - entry
        after_mfe = path.loc[path.index >= mfe_index]
        retracement_price = float(after_mfe["low"].min())
        retracement = max(0.0, mfe_price - retracement_price)
        initial_favorable = (float(path.iloc[0]["high"]) - entry) / entry
        initial_adverse = (float(path.iloc[0]["low"]) - entry) / entry
    else:
        mfe_index = path["low"].idxmin()
        mae_index = path["high"].idxmax()
        mfe_price = float(path.loc[mfe_index, "low"])
        mae_price = float(path.loc[mae_index, "high"])
        favorable_delta = entry - mfe_price
        adverse_delta = entry - mae_price
        after_mfe = path.loc[path.index >= mfe_index]
        retracement_price = float(after_mfe["high"].max())
        retracement = max(0.0, retracement_price - mfe_price)
        initial_favorable = (entry - float(path.iloc[0]["low"])) / entry
        initial_adverse = (entry - float(path.iloc[0]["high"])) / entry
    open_time = pd.Timestamp(trade["open_time_utc"])
    mfe_time = pd.Timestamp(path.loc[mfe_index, "ts"])
    mae_time = pd.Timestamp(path.loc[mae_index, "ts"])
    range_low = float(path["low"].min())
    range_high = float(path["high"].max())
    final_close = float(path.iloc[-1]["close"])
    relative_close = (
        (final_close - range_low) / (range_high - range_low)
        if range_high > range_low
        else 0.5
    )
    mfe_absolute = favorable_delta * multiplier
    retracement_absolute = retracement * multiplier
    elapsed_after_mfe = max(0.0, (pd.Timestamp(path.iloc[-1]["ts"]) - mfe_time).total_seconds())
    gross = trade.get("gross_pnl")
    fees = trade.get("fees")
    gross_supported = (
        str(trade.get("financial_decomposition_status")) == "authoritative_reconciled"
        and pd.notna(gross)
    )
    return {
        "mfe_absolute": mfe_absolute,
        "mfe_pct": favorable_delta / entry,
        "mae_absolute": adverse_delta * multiplier,
        "mae_pct": adverse_delta / entry,
        "mfe_price": mfe_price,
        "mae_price": mae_price,
        "time_to_mfe_seconds": float((mfe_time - open_time).total_seconds()),
        "time_to_mae_seconds": float((mae_time - open_time).total_seconds()),
        "retracement_after_mfe_absolute": retracement_absolute,
        "retracement_pct_of_mfe": retracement_absolute / mfe_absolute if mfe_absolute > 0 else None,
        "mfe_retracement_velocity": (
            retracement_absolute / elapsed_after_mfe if elapsed_after_mfe > 0 else None
        ),
        "positive_candle_count": int(path["close"].gt(path["open"]).sum()),
        "negative_candle_count": int(path["close"].lt(path["open"]).sum()),
        "initial_adverse_move_pct": initial_adverse,
        "initial_favorable_move_pct": initial_favorable,
        "intratrade_close_relative_range": relative_close,
        "winner_to_loser_conversion": bool(mfe_absolute > 0 and float(trade["net_pnl"]) < 0),
        "fee_burden": (
            float(fees) / abs(float(gross))
            if gross_supported and pd.notna(fees) and float(gross) != 0
            else None
        ),
        "gross_result_supported": bool(gross_supported),
    }


def duration_bucket(value: Any) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    minutes = float(value) / 60.0
    if minutes < 15:
        return "lt_15m"
    if minutes < 30:
        return "15m_30m"
    if minutes < 60:
        return "30m_60m"
    if minutes < 180:
        return "1h_3h"
    if minutes < 360:
        return "3h_6h"
    return "gte_6h"
