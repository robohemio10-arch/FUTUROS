"""Descriptive exit-path research with conservative intrabar handling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from .contracts import TIMEFRAME_SECONDS, finite_float, normalize_symbol


BREAK_EVEN_THRESHOLDS = (0.003, 0.005, 0.006, 0.008)
TRAILING_THRESHOLDS = (0.003, 0.005)
TIME_STOP_MINUTES = (15, 30, 60, 180)


def analyze_exit_efficiency(
    closed: pd.DataFrame,
    candles_by_timeframe: Mapping[str, pd.DataFrame],
) -> dict[str, Any]:
    """Analyze closed trades without selecting or authorizing an exit policy."""

    prepared = {
        timeframe: {
            str(symbol): group.reset_index(drop=True)
            for symbol, group in _prepare_candles(frame).groupby("symbol", sort=False)
        }
        for timeframe, frame in candles_by_timeframe.items()
        if not frame.empty
    }
    trade_results: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    for trade in closed.itertuples(index=False):
        result, policies = _analyze_trade(trade, prepared)
        trade_results.append(result)
        policy_rows.extend(policies)

    sufficient_statuses = {"SUFFICIENT_15S", "SUFFICIENT_1M"}
    covered = sum(row["path_coverage_status"] in sufficient_statuses for row in trade_results)
    partial = sum(row["path_coverage_status"] == "PARTIAL" for row in trade_results)
    missing = sum(row["path_coverage_status"] == "MISSING" for row in trade_results)
    policy_eligible = sum(bool(row["policy_eligible"]) for row in trade_results)
    metrics = _aggregate_exit_metrics(trade_results)
    return {
        "status": "ok" if len(closed) else "SOURCE_MISSING",
        "trade_count": int(len(closed)),
        "path_covered_trade_count": int(covered),
        "full_or_sufficient_path_trade_count": int(covered),
        "partial_path_trade_count": int(partial),
        "missing_path_trade_count": int(missing),
        "policy_eligible_trade_count": int(policy_eligible),
        "path_coverage_rate": float(covered / len(closed)) if len(closed) else 0.0,
        "exit_efficiency_path_coverage_sufficient": bool(
            len(closed) and covered / len(closed) >= 0.8
        ),
        "metrics": metrics,
        "trades": trade_results,
        "policy_comparison": _summarize_policies(policy_rows),
        "policy_search_performed": False,
        "policy_promotion_performed": False,
    }


def _prepare_candles(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], utc=True, errors="coerce")
    prepared["symbol"] = prepared["symbol"].map(normalize_symbol)
    for column in ("open", "high", "low", "close"):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    return (
        prepared.dropna(subset=["timestamp", "symbol", "open", "high", "low", "close"])
        .sort_values(["symbol", "timestamp"], kind="mergesort")
        .reset_index(drop=True)
    )


def _analyze_trade(
    trade: Any,
    candles: Mapping[str, Mapping[str, pd.DataFrame]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trade_id = int(trade.id)
    symbol = normalize_symbol(trade.pair)
    side = "SHORT" if int(trade.is_short) == 1 else "LONG"
    opened = pd.Timestamp(trade.open_date)
    closed_at = pd.Timestamp(trade.close_date)
    open_rate = finite_float(trade.open_rate)
    close_rate = finite_float(trade.close_rate)
    paths = {
        timeframe: _slice_time_window(
            by_symbol.get(symbol),
            opened,
            closed_at,
            TIMEFRAME_SECONDS[timeframe],
        )
        for timeframe, by_symbol in candles.items()
        if timeframe in TIMEFRAME_SECONDS
    }
    bars_15s = len(paths.get("15s", ()))
    bars_1m = len(paths.get("1m", ()))
    coverage_status = _coverage_status(paths, opened, closed_at)
    path, used_timeframe = _select_path(paths, coverage_status)
    policy_eligible = bool(
        coverage_status in {"SUFFICIENT_15S", "SUFFICIENT_1M"}
        and open_rate is not None
        and open_rate > 0
    )
    base = {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "bars_15s": int(bars_15s),
        "bars_1m": int(bars_1m),
        "path_coverage_status": coverage_status,
        "path_timeframe_used": used_timeframe,
        "policy_eligible": policy_eligible,
        "mfe": None,
        "mae": None,
        "time_to_MFE_seconds": None,
        "time_to_MAE_seconds": None,
        "time_to_MFE": None,
        "time_to_MAE": None,
        "realized_exit_price_return": None,
        "price_give_back": None,
        "max_give_back_before_exit": None,
        "time_in_profit_seconds": None,
        "time_underwater_seconds": None,
        "time_in_profit": None,
        "time_underwater": None,
    }
    if not policy_eligible or path.empty or open_rate is None or open_rate <= 0:
        return base, []

    if side == "LONG":
        favorable = path["high"] / open_rate - 1.0
        adverse = path["low"] / open_rate - 1.0
        close_moves = path["close"] / open_rate - 1.0
        realized = close_rate / open_rate - 1.0 if close_rate and close_rate > 0 else None
    else:
        favorable = 1.0 - path["low"] / open_rate
        adverse = 1.0 - path["high"] / open_rate
        close_moves = 1.0 - path["close"] / open_rate
        realized = 1.0 - close_rate / open_rate if close_rate and close_rate > 0 else None
    mfe_index = favorable.idxmax()
    mae_index = adverse.idxmin()
    duration_per_bar = 15 if used_timeframe == "15s" else 60
    running_favorable = favorable.cummax()
    give_back_path = running_favorable - close_moves
    base.update(
        {
            "mfe": float(favorable.max()),
            "mae": float(adverse.min()),
            "time_to_MFE_seconds": float(
                (path.loc[mfe_index, "timestamp"] - opened).total_seconds()
            ),
            "time_to_MAE_seconds": float(
                (path.loc[mae_index, "timestamp"] - opened).total_seconds()
            ),
            "realized_exit_price_return": float(realized) if realized is not None else None,
            "price_give_back": (
                float(favorable.max() - realized) if realized is not None else None
            ),
            "max_give_back_before_exit": float(give_back_path.max()),
            "time_in_profit_seconds": int(close_moves.gt(0).sum()) * duration_per_bar,
            "time_underwater_seconds": int(close_moves.lt(0).sum()) * duration_per_bar,
        }
    )
    base["time_to_MFE"] = base["time_to_MFE_seconds"]
    base["time_to_MAE"] = base["time_to_MAE_seconds"]
    base["time_in_profit"] = base["time_in_profit_seconds"]
    base["time_underwater"] = base["time_underwater_seconds"]
    policies = _simulate_policies(
        trade_id=trade_id,
        side=side,
        open_rate=open_rate,
        path=path,
        opened=opened,
        baseline_pnl=finite_float(trade.close_profit_abs),
    )
    return base, policies


def _slice_time_window(
    frame: pd.DataFrame | None,
    opened: pd.Timestamp,
    closed_at: pd.Timestamp,
    timeframe_seconds: int,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    timestamps = frame["timestamp"]
    candle_ends = timestamps.add(pd.Timedelta(seconds=timeframe_seconds))
    return frame.loc[timestamps.ge(opened) & candle_ends.le(closed_at)].reset_index(
        drop=True
    )


def _select_path(
    paths: Mapping[str, pd.DataFrame],
    status: str,
) -> tuple[pd.DataFrame, str | None]:
    if status == "SUFFICIENT_15S":
        return paths["15s"], "15s"
    if status == "SUFFICIENT_1M":
        return paths["1m"], "1m"
    for timeframe in ("15s", "1m"):
        if timeframe in paths and not paths[timeframe].empty:
            return paths[timeframe], timeframe
    return pd.DataFrame(), None


def _coverage_status(
    paths: Mapping[str, pd.DataFrame],
    opened: pd.Timestamp,
    closed_at: pd.Timestamp,
) -> str:
    duration = max(0.0, (closed_at - opened).total_seconds())
    for timeframe, seconds, label in (
        ("15s", 15, "SUFFICIENT_15S"),
        ("1m", 60, "SUFFICIENT_1M"),
    ):
        path = paths.get(timeframe)
        if path is None or path.empty:
            continue
        expected = max(1, int(duration // seconds))
        if len(path) >= expected * 0.8:
            return label
    if any(
        timeframe in paths and not paths[timeframe].empty
        for timeframe in ("15s", "1m")
    ):
        return "PARTIAL"
    return "MISSING"


def _simulate_policies(
    *,
    trade_id: int,
    side: str,
    open_rate: float,
    path: pd.DataFrame,
    opened: pd.Timestamp,
    baseline_pnl: float | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for threshold in BREAK_EVEN_THRESHOLDS:
        rows.append(
            _simulate_break_even(trade_id, side, open_rate, path, threshold, baseline_pnl)
        )
    for threshold in TRAILING_THRESHOLDS:
        rows.append(_simulate_trailing(trade_id, side, open_rate, path, threshold, baseline_pnl))
    for minutes in TIME_STOP_MINUTES:
        rows.append(_simulate_time_stop(trade_id, path, opened, minutes, baseline_pnl))
    return rows


def _simulate_break_even(
    trade_id: int,
    side: str,
    open_rate: float,
    path: pd.DataFrame,
    threshold: float,
    baseline_pnl: float | None,
) -> dict[str, Any]:
    activated = False
    for row in path.itertuples(index=False):
        favorable_hit = row.high >= open_rate * (1 + threshold) if side == "LONG" else row.low <= open_rate * (1 - threshold)
        stop_hit = row.low <= open_rate if side == "LONG" else row.high >= open_rate
        if favorable_hit and stop_hit and not activated:
            return _policy_row(trade_id, f"break_even_{threshold:.4f}", "AMBIGUOUS", True, None, baseline_pnl)
        if favorable_hit:
            activated = True
        if activated and stop_hit:
            return _policy_row(trade_id, f"break_even_{threshold:.4f}", "TRIGGERED", False, open_rate, baseline_pnl)
    return _policy_row(trade_id, f"break_even_{threshold:.4f}", "NOT_TRIGGERED", False, None, baseline_pnl)


def _simulate_trailing(
    trade_id: int,
    side: str,
    open_rate: float,
    path: pd.DataFrame,
    threshold: float,
    baseline_pnl: float | None,
) -> dict[str, Any]:
    best = open_rate
    for row in path.itertuples(index=False):
        prior_best = best
        best = max(best, float(row.high)) if side == "LONG" else min(best, float(row.low))
        stop = best * (1 - threshold) if side == "LONG" else best * (1 + threshold)
        hit = row.low <= stop if side == "LONG" else row.high >= stop
        updated = best != prior_best
        if updated and hit:
            return _policy_row(trade_id, f"trailing_{threshold:.4f}", "AMBIGUOUS", True, None, baseline_pnl)
        if hit and best != open_rate:
            return _policy_row(trade_id, f"trailing_{threshold:.4f}", "TRIGGERED", False, stop, baseline_pnl)
    return _policy_row(trade_id, f"trailing_{threshold:.4f}", "NOT_TRIGGERED", False, None, baseline_pnl)


def _simulate_time_stop(
    trade_id: int,
    path: pd.DataFrame,
    opened: pd.Timestamp,
    minutes: int,
    baseline_pnl: float | None,
) -> dict[str, Any]:
    eligible = path.loc[path["timestamp"].ge(opened + pd.Timedelta(minutes=minutes))]
    if eligible.empty:
        return _policy_row(trade_id, f"time_stop_{minutes}m", "NOT_TRIGGERED", False, None, baseline_pnl)
    return _policy_row(
        trade_id,
        f"time_stop_{minutes}m",
        "TRIGGERED",
        False,
        float(eligible.iloc[0]["close"]),
        baseline_pnl,
    )


def _policy_row(
    trade_id: int,
    policy: str,
    status: str,
    ambiguous: bool,
    exit_price: float | None,
    baseline_pnl: float | None,
) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "policy": policy,
        "status": status,
        "ambiguous_intrabar_path": ambiguous,
        "simulated_exit_price": exit_price,
        "baseline_net_pnl": baseline_pnl,
        "net_pnl_delta_estimate": None,
        "net_pnl_delta_status": "INCOMPLETE_COST_MODEL",
    }


def _summarize_policies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    output: list[dict[str, Any]] = []
    for policy, group in frame.groupby("policy", sort=True):
        output.append(
            {
                "baseline": "reported_close_profit_abs",
                "policy": str(policy),
                "trade_count": int(len(group)),
                "triggered_count": int(group["status"].eq("TRIGGERED").sum()),
                "ambiguous_intrabar_count": int(group["ambiguous_intrabar_path"].sum()),
                "net_pnl_delta_estimate": None,
                "winner_to_loser_count": None,
                "loser_to_breakeven_count": None,
                "missed_upside_count": None,
                "max_drawdown_proxy": None,
                "cost_model_status": "INCOMPLETE",
            }
        )
    return output


def _aggregate_exit_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "analyzed_trade_count": 0,
            "mfe_mean": None,
            "mae_mean": None,
            "price_give_back_mean": None,
            "max_give_back_before_exit_mean": None,
        }
    frame = pd.DataFrame(rows)
    valid = frame.loc[
        frame["path_coverage_status"].isin({"SUFFICIENT_15S", "SUFFICIENT_1M"})
        & frame["mfe"].notna()
        & frame["mae"].notna()
    ]
    return {
        "analyzed_trade_count": int(len(valid)),
        "mfe_mean": float(valid["mfe"].mean()) if not valid.empty else None,
        "mae_mean": float(valid["mae"].mean()) if not valid.empty else None,
        "price_give_back_mean": (
            float(valid["price_give_back"].dropna().mean())
            if valid["price_give_back"].notna().any()
            else None
        ),
        "max_give_back_before_exit_mean": (
            float(valid["max_give_back_before_exit"].dropna().mean())
            if valid["max_give_back_before_exit"].notna().any()
            else None
        ),
    }
