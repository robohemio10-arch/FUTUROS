"""Closed-trade financial label derivation for target-store evidence."""

from __future__ import annotations

from typing import Any

import pandas as pd

TARGET_COLUMNS = [
    "target_label_sign",
    "target_win_loss",
    "target_net_pnl",
    "target_profit_ratio",
    "target_net_return_after_costs",
    "target_roi_hit",
    "target_stoploss_hit",
    "target_time_exit",
    "target_holding_seconds",
    "target_holding_bucket",
    "target_net_pnl_bucket",
    "target_quality_score",
    "target_expected_value_component",
    "target_risk_penalty_component",
    "target_cost_component",
    "target_triple_barrier_label",
    "target_triple_barrier_reason",
    "target_barrier_upper_pct",
    "target_barrier_lower_pct",
    "target_barrier_vertical_seconds",
]

IDENTIFIER_COLUMNS = [
    "event_id",
    "order_id",
    "internal_order_id",
    "trade_id",
    "row_fingerprint",
    "symbol",
    "symbol_norm",
    "side",
    "position_side",
    "open_time_utc",
    "close_time_utc",
]


def validate_target_source(frame: pd.DataFrame) -> list[str]:
    """Validate that closed-trade outcomes can produce target labels."""

    errors: list[str] = []
    if frame.empty:
        errors.append("selected_dataset_empty")
    if "net_pnl" not in frame.columns:
        errors.append("missing_net_pnl")
    if "profit_ratio" not in frame.columns:
        errors.append("missing_profit_ratio")
    if "label_sign" not in frame.columns and "label_win_loss" not in frame.columns:
        errors.append("missing_labels")
    if "close_time_utc" not in frame.columns and "is_closed" not in frame.columns:
        errors.append("missing_closed_trade_marker")
    return sorted(set(errors))


def closed_trade_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Return rows that are auditable as closed trades."""

    if frame.empty:
        return frame.copy()
    mask = pd.Series(True, index=frame.index)
    if "is_closed" in frame.columns:
        is_closed = frame["is_closed"].map(_truthy_closed)
        mask &= is_closed.fillna(False)
    if "close_time_utc" in frame.columns:
        close_time = pd.to_datetime(frame["close_time_utc"], utc=True, errors="coerce")
        mask &= close_time.notna()
    return frame.loc[mask].copy()


def build_target_frame(
    frame: pd.DataFrame,
    *,
    upper_barrier_pct: float,
    lower_barrier_pct: float,
    vertical_barrier_seconds: int,
) -> pd.DataFrame:
    """Build deterministic target columns from closed-trade outcomes."""

    closed = closed_trade_rows(frame)
    target = pd.DataFrame(index=closed.index)
    target["target_label_sign"] = derive_label_sign(closed)
    target["target_win_loss"] = target["target_label_sign"].map(_win_loss_from_sign)
    target["target_net_pnl"] = numeric_column(closed, "net_pnl")
    target["target_profit_ratio"] = numeric_column(closed, "profit_ratio")
    cost_components = build_cost_components(closed)
    target["target_net_return_after_costs"] = target["target_profit_ratio"] - cost_components["cost_ratio_proxy"]
    target["target_roi_hit"] = derive_roi_hit(closed, target["target_label_sign"])
    target["target_stoploss_hit"] = derive_stoploss_hit(closed, target["target_label_sign"])
    target["target_time_exit"] = ~(target["target_roi_hit"] | target["target_stoploss_hit"])
    target["target_holding_seconds"] = derive_holding_seconds(closed)
    target["target_holding_bucket"] = target["target_holding_seconds"].map(holding_bucket)
    target["target_net_pnl_bucket"] = target["target_net_pnl"].map(net_pnl_bucket)
    target["target_quality_score"] = target.apply(quality_score, axis=1)
    target["target_cost_component"] = cost_components["cost_total"]
    target["target_risk_penalty_component"] = target["target_net_pnl"].map(lambda value: max(0.0, -float(value)))
    target["target_expected_value_component"] = (
        target["target_net_pnl"] - target["target_cost_component"] - target["target_risk_penalty_component"]
    )
    target["target_triple_barrier_label"] = target.apply(triple_barrier_label, axis=1)
    target["target_triple_barrier_reason"] = target.apply(triple_barrier_reason, axis=1)
    target["target_barrier_upper_pct"] = float(upper_barrier_pct)
    target["target_barrier_lower_pct"] = float(lower_barrier_pct)
    target["target_barrier_vertical_seconds"] = int(vertical_barrier_seconds)

    for column in IDENTIFIER_COLUMNS:
        if column in closed.columns:
            target[column] = closed[column]
    ordered = [column for column in IDENTIFIER_COLUMNS if column in target.columns] + TARGET_COLUMNS
    return target[ordered].reset_index(drop=True)


def build_cost_components(frame: pd.DataFrame) -> dict[str, pd.Series]:
    trading_fee = numeric_column(frame, "trading_fee") if "trading_fee" in frame.columns else pd.Series(0.0, index=frame.index)
    funding_fee = numeric_column(frame, "funding_fee") if "funding_fee" in frame.columns else pd.Series(0.0, index=frame.index)
    slippage = numeric_column(frame, "slippage_estimate") if "slippage_estimate" in frame.columns else pd.Series(0.0, index=frame.index)
    spread = numeric_column(frame, "spread_estimate") if "spread_estimate" in frame.columns else pd.Series(0.0, index=frame.index)
    cost_total = trading_fee.abs() + funding_fee.abs() + slippage.abs() + spread.abs()
    notional = numeric_column(frame, "notional").abs() if "notional" in frame.columns else pd.Series(0.0, index=frame.index)
    cost_ratio_proxy = cost_total.where(notional <= 0, cost_total / notional).fillna(0.0)
    return {
        "trading_fee": trading_fee,
        "funding_fee": funding_fee,
        "slippage_estimate": slippage,
        "spread_estimate": spread,
        "cost_total": cost_total,
        "cost_ratio_proxy": cost_ratio_proxy,
    }


def numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0).astype(float)


def derive_label_sign(frame: pd.DataFrame) -> pd.Series:
    if "label_sign" in frame.columns:
        parsed = frame["label_sign"].map(parse_label_sign)
        if parsed.notna().any():
            return parsed.fillna(0).astype(int)
    if "label_win_loss" in frame.columns:
        return frame["label_win_loss"].map(parse_win_loss).fillna(0).astype(int)
    return numeric_column(frame, "net_pnl").map(lambda value: 1 if value > 0 else (-1 if value < 0 else 0)).astype(int)


def derive_roi_hit(frame: pd.DataFrame, label_sign: pd.Series) -> pd.Series:
    if "roi_hit" in frame.columns:
        return frame["roi_hit"].map(boolish).fillna(False)
    exit_reason = frame["exit_reason"].astype("string").str.lower() if "exit_reason" in frame.columns else pd.Series("", index=frame.index)
    return exit_reason.str.contains("roi|take|tp", regex=True, na=False) | (label_sign > 0)


def derive_stoploss_hit(frame: pd.DataFrame, label_sign: pd.Series) -> pd.Series:
    if "stoploss_hit" in frame.columns:
        return frame["stoploss_hit"].map(boolish).fillna(False)
    exit_reason = frame["exit_reason"].astype("string").str.lower() if "exit_reason" in frame.columns else pd.Series("", index=frame.index)
    return exit_reason.str.contains("stop|loss|sl", regex=True, na=False) | (label_sign < 0)


def derive_holding_seconds(frame: pd.DataFrame) -> pd.Series:
    if "duration_seconds" in frame.columns:
        return pd.to_numeric(frame["duration_seconds"], errors="coerce").fillna(0).clip(lower=0).astype(int)
    if "open_time_utc" not in frame.columns or "close_time_utc" not in frame.columns:
        return pd.Series(0, index=frame.index, dtype="int64")
    opened = pd.to_datetime(frame["open_time_utc"], utc=True, errors="coerce")
    closed = pd.to_datetime(frame["close_time_utc"], utc=True, errors="coerce")
    return ((closed - opened).dt.total_seconds().fillna(0).clip(lower=0)).astype(int)


def parse_label_sign(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().lower()
    if text in {"1", "1.0", "win", "winner", "positive", "profit"}:
        return 1
    if text in {"-1", "-1.0", "loss", "loser", "negative"}:
        return -1
    if text in {"0", "0.0", "flat", "breakeven", "break_even", "neutral"}:
        return 0
    try:
        numeric = float(text)
    except ValueError:
        return None
    return 1 if numeric > 0 else (-1 if numeric < 0 else 0)


def parse_win_loss(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().lower()
    if text in {"win", "winner", "1", "true", "profit", "positive"}:
        return 1
    if text in {"loss", "loser", "-1", "false", "negative"}:
        return -1
    if text in {"flat", "breakeven", "break_even", "0", "neutral"}:
        return 0
    return None


def boolish(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "1.0", "true", "yes", "y"}:
        return True
    if text in {"0", "0.0", "false", "no", "n"}:
        return False
    return None


def holding_bucket(seconds: Any) -> str:
    value = int(seconds)
    if value < 300:
        return "lt_5m"
    if value < 1800:
        return "5m_30m"
    if value < 3600:
        return "30m_1h"
    if value < 14400:
        return "1h_4h"
    if value < 86400:
        return "4h_24h"
    return "gte_24h"


def net_pnl_bucket(value: Any) -> str:
    numeric = float(value)
    if numeric > 0:
        return "positive"
    if numeric < 0:
        return "negative"
    return "breakeven"


def quality_score(row: pd.Series) -> float:
    if pd.isna(row["target_net_pnl"]) or pd.isna(row["target_profit_ratio"]):
        return 0.0
    if int(row["target_holding_seconds"]) < 0:
        return 0.0
    return 1.0


def triple_barrier_label(row: pd.Series) -> int:
    if bool(row["target_roi_hit"]):
        return 1
    if bool(row["target_stoploss_hit"]):
        return -1
    return 0


def triple_barrier_reason(row: pd.Series) -> str:
    if bool(row["target_roi_hit"]):
        return "upper_barrier_closed_trade_proxy"
    if bool(row["target_stoploss_hit"]):
        return "lower_barrier_closed_trade_proxy"
    return "vertical_barrier_closed_trade_proxy"


def _truthy_closed(value: Any) -> bool:
    parsed = boolish(value)
    return bool(parsed)


def _win_loss_from_sign(value: Any) -> str:
    numeric = int(value)
    if numeric > 0:
        return "win"
    if numeric < 0:
        return "loss"
    return "breakeven"
