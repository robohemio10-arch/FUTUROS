from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


BASELINE_NAMES = (
    "random_strategy",
    "always_predict_win",
    "always_predict_loss",
    "majority_class",
    "no_trade/cash",
)

COST_COLUMNS = (
    "cost_pct",
    "fee_pct",
    "fees_pct",
    "commission_pct",
    "slippage_pct",
    "spread_pct",
    "total_cost_pct",
)


class BaselineEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class BaselineEvaluationResult:
    status: str
    target_column: str
    return_column: str | None
    cost_columns: list[str]
    limitations: list[str]
    baselines: dict[str, dict[str, Any]]
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_baselines(
    frame: pd.DataFrame,
    *,
    target_column: str = "target_win",
    return_column: str = "return_pct",
    seed: int = 42,
) -> BaselineEvaluationResult:
    if not isinstance(frame, pd.DataFrame):
        raise BaselineEvaluationError("baseline_input_must_be_dataframe")
    if target_column not in frame.columns:
        raise BaselineEvaluationError(f"target_column_missing:{target_column}")

    target = pd.to_numeric(frame[target_column], errors="coerce")
    if target.isna().any():
        raise BaselineEvaluationError("target_column_contains_null_or_non_numeric")
    y_true = target.astype(int).clip(lower=0, upper=1).to_numpy()

    returns, resolved_return_column, limitations = resolve_returns(
        frame,
        return_column=return_column,
    )
    costs, used_cost_columns = resolve_costs(frame)
    if not used_cost_columns:
        limitations.append("no_cost_slippage_or_spread_columns_present")

    predictions = build_baseline_predictions(y_true, seed=seed)
    baselines: dict[str, dict[str, Any]] = {}
    for name, y_pred in predictions.items():
        trade_mask = np.zeros_like(y_pred, dtype=bool) if name == "no_trade/cash" else y_pred == 1
        baselines[name] = compute_metrics(
            y_true,
            y_pred,
            returns=returns,
            costs=costs,
            trade_mask=trade_mask,
        )

    return BaselineEvaluationResult(
        status="OK",
        target_column=target_column,
        return_column=resolved_return_column,
        cost_columns=used_cost_columns,
        limitations=limitations,
        baselines=baselines,
    )


def build_baseline_predictions(y_true: np.ndarray, *, seed: int) -> dict[str, np.ndarray]:
    rng = random.Random(seed)
    majority = int(np.mean(y_true) >= 0.5)
    return {
        "random_strategy": np.array([rng.randint(0, 1) for _ in y_true], dtype=int),
        "always_predict_win": np.ones_like(y_true, dtype=int),
        "always_predict_loss": np.zeros_like(y_true, dtype=int),
        "majority_class": np.full_like(y_true, majority, dtype=int),
        "no_trade/cash": np.zeros_like(y_true, dtype=int),
    }


def resolve_returns(
    frame: pd.DataFrame,
    *,
    return_column: str,
) -> tuple[np.ndarray | None, str | None, list[str]]:
    if return_column not in frame.columns:
        return None, None, [f"return_column_missing:{return_column}"]
    returns = pd.to_numeric(frame[return_column], errors="coerce").fillna(0.0).to_numpy(float)
    return returns, return_column, []


def resolve_costs(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    used = [column for column in COST_COLUMNS if column in frame.columns]
    if not used:
        return np.zeros(len(frame), dtype=float), []
    total = np.zeros(len(frame), dtype=float)
    for column in used:
        total += pd.to_numeric(frame[column], errors="coerce").fillna(0.0).to_numpy(float)
    return total, used


def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    returns: np.ndarray | None,
    costs: np.ndarray,
    trade_mask: np.ndarray,
) -> dict[str, Any]:
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    accuracy = float((y_true == y_pred).mean()) if len(y_true) else 0.0
    precision = float(tp / (tp + fp)) if (tp + fp) else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) else 0.0

    if returns is None:
        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "win_rate": None,
            "average_return_pct": None,
            "total_return_pct": None,
            "profit_factor": None,
            "max_drawdown": None,
            "trades": int(trade_mask.sum()),
        }

    net_returns = np.where(trade_mask, returns - costs, 0.0)
    selected = net_returns[trade_mask]
    gains = selected[selected > 0].sum()
    losses = selected[selected < 0].sum()
    equity = np.cumsum(net_returns)
    max_drawdown = simplified_max_drawdown(equity)

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "win_rate": float((selected > 0).mean()) if selected.size else 0.0,
        "average_return_pct": float(selected.mean()) if selected.size else 0.0,
        "total_return_pct": float(selected.sum()) if selected.size else 0.0,
        "profit_factor": float(gains / abs(losses)) if losses < 0 else None,
        "max_drawdown": max_drawdown,
        "trades": int(trade_mask.sum()),
    }


def simplified_max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(equity)
    drawdown = equity - running_peak
    return float(drawdown.min())


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
