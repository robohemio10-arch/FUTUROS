"""Research-only Monte Carlo risk simulation over realized trade PnL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd


SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "runtime_mode": "paper",
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_training_dataset": False,
    "research_only": True,
}


@dataclass(frozen=True)
class MonteCarloConfig:
    iterations: int = 2_000
    seed: int = 42
    ruin_level_usdt: float = 0.0
    starting_equity_usdt: float = 0.0
    block_size: int = 20


def max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    high_watermark = np.maximum.accumulate(equity)
    drawdowns = equity - high_watermark
    return float(drawdowns.min())


def _percentiles(values: np.ndarray, percentiles: Sequence[int]) -> dict[str, float]:
    if values.size == 0:
        return {f"p{p}": 0.0 for p in percentiles}
    return {f"p{p}": float(np.percentile(values, p)) for p in percentiles}


def _simulate_shuffle(pnl: np.ndarray, *, rng: np.random.Generator, iterations: int, starting_equity: float) -> tuple[np.ndarray, np.ndarray]:
    final_equity = np.zeros(iterations, dtype="float64")
    drawdowns = np.zeros(iterations, dtype="float64")
    for idx in range(iterations):
        sequence = rng.permutation(pnl)
        equity = starting_equity + np.cumsum(sequence)
        final_equity[idx] = float(equity[-1]) if equity.size else starting_equity
        drawdowns[idx] = max_drawdown(equity)
    return final_equity, drawdowns


def _simulate_bootstrap(pnl: np.ndarray, *, rng: np.random.Generator, iterations: int, starting_equity: float) -> tuple[np.ndarray, np.ndarray]:
    if pnl.size == 0:
        return np.zeros(iterations), np.zeros(iterations)
    final_equity = np.zeros(iterations, dtype="float64")
    drawdowns = np.zeros(iterations, dtype="float64")
    for idx in range(iterations):
        sequence = rng.choice(pnl, size=pnl.size, replace=True)
        equity = starting_equity + np.cumsum(sequence)
        final_equity[idx] = float(equity[-1])
        drawdowns[idx] = max_drawdown(equity)
    return final_equity, drawdowns


def _simulate_block_bootstrap(
    pnl: np.ndarray,
    *,
    rng: np.random.Generator,
    iterations: int,
    starting_equity: float,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    if pnl.size == 0:
        return np.zeros(iterations), np.zeros(iterations)
    safe_block = max(1, int(block_size))
    starts = np.arange(0, max(1, pnl.size - safe_block + 1), dtype="int64")
    final_equity = np.zeros(iterations, dtype="float64")
    drawdowns = np.zeros(iterations, dtype="float64")
    for idx in range(iterations):
        blocks: list[np.ndarray] = []
        while sum(len(block) for block in blocks) < pnl.size:
            start = int(rng.choice(starts))
            blocks.append(pnl[start : start + safe_block])
        sequence = np.concatenate(blocks)[: pnl.size]
        equity = starting_equity + np.cumsum(sequence)
        final_equity[idx] = float(equity[-1])
        drawdowns[idx] = max_drawdown(equity)
    return final_equity, drawdowns


def summarize_realized_pnl(pnl: np.ndarray) -> dict[str, Any]:
    if pnl.size == 0:
        return {
            "trade_count": 0,
            "net_pnl_usdt": 0.0,
            "gross_profit_usdt": 0.0,
            "gross_loss_usdt": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "average_win_usdt": 0.0,
            "average_loss_usdt": 0.0,
            "expectancy_usdt": 0.0,
            "max_drawdown_usdt": 0.0,
        }
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(losses.sum()) if losses.size else 0.0
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(abs(losses.mean())) if losses.size else 0.0
    win_rate = float(wins.size / pnl.size)
    loss_rate = float(losses.size / pnl.size)
    equity = np.cumsum(pnl)
    return {
        "trade_count": int(pnl.size),
        "net_pnl_usdt": float(pnl.sum()),
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": float(gross_profit / abs(gross_loss)) if gross_loss < 0.0 else 0.0,
        "win_rate": win_rate,
        "average_win_usdt": avg_win,
        "average_loss_usdt": avg_loss,
        "expectancy_usdt": float((win_rate * avg_win) - (loss_rate * avg_loss)),
        "max_drawdown_usdt": max_drawdown(equity),
    }


def run_monte_carlo(
    frame: pd.DataFrame,
    *,
    pnl_column: str,
    config: MonteCarloConfig | None = None,
) -> dict[str, Any]:
    cfg = config or MonteCarloConfig()
    pnl = pd.to_numeric(frame[pnl_column], errors="coerce").dropna().to_numpy(dtype="float64")
    if pnl.size < 2:
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "insufficient_pnl_rows_for_monte_carlo",
            "pnl_column": pnl_column,
            "rows_used": int(pnl.size),
        }

    rng = np.random.default_rng(int(cfg.seed))
    shuffle_final, shuffle_dd = _simulate_shuffle(
        pnl,
        rng=rng,
        iterations=int(cfg.iterations),
        starting_equity=float(cfg.starting_equity_usdt),
    )
    bootstrap_final, bootstrap_dd = _simulate_bootstrap(
        pnl,
        rng=rng,
        iterations=int(cfg.iterations),
        starting_equity=float(cfg.starting_equity_usdt),
    )
    block_final, block_dd = _simulate_block_bootstrap(
        pnl,
        rng=rng,
        iterations=int(cfg.iterations),
        starting_equity=float(cfg.starting_equity_usdt),
        block_size=int(cfg.block_size),
    )

    return {
        **SAFETY_FLAGS,
        "status": "ok",
        "reason": "monte_carlo_completed",
        "pnl_column": pnl_column,
        "rows_used": int(pnl.size),
        "iterations": int(cfg.iterations),
        "seed": int(cfg.seed),
        "block_size": int(cfg.block_size),
        "ruin_level_usdt": float(cfg.ruin_level_usdt),
        "starting_equity_usdt": float(cfg.starting_equity_usdt),
        "realized": summarize_realized_pnl(pnl),
        "shuffle": {
            "final_equity_percentiles": _percentiles(shuffle_final, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "drawdown_percentiles": _percentiles(shuffle_dd, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "risk_of_ruin": float(np.mean(shuffle_final <= float(cfg.ruin_level_usdt))),
            "negative_final_equity_probability": float(np.mean(shuffle_final < 0.0)),
        },
        "bootstrap": {
            "final_equity_percentiles": _percentiles(bootstrap_final, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "drawdown_percentiles": _percentiles(bootstrap_dd, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "risk_of_ruin": float(np.mean(bootstrap_final <= float(cfg.ruin_level_usdt))),
            "negative_final_equity_probability": float(np.mean(bootstrap_final < 0.0)),
        },
        "block_bootstrap": {
            "final_equity_percentiles": _percentiles(block_final, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "drawdown_percentiles": _percentiles(block_dd, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
            "risk_of_ruin": float(np.mean(block_final <= float(cfg.ruin_level_usdt))),
            "negative_final_equity_probability": float(np.mean(block_final < 0.0)),
        },
    }
