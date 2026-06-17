"""Temporal walk-forward validation over realized trade outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.monte_carlo_risk import summarize_realized_pnl


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
class WalkForwardConfig:
    min_train_rows: int = 300
    test_rows: int = 100
    embargo_rows: int = 5
    max_folds: int = 24
    mode: str = "expanding"


def _safe_timestamp_frame(frame: pd.DataFrame, timestamp_column: str) -> pd.DataFrame:
    output = frame.copy()
    output["__validation_timestamp"] = pd.to_datetime(output[timestamp_column], utc=True, errors="coerce")
    output = output.dropna(subset=["__validation_timestamp"]).sort_values("__validation_timestamp")
    return output.reset_index(drop=True)


def _fold_ranges(total_rows: int, config: WalkForwardConfig) -> list[tuple[int, int, int, int]]:
    ranges: list[tuple[int, int, int, int]] = []
    cursor = int(config.min_train_rows) + int(config.embargo_rows)
    while cursor + int(config.test_rows) <= total_rows and len(ranges) < int(config.max_folds):
        if config.mode == "rolling":
            train_start = max(0, cursor - int(config.embargo_rows) - int(config.min_train_rows))
        else:
            train_start = 0
        train_end = cursor - int(config.embargo_rows)
        test_start = cursor
        test_end = cursor + int(config.test_rows)
        if train_end > train_start and test_end > test_start:
            ranges.append((train_start, train_end, test_start, test_end))
        cursor += int(config.test_rows)
    return ranges


def run_walkforward_validation(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    pnl_column: str,
    config: WalkForwardConfig | None = None,
) -> dict[str, Any]:
    cfg = config or WalkForwardConfig()
    if timestamp_column not in frame.columns:
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "timestamp_column_missing_for_walkforward",
            "timestamp_column": timestamp_column,
            "pnl_column": pnl_column,
        }
    if pnl_column not in frame.columns:
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "pnl_column_missing_for_walkforward",
            "timestamp_column": timestamp_column,
            "pnl_column": pnl_column,
        }

    ordered = _safe_timestamp_frame(frame, timestamp_column)
    if len(ordered) < int(cfg.min_train_rows) + int(cfg.embargo_rows) + int(cfg.test_rows):
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "insufficient_rows_for_walkforward",
            "rows": int(len(ordered)),
            "min_required_rows": int(cfg.min_train_rows + cfg.embargo_rows + cfg.test_rows),
        }

    ranges = _fold_ranges(int(len(ordered)), cfg)
    folds: list[dict[str, Any]] = []
    combined_test_pnl: list[float] = []
    for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(ranges, start=1):
        train = ordered.iloc[train_start:train_end]
        test = ordered.iloc[test_start:test_end]
        test_pnl = pd.to_numeric(test[pnl_column], errors="coerce").dropna().to_numpy(dtype="float64")
        combined_test_pnl.extend(float(value) for value in test_pnl)
        folds.append(
            {
                "fold": int(fold_idx),
                "train_start_index": int(train_start),
                "train_end_index_exclusive": int(train_end),
                "test_start_index": int(test_start),
                "test_end_index_exclusive": int(test_end),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_start_utc": train["__validation_timestamp"].min().isoformat(),
                "train_end_utc": train["__validation_timestamp"].max().isoformat(),
                "test_start_utc": test["__validation_timestamp"].min().isoformat(),
                "test_end_utc": test["__validation_timestamp"].max().isoformat(),
                "metrics": summarize_realized_pnl(test_pnl),
            }
        )

    if not folds:
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "no_valid_walkforward_folds",
            "rows": int(len(ordered)),
        }

    combined = np.asarray(combined_test_pnl, dtype="float64")
    return {
        **SAFETY_FLAGS,
        "status": "ok",
        "reason": "walkforward_completed",
        "timestamp_column": timestamp_column,
        "pnl_column": pnl_column,
        "rows": int(len(ordered)),
        "fold_count": int(len(folds)),
        "config": {
            "min_train_rows": int(cfg.min_train_rows),
            "test_rows": int(cfg.test_rows),
            "embargo_rows": int(cfg.embargo_rows),
            "max_folds": int(cfg.max_folds),
            "mode": cfg.mode,
        },
        "combined_test_metrics": summarize_realized_pnl(combined),
        "folds": folds,
    }
