"""Deterministic paper-only TP/SL research simulator for OCR V1.1 trades."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from smartcrypto.research.ocr_v11_dataset import (
    atomic_write_json,
    atomic_write_parquet,
    atomic_write_text,
    normalize_candles,
    read_table,
)
from smartcrypto.research.reporting import (
    build_tp_sl_executive_summary,
    render_tp_sl_executive_markdown,
)


DEFAULT_TP_BPS = (10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 75.0, 100.0, 150.0, 200.0)
DEFAULT_SL_BPS = DEFAULT_TP_BPS
DEFAULT_ATR_MULTIPLIERS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
DEFAULT_TRAILING_ATR_MULTIPLIERS = (0.25, 0.5, 1.0)
DEFAULT_FEE_BPS = 4.0
DEFAULT_SLIPPAGE_BPS = 2.0
DEFAULT_WORKERS = 10
DEFAULT_MAX_RAM_GB = 16.0

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_ocr": False,
    "imports_ocr": False,
    "promotes_quality_gated": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "runs_training": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
}

REQUIRED_RESEARCH_COLUMNS = {
    "trade_id",
    "symbol",
    "side",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "volume_closed",
    "net_pnl",
    "is_win",
    "is_research_eligible",
    "research_block_reason",
    "mfe_pct",
    "mae_pct",
    "max_favorable_price",
    "max_adverse_price",
    "time_to_mfe_seconds",
    "time_to_mae_seconds",
}

TRADE_OUTPUT_COLUMNS = (
    "trade_id",
    "symbol",
    "side",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "original_net_pnl",
    "original_is_win",
    "simulation_status",
    "simulation_block_reason",
    "candles_between_count",
    "strategy_id",
    "tp_mode",
    "sl_mode",
    "tp_value",
    "sl_value",
    "trailing_mode",
    "entry_atr",
    "tp_bps",
    "sl_bps",
    "tp_price",
    "sl_price",
    "tp_hit",
    "sl_hit",
    "tp_hit_time",
    "sl_hit_time",
    "first_hit",
    "conservative_same_candle_rule_applied",
    "simulated_exit_time",
    "simulated_exit_price",
    "simulated_gross_pnl",
    "simulated_fee",
    "simulated_slippage",
    "simulated_net_pnl",
    "simulated_is_win",
    "simulated_net_pnl_delta",
    "opposite_side_net_pnl",
    "opposite_side_is_win",
    "opposite_side_delta",
    "hold_to_original_exit_net_pnl",
    "best_possible_net_pnl",
    "worst_possible_net_pnl",
    "mfe_pct",
    "mae_pct",
    "max_favorable_price",
    "max_adverse_price",
    "time_to_mfe_seconds",
    "time_to_mae_seconds",
    "recomputed_mfe_pct",
    "recomputed_mae_pct",
    "mfe_mae_consistent",
)

GRID_OUTPUT_COLUMNS = (
    "strategy_id",
    "tp_mode",
    "sl_mode",
    "tp_value",
    "sl_value",
    "trailing_mode",
    "fee_bps",
    "slippage_bps",
    "evaluated_trades",
    "blocked_trades",
    "net_pnl",
    "gross_profit",
    "gross_loss",
    "profit_factor",
    "win_rate",
    "loss_rate",
    "avg_win",
    "avg_loss",
    "payoff_ratio",
    "expectancy",
    "median_trade_pnl",
    "max_drawdown",
    "max_consecutive_losses",
    "recovery_factor",
    "original_net_pnl",
    "net_pnl_delta_vs_original",
    "win_rate_delta_vs_original",
    "profit_factor_delta_vs_original",
    "ranking_score",
    "is_candidate_best",
)


@dataclass(frozen=True)
class SimulatorPaths:
    project_root: Path
    research_dataset_path: Path
    candles_path: Path | None
    output_grid_path: Path
    output_trade_path: Path
    report_path: Path
    executive_report_path: Path
    summary_path: Path


@dataclass(frozen=True)
class SimulatorConfig:
    tp_bps: tuple[float, ...] = DEFAULT_TP_BPS
    sl_bps: tuple[float, ...] = DEFAULT_SL_BPS
    atr_multipliers: tuple[float, ...] = DEFAULT_ATR_MULTIPLIERS
    trailing_atr_multipliers: tuple[float, ...] = DEFAULT_TRAILING_ATR_MULTIPLIERS
    fee_bps: float = DEFAULT_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    workers: int = DEFAULT_WORKERS
    max_ram_gb: float = DEFAULT_MAX_RAM_GB


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    tp_mode: Literal["fixed_bps", "atr", "none"]
    sl_mode: Literal["fixed_bps", "atr"]
    tp_value: float | None
    sl_value: float
    trailing_mode: Literal["none", "atr"] = "none"


@dataclass(frozen=True)
class TradeContext:
    row: dict[str, Any]
    path: pd.DataFrame
    entry_atr: float
    block_reason: str | None
    recomputed_mfe_pct: float | None = None
    recomputed_mae_pct: float | None = None
    mfe_mae_consistent: bool | None = None


@dataclass(frozen=True)
class SimulationResult:
    status: Literal["ok", "blocked"]
    reason: str
    tp_bps: float | None
    sl_bps: float | None
    tp_price: float | None
    sl_price: float | None
    tp_hit: bool
    sl_hit: bool
    tp_hit_time: pd.Timestamp | None
    sl_hit_time: pd.Timestamp | None
    first_hit: str
    same_candle_rule_applied: bool
    exit_time: pd.Timestamp | None
    exit_price: float | None
    gross_pnl: float | None
    fee: float | None
    slippage: float | None
    net_pnl: float | None


@dataclass(frozen=True)
class SimulatorBuildResult:
    grid: pd.DataFrame
    trades: pd.DataFrame
    report: dict[str, Any]


def _resolved(root: Path, value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_paths(
    project_root: str | Path,
    *,
    research_dataset_path: str | Path | None = None,
    candles_path: str | Path | None = None,
    output_grid_path: str | Path | None = None,
    output_trade_path: str | Path | None = None,
    report_path: str | Path | None = None,
    executive_report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> SimulatorPaths:
    root = Path(project_root).expanduser().resolve()
    research = _resolved(
        root,
        research_dataset_path,
        root / "data" / "research" / "ocr_v11_trade_research_dataset.parquet",
    )
    candle_candidates = (
        [_resolved(root, candles_path, root)]
        if candles_path is not None
        else [
            root / "data" / "features" / "market_features_60d.parquet",
            root / "data" / "raw" / "futures_ohlcv_60d.parquet",
        ]
    )
    candles = next((path.resolve() for path in candle_candidates if path.exists()), None)
    return SimulatorPaths(
        project_root=root,
        research_dataset_path=research,
        candles_path=candles,
        output_grid_path=_resolved(
            root,
            output_grid_path,
            root / "data" / "research" / "ocr_v11_tp_sl_grid_results.parquet",
        ),
        output_trade_path=_resolved(
            root,
            output_trade_path,
            root / "data" / "research" / "ocr_v11_trade_outcome_simulation.parquet",
        ),
        report_path=_resolved(
            root,
            report_path,
            root / "data" / "reports" / "ocr_v11_tp_sl_grid_summary.json",
        ),
        executive_report_path=_resolved(
            root,
            executive_report_path,
            root
            / "data"
            / "reports"
            / "training_reports"
            / "ocr_v11_tp_sl_executive.md",
        ),
        summary_path=_resolved(
            root,
            summary_path,
            root
            / "data"
            / "reports"
            / "training_reports"
            / "ocr_v11_tp_sl_summary.json",
        ),
    )


def validate_config(config: SimulatorConfig) -> list[str]:
    errors: list[str] = []
    for name, values in (
        ("tp_bps", config.tp_bps),
        ("sl_bps", config.sl_bps),
        ("atr_multipliers", config.atr_multipliers),
        ("trailing_atr_multipliers", config.trailing_atr_multipliers),
    ):
        if not values or any(not np.isfinite(value) or value <= 0 for value in values):
            errors.append(f"invalid_{name}")
    if not np.isfinite(config.fee_bps) or config.fee_bps < 0:
        errors.append("invalid_fee_bps")
    if not np.isfinite(config.slippage_bps) or config.slippage_bps < 0:
        errors.append("invalid_slippage_bps")
    if config.workers < 1:
        errors.append("invalid_workers")
    if not np.isfinite(config.max_ram_gb) or config.max_ram_gb < 1:
        errors.append("invalid_max_ram_gb")
    return errors


def build_strategy_grid(config: SimulatorConfig) -> list[StrategySpec]:
    strategies: list[StrategySpec] = []
    for tp in sorted(set(config.tp_bps)):
        for sl in sorted(set(config.sl_bps)):
            strategies.append(
                StrategySpec(
                    strategy_id=f"fixed_tp_{tp:g}_sl_{sl:g}",
                    tp_mode="fixed_bps",
                    sl_mode="fixed_bps",
                    tp_value=tp,
                    sl_value=sl,
                )
            )
    multipliers = sorted(set(config.atr_multipliers))
    for tp in multipliers:
        for sl in multipliers:
            strategies.append(
                StrategySpec(
                    strategy_id=f"atr_tp_{tp:g}_sl_{sl:g}",
                    tp_mode="atr",
                    sl_mode="atr",
                    tp_value=tp,
                    sl_value=sl,
                )
            )
    for trailing in sorted(set(config.trailing_atr_multipliers)):
        strategies.append(
            StrategySpec(
                strategy_id=f"trailing_atr_{trailing:g}",
                tp_mode="none",
                sl_mode="atr",
                tp_value=None,
                sl_value=trailing,
                trailing_mode="atr",
            )
        )
    return strategies


def prepare_candles(raw: pd.DataFrame) -> pd.DataFrame:
    candles, _invalid = normalize_candles(raw)
    groups: list[pd.DataFrame] = []
    for _symbol, group in candles.groupby("symbol", sort=True):
        ordered = group.sort_values("ts").copy()
        previous_close = ordered["close"].shift(1)
        true_range = pd.concat(
            [
                ordered["high"] - ordered["low"],
                (ordered["high"] - previous_close).abs(),
                (ordered["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        derived_atr = true_range.rolling(14, min_periods=14).mean()
        if "atr_14" in ordered.columns:
            source_atr = pd.to_numeric(ordered["atr_14"], errors="coerce")
            ordered["_simulation_atr"] = source_atr.where(source_atr.gt(0), derived_atr)
        else:
            ordered["_simulation_atr"] = derived_atr
        groups.append(ordered)
    if not groups:
        candles["_simulation_atr"] = pd.Series(dtype=float)
        return candles
    return pd.concat(groups, ignore_index=True)


def _complete_path(group: pd.DataFrame, open_time: pd.Timestamp, close_time: pd.Timestamp) -> pd.DataFrame:
    start = open_time.ceil("min")
    if open_time == open_time.floor("min"):
        start = open_time
    return group.loc[
        group["ts"].ge(start) & (group["ts"] + pd.Timedelta(minutes=1)).le(close_time)
    ].copy()


def _entry_atr(group: pd.DataFrame, open_time: pd.Timestamp) -> float:
    available = group.loc[(group["ts"] + pd.Timedelta(minutes=1)).le(open_time)]
    if available.empty:
        return np.nan
    value = available.iloc[-1]["_simulation_atr"]
    return float(value) if pd.notna(value) and np.isfinite(value) and value > 0 else np.nan


def _recompute_mfe_mae(
    group: pd.DataFrame,
    row: dict[str, Any],
) -> tuple[float | None, float | None, bool | None]:
    entry_price = float(row.get("entry_price", np.nan))
    side = str(row.get("side", ""))
    open_time = row["open_time"]
    close_time = row["close_time"]
    if not np.isfinite(entry_price) or entry_price <= 0 or side not in {"long", "short"}:
        return None, None, None
    validation_path = group.loc[
        group["ts"].ge(open_time.floor("min"))
        & group["ts"].le(close_time.floor("min"))
    ]
    if validation_path.empty:
        return None, None, None
    if side == "long":
        mfe_pct = (float(validation_path["high"].max()) - entry_price) / entry_price * 100.0
        mae_pct = (float(validation_path["low"].min()) - entry_price) / entry_price * 100.0
    else:
        mfe_pct = (entry_price - float(validation_path["low"].min())) / entry_price * 100.0
        mae_pct = (entry_price - float(validation_path["high"].max())) / entry_price * 100.0
    source_mfe = row.get("mfe_pct")
    source_mae = row.get("mae_pct")
    if pd.isna(source_mfe) or pd.isna(source_mae):
        return mfe_pct, mae_pct, None
    consistent = bool(
        np.isclose(mfe_pct, float(source_mfe), rtol=1e-7, atol=1e-9)
        and np.isclose(mae_pct, float(source_mae), rtol=1e-7, atol=1e-9)
    )
    return mfe_pct, mae_pct, consistent


def prepare_trade_contexts(research: pd.DataFrame, candles: pd.DataFrame) -> list[TradeContext]:
    groups = {
        symbol: group.sort_values("ts").reset_index(drop=True)
        for symbol, group in candles.groupby("symbol", sort=True)
    }
    contexts: list[TradeContext] = []
    for _, source in research.iterrows():
        row = source.to_dict()
        row["open_time"] = pd.to_datetime(row.get("open_time"), errors="coerce", utc=True)
        row["close_time"] = pd.to_datetime(row.get("close_time"), errors="coerce", utc=True)
        reason: str | None = None
        path = pd.DataFrame(columns=candles.columns)
        atr = np.nan
        recomputed_mfe: float | None = None
        recomputed_mae: float | None = None
        mfe_mae_consistent: bool | None = None
        if not bool(row.get("is_research_eligible", False)):
            reason = f"research_ineligible:{row.get('research_block_reason', 'unknown')}"
        elif pd.isna(row["open_time"]) or pd.isna(row["close_time"]):
            reason = "invalid_trade_times"
        else:
            group = groups.get(str(row.get("symbol", "")))
            if group is None:
                reason = "missing_symbol_candles"
            else:
                path = _complete_path(group, row["open_time"], row["close_time"])
                atr = _entry_atr(group, row["open_time"])
                recomputed_mfe, recomputed_mae, mfe_mae_consistent = _recompute_mfe_mae(
                    group,
                    row,
                )
                if path.empty:
                    reason = "no_complete_candles_during_trade"
        volume = pd.to_numeric(pd.Series([row.get("volume_closed")]), errors="coerce").iloc[0]
        if reason is None and (pd.isna(volume) or not np.isfinite(volume) or volume <= 0):
            reason = "invalid_volume_closed"
        contexts.append(
            TradeContext(
                row=row,
                path=path,
                entry_atr=atr,
                block_reason=reason,
                recomputed_mfe_pct=recomputed_mfe,
                recomputed_mae_pct=recomputed_mae,
                mfe_mae_consistent=mfe_mae_consistent,
            )
        )
    return contexts


def _levels(
    entry_price: float,
    side: str,
    strategy: StrategySpec,
    entry_atr: float,
) -> tuple[float | None, float, float | None, float]:
    direction = 1.0 if side == "long" else -1.0
    if strategy.tp_mode == "fixed_bps":
        tp_bps = float(strategy.tp_value or 0.0)
        tp_distance = entry_price * tp_bps / 10_000.0
    elif strategy.tp_mode == "atr":
        tp_distance = entry_atr * float(strategy.tp_value or 0.0)
        tp_bps = tp_distance / entry_price * 10_000.0
    else:
        tp_distance = 0.0
        tp_bps = None
    if strategy.sl_mode == "fixed_bps":
        sl_bps = strategy.sl_value
        sl_distance = entry_price * sl_bps / 10_000.0
    else:
        sl_distance = entry_atr * strategy.sl_value
        sl_bps = sl_distance / entry_price * 10_000.0
    tp_price = entry_price + direction * tp_distance if tp_bps is not None else None
    sl_price = entry_price - direction * sl_distance
    return tp_price, sl_price, tp_bps, sl_bps


def _costed_pnl(
    *,
    entry_price: float,
    exit_price: float,
    volume: float,
    side: str,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[float, float, float, float]:
    direction = 1.0 if side == "long" else -1.0
    gross = direction * (exit_price - entry_price) * volume
    turnover = (entry_price + exit_price) * volume
    fee = turnover * fee_bps / 10_000.0
    slippage = turnover * slippage_bps / 10_000.0
    return gross, fee, slippage, gross - fee - slippage


def _fixed_hits(
    path: pd.DataFrame,
    side: str,
    tp_price: float | None,
    sl_price: float,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    highs = path["high"].to_numpy(dtype=float, copy=False)
    lows = path["low"].to_numpy(dtype=float, copy=False)
    if side == "long":
        tp_positions = np.flatnonzero(highs >= tp_price) if tp_price is not None else np.array([])
        sl_positions = np.flatnonzero(lows <= sl_price)
    else:
        tp_positions = np.flatnonzero(lows <= tp_price) if tp_price is not None else np.array([])
        sl_positions = np.flatnonzero(highs >= sl_price)
    tp_time = (
        pd.Timestamp(path["ts"].iloc[int(tp_positions[0])]) if tp_positions.size else None
    )
    sl_time = (
        pd.Timestamp(path["ts"].iloc[int(sl_positions[0])]) if sl_positions.size else None
    )
    return tp_time, sl_time


def _trailing_hit(
    path: pd.DataFrame,
    side: str,
    initial_stop: float,
    distance: float,
) -> tuple[pd.Timestamp | None, float]:
    stop = initial_stop
    for _, candle in path.iterrows():
        if side == "long":
            stop = max(stop, float(candle["high"]) - distance)
            touched = float(candle["low"]) <= stop
        else:
            stop = min(stop, float(candle["low"]) + distance)
            touched = float(candle["high"]) >= stop
        if touched:
            return pd.Timestamp(candle["ts"]), stop
    return None, stop


def simulate_strategy(
    context: TradeContext,
    strategy: StrategySpec,
    config: SimulatorConfig,
    *,
    side_override: str | None = None,
) -> SimulationResult:
    row = context.row
    side = side_override or str(row.get("side", ""))
    entry_price = float(row.get("entry_price", np.nan))
    exit_price = float(row.get("exit_price", np.nan))
    volume = float(row.get("volume_closed", np.nan))
    close_time = row.get("close_time")
    requires_atr = strategy.tp_mode == "atr" or strategy.sl_mode == "atr"
    reason = context.block_reason
    if reason is None and requires_atr and not np.isfinite(context.entry_atr):
        reason = "missing_entry_atr"
    if reason is not None:
        return SimulationResult(
            status="blocked",
            reason=reason,
            tp_bps=None,
            sl_bps=None,
            tp_price=None,
            sl_price=None,
            tp_hit=False,
            sl_hit=False,
            tp_hit_time=None,
            sl_hit_time=None,
            first_hit="none",
            same_candle_rule_applied=False,
            exit_time=None,
            exit_price=None,
            gross_pnl=None,
            fee=None,
            slippage=None,
            net_pnl=None,
        )
    tp_price, initial_sl, tp_bps, sl_bps = _levels(
        entry_price,
        side,
        strategy,
        context.entry_atr,
    )
    tp_time: pd.Timestamp | None = None
    sl_time: pd.Timestamp | None = None
    final_sl = initial_sl
    if strategy.trailing_mode == "atr":
        distance = context.entry_atr * strategy.sl_value
        sl_time, final_sl = _trailing_hit(context.path, side, initial_sl, distance)
    else:
        tp_time, sl_time = _fixed_hits(context.path, side, tp_price, initial_sl)
    same_candle = tp_time is not None and sl_time is not None and tp_time == sl_time
    if sl_time is not None and (tp_time is None or sl_time <= tp_time):
        first_hit = "sl"
        simulated_exit_time = sl_time
        simulated_exit_price = final_sl if strategy.trailing_mode == "atr" else initial_sl
    elif tp_time is not None:
        first_hit = "tp"
        simulated_exit_time = tp_time
        simulated_exit_price = float(tp_price)
    else:
        first_hit = "hold"
        simulated_exit_time = pd.Timestamp(close_time)
        simulated_exit_price = exit_price
    gross, fee, slippage, net = _costed_pnl(
        entry_price=entry_price,
        exit_price=simulated_exit_price,
        volume=volume,
        side=side,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
    )
    return SimulationResult(
        status="ok",
        reason="simulated",
        tp_bps=tp_bps,
        sl_bps=sl_bps,
        tp_price=tp_price,
        sl_price=initial_sl,
        tp_hit=tp_time is not None,
        sl_hit=sl_time is not None,
        tp_hit_time=tp_time,
        sl_hit_time=sl_time,
        first_hit=first_hit,
        same_candle_rule_applied=same_candle,
        exit_time=simulated_exit_time,
        exit_price=simulated_exit_price,
        gross_pnl=gross,
        fee=fee,
        slippage=slippage,
        net_pnl=net,
    )


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    equity = np.concatenate(([0.0], np.cumsum(np.asarray(values, dtype=float))))
    peaks = np.maximum.accumulate(equity)
    return float(np.max(peaks - equity))


def max_consecutive_losses(values: list[float]) -> int:
    maximum = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        maximum = max(maximum, current)
    return maximum


def financial_metrics(values: list[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if np.isfinite(value)]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(sum(losses))
    profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else None
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff_ratio = avg_win / abs(avg_loss) if avg_loss < 0 else None
    drawdown = max_drawdown(clean)
    total = float(sum(clean))
    return {
        "trades": len(clean),
        "net_pnl": total,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "win_rate": len(wins) / len(clean) if clean else 0.0,
        "loss_rate": len(losses) / len(clean) if clean else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "expectancy": float(np.mean(clean)) if clean else 0.0,
        "median_trade_pnl": float(np.median(clean)) if clean else 0.0,
        "max_drawdown": drawdown,
        "max_consecutive_losses": max_consecutive_losses(clean),
        "recovery_factor": total / drawdown if drawdown > 0 else None,
    }


def _aggregate_strategy(
    strategy: StrategySpec,
    contexts: list[TradeContext],
    config: SimulatorConfig,
) -> dict[str, Any]:
    outcomes: list[tuple[pd.Timestamp, str, float, float]] = []
    blocked = 0
    for context in contexts:
        result = simulate_strategy(context, strategy, config)
        if result.status != "ok" or result.net_pnl is None:
            blocked += 1
            continue
        original = float(context.row.get("net_pnl", np.nan))
        outcomes.append(
            (
                pd.Timestamp(context.row["open_time"]),
                str(context.row.get("trade_id", "")),
                result.net_pnl,
                original,
            )
        )
    outcomes.sort(key=lambda item: (item[0], item[1]))
    simulated_values = [item[2] for item in outcomes]
    original_values = [item[3] for item in outcomes if np.isfinite(item[3])]
    simulated = financial_metrics(simulated_values)
    original = financial_metrics(original_values)
    profit_factor = simulated["profit_factor"]
    original_profit_factor = original["profit_factor"]
    return {
        "strategy_id": strategy.strategy_id,
        "tp_mode": strategy.tp_mode,
        "sl_mode": strategy.sl_mode,
        "tp_value": strategy.tp_value,
        "sl_value": strategy.sl_value,
        "trailing_mode": strategy.trailing_mode,
        "fee_bps": config.fee_bps,
        "slippage_bps": config.slippage_bps,
        "evaluated_trades": simulated["trades"],
        "blocked_trades": blocked,
        **{key: simulated[key] for key in (
            "net_pnl",
            "gross_profit",
            "gross_loss",
            "profit_factor",
            "win_rate",
            "loss_rate",
            "avg_win",
            "avg_loss",
            "payoff_ratio",
            "expectancy",
            "median_trade_pnl",
            "max_drawdown",
            "max_consecutive_losses",
            "recovery_factor",
        )},
        "original_net_pnl": original["net_pnl"],
        "net_pnl_delta_vs_original": float(simulated["net_pnl"]) - float(original["net_pnl"]),
        "win_rate_delta_vs_original": float(simulated["win_rate"]) - float(original["win_rate"]),
        "profit_factor_delta_vs_original": (
            float(profit_factor) - float(original_profit_factor)
            if profit_factor is not None and original_profit_factor is not None
            else None
        ),
    }


def rank_grid(grid: pd.DataFrame) -> pd.DataFrame:
    ranked = grid.copy()
    if ranked.empty:
        ranked["ranking_score"] = pd.Series(dtype=float)
        ranked["is_candidate_best"] = pd.Series(dtype=bool)
        return ranked
    count = len(ranked)
    net_rank = ranked["net_pnl"].rank(method="average", pct=True)
    profit_factors = pd.to_numeric(ranked["profit_factor"], errors="coerce").astype(float)
    pf_rank = profit_factors.fillna(0.0).rank(method="average", pct=True)
    expectancy_rank = ranked["expectancy"].rank(method="average", pct=True)
    drawdown_penalty = ranked["max_drawdown"].rank(method="average", pct=True)
    blocked_ratio = ranked["blocked_trades"] / (
        ranked["evaluated_trades"] + ranked["blocked_trades"]
    ).replace(0, np.nan)
    loss_instability = ranked["max_consecutive_losses"] / ranked["evaluated_trades"].replace(0, np.nan)
    instability = (blocked_ratio.fillna(1.0) + loss_instability.fillna(1.0)).rank(
        method="average", pct=True
    )
    ranked["ranking_score"] = (
        net_rank + pf_rank + expectancy_rank - drawdown_penalty - instability
    )
    ranked["is_candidate_best"] = False
    best_index = ranked.sort_values(
        ["ranking_score", "net_pnl", "max_drawdown", "strategy_id"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).index[0]
    ranked.loc[best_index, "is_candidate_best"] = True
    assert int(ranked["is_candidate_best"].sum()) == 1 and count > 0
    return ranked


def _optional_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def build_grid(
    contexts: list[TradeContext],
    config: SimulatorConfig,
) -> tuple[pd.DataFrame, StrategySpec | None]:
    strategies = build_strategy_grid(config)
    rows = [_aggregate_strategy(strategy, contexts, config) for strategy in strategies]
    grid = rank_grid(pd.DataFrame(rows))
    if grid.empty:
        return grid.reindex(columns=GRID_OUTPUT_COLUMNS), None
    best_id = str(grid.loc[grid["is_candidate_best"], "strategy_id"].iloc[0])
    best = next(strategy for strategy in strategies if strategy.strategy_id == best_id)
    return grid.reindex(columns=GRID_OUTPUT_COLUMNS), best


def _path_extreme_price(context: TradeContext, *, favorable: bool) -> float | None:
    if context.path.empty:
        return None
    side = str(context.row.get("side", ""))
    if side == "long":
        column, reducer = ("high", "max") if favorable else ("low", "min")
    elif side == "short":
        column, reducer = ("low", "min") if favorable else ("high", "max")
    else:
        return None
    series = context.path[column]
    return float(series.max() if reducer == "max" else series.min())


def _path_extreme_net(
    context: TradeContext,
    config: SimulatorConfig,
    *,
    favorable: bool,
) -> float | None:
    price = _path_extreme_price(context, favorable=favorable)
    if context.block_reason is not None or price is None:
        return None
    source = context.row
    return _costed_pnl(
        entry_price=float(source["entry_price"]),
        exit_price=price,
        volume=float(source["volume_closed"]),
        side=str(source["side"]),
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
    )[3]


def build_trade_outcomes(
    contexts: list[TradeContext],
    strategy: StrategySpec,
    config: SimulatorConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for context in contexts:
        source = context.row
        result = simulate_strategy(context, strategy, config)
        opposite_side = "short" if source.get("side") == "long" else "long"
        opposite = simulate_strategy(
            context,
            strategy,
            config,
            side_override=opposite_side,
        )
        hold_net: float | None = None
        if context.block_reason is None:
            _gross, _fee, _slippage, hold_net = _costed_pnl(
                entry_price=float(source["entry_price"]),
                exit_price=float(source["exit_price"]),
                volume=float(source["volume_closed"]),
                side=str(source["side"]),
                fee_bps=config.fee_bps,
                slippage_bps=config.slippage_bps,
            )
        original = float(source.get("net_pnl", np.nan))
        rows.append(
            {
                "trade_id": source.get("trade_id"),
                "symbol": source.get("symbol"),
                "side": source.get("side"),
                "open_time": source.get("open_time"),
                "close_time": source.get("close_time"),
                "entry_price": source.get("entry_price"),
                "exit_price": source.get("exit_price"),
                "original_net_pnl": original,
                "original_is_win": source.get("is_win"),
                "simulation_status": result.status,
                "simulation_block_reason": result.reason,
                "candles_between_count": int(len(context.path)),
                "strategy_id": strategy.strategy_id,
                "tp_mode": strategy.tp_mode,
                "sl_mode": strategy.sl_mode,
                "tp_value": strategy.tp_value,
                "sl_value": strategy.sl_value,
                "trailing_mode": strategy.trailing_mode,
                "entry_atr": context.entry_atr if np.isfinite(context.entry_atr) else None,
                "tp_bps": result.tp_bps,
                "sl_bps": result.sl_bps,
                "tp_price": result.tp_price,
                "sl_price": result.sl_price,
                "tp_hit": result.tp_hit,
                "sl_hit": result.sl_hit,
                "tp_hit_time": result.tp_hit_time,
                "sl_hit_time": result.sl_hit_time,
                "first_hit": result.first_hit,
                "conservative_same_candle_rule_applied": result.same_candle_rule_applied,
                "simulated_exit_time": result.exit_time,
                "simulated_exit_price": result.exit_price,
                "simulated_gross_pnl": result.gross_pnl,
                "simulated_fee": result.fee,
                "simulated_slippage": result.slippage,
                "simulated_net_pnl": result.net_pnl,
                "simulated_is_win": (
                    int(result.net_pnl > 0) if result.net_pnl is not None else pd.NA
                ),
                "simulated_net_pnl_delta": (
                    result.net_pnl - original
                    if result.net_pnl is not None and np.isfinite(original)
                    else None
                ),
                "opposite_side_net_pnl": opposite.net_pnl,
                "opposite_side_is_win": (
                    int(opposite.net_pnl > 0) if opposite.net_pnl is not None else pd.NA
                ),
                "opposite_side_delta": (
                    opposite.net_pnl - result.net_pnl
                    if opposite.net_pnl is not None and result.net_pnl is not None
                    else None
                ),
                "hold_to_original_exit_net_pnl": hold_net,
                "best_possible_net_pnl": _path_extreme_net(
                    context,
                    config,
                    favorable=True,
                ),
                "worst_possible_net_pnl": _path_extreme_net(
                    context,
                    config,
                    favorable=False,
                ),
                "mfe_pct": source.get("mfe_pct"),
                "mae_pct": source.get("mae_pct"),
                "max_favorable_price": source.get("max_favorable_price"),
                "max_adverse_price": source.get("max_adverse_price"),
                "time_to_mfe_seconds": source.get("time_to_mfe_seconds"),
                "time_to_mae_seconds": source.get("time_to_mae_seconds"),
                "recomputed_mfe_pct": context.recomputed_mfe_pct,
                "recomputed_mae_pct": context.recomputed_mae_pct,
                "mfe_mae_consistent": context.mfe_mae_consistent,
            }
        )
    return pd.DataFrame(rows).reindex(columns=TRADE_OUTPUT_COLUMNS)


def base_report(paths: SimulatorPaths, config: SimulatorConfig, write: bool) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "not_started",
        "research_dataset_path": str(paths.research_dataset_path),
        "research_dataset_rows": 0,
        "eligible_rows": 0,
        "blocked_rows": 0,
        "candles_path": str(paths.candles_path) if paths.candles_path else None,
        "candles_rows": 0,
        "grid_rows": 0,
        "trade_simulation_rows": 0,
        "mfe_mae_consistency_checked_rows": 0,
        "mfe_mae_mismatch_rows": 0,
        "best_strategy_id": None,
        "best_tp": None,
        "best_sl": None,
        "best_net_pnl": None,
        "best_profit_factor": None,
        "best_win_rate": None,
        "best_max_drawdown": None,
        "original_net_pnl": None,
        "net_pnl_delta_vs_original": None,
        "write_requested": write,
        "write_performed": False,
        "output_grid_path": str(paths.output_grid_path),
        "output_trade_path": str(paths.output_trade_path),
        "report_path": str(paths.report_path),
        "executive_report_path": str(paths.executive_report_path),
        "summary_path": str(paths.summary_path),
        "configured_workers": config.workers,
        "configured_max_ram_gb": config.max_ram_gb,
        "fee_bps": config.fee_bps,
        "slippage_bps": config.slippage_bps,
        "validation_errors": [],
        "warnings": [],
        **SAFETY_FLAGS,
    }


def run_simulation(
    paths: SimulatorPaths,
    config: SimulatorConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> SimulatorBuildResult:
    report = base_report(paths, config, write)
    config_errors = validate_config(config)
    if config_errors:
        report.update(reason="invalid_configuration", validation_errors=config_errors)
        return SimulatorBuildResult(pd.DataFrame(), pd.DataFrame(), report)
    if not paths.research_dataset_path.exists():
        report.update(reason="missing_research_dataset", validation_errors=["research_dataset_not_found"])
        return SimulatorBuildResult(pd.DataFrame(), pd.DataFrame(), report)
    if paths.candles_path is None or not paths.candles_path.exists():
        report.update(reason="missing_candles", validation_errors=["candles_not_found"])
        return SimulatorBuildResult(pd.DataFrame(), pd.DataFrame(), report)
    try:
        research = read_table(paths.research_dataset_path)
        missing = sorted(REQUIRED_RESEARCH_COLUMNS - set(research.columns))
        if missing:
            report.update(
                reason="invalid_research_schema",
                validation_errors=["missing_research_columns:" + ",".join(missing)],
            )
            return SimulatorBuildResult(pd.DataFrame(), pd.DataFrame(), report)
        candles = prepare_candles(read_table(paths.candles_path))
        contexts = prepare_trade_contexts(research, candles)
        grid, best_strategy = build_grid(contexts, config)
        if best_strategy is None:
            report.update(reason="empty_strategy_grid", validation_errors=["grid_has_no_rows"])
            return SimulatorBuildResult(grid, pd.DataFrame(), report)
        trade_results = build_trade_outcomes(contexts, best_strategy, config)
    except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError) as exc:
        report.update(
            status="failed",
            reason="simulation_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return SimulatorBuildResult(pd.DataFrame(), pd.DataFrame(), report)

    best = grid.loc[grid["is_candidate_best"]].iloc[0]
    checked_consistency = trade_results["mfe_mae_consistent"].notna()
    mismatch_count = int(
        trade_results.loc[checked_consistency, "mfe_mae_consistent"].eq(False).sum()
    )
    if mismatch_count:
        report["warnings"].append(f"mfe_mae_mismatch_rows:{mismatch_count}")
    report.update(
        status="ok",
        reason="simulation_ready",
        research_dataset_rows=int(len(research)),
        eligible_rows=int(research["is_research_eligible"].eq(True).sum()),
        blocked_rows=int(research["is_research_eligible"].ne(True).sum()),
        candles_rows=int(len(candles)),
        grid_rows=int(len(grid)),
        trade_simulation_rows=int(len(trade_results)),
        mfe_mae_consistency_checked_rows=int(checked_consistency.sum()),
        mfe_mae_mismatch_rows=mismatch_count,
        best_strategy_id=str(best["strategy_id"]),
        best_tp=_optional_number(best["tp_value"]),
        best_sl=_optional_number(best["sl_value"]),
        best_net_pnl=_optional_number(best["net_pnl"]),
        best_profit_factor=_optional_number(best["profit_factor"]),
        best_win_rate=_optional_number(best["win_rate"]),
        best_max_drawdown=_optional_number(best["max_drawdown"]),
        original_net_pnl=_optional_number(best["original_net_pnl"]),
        net_pnl_delta_vs_original=_optional_number(best["net_pnl_delta_vs_original"]),
    )
    if write:
        atomic_write_parquet(paths.output_grid_path, grid)
        atomic_write_parquet(paths.output_trade_path, trade_results)
        report["write_performed"] = True
        atomic_write_json(paths.report_path, report)
        summary = build_tp_sl_executive_summary(
            grid,
            trade_results,
            report,
            analysis_date_utc=(
                analysis_date_utc or pd.Timestamp.now(tz="UTC").isoformat()
            ),
        )
        atomic_write_json(paths.summary_path, summary)
        atomic_write_text(
            paths.executive_report_path,
            render_tp_sl_executive_markdown(summary),
        )
    return SimulatorBuildResult(grid, trade_results, report)
