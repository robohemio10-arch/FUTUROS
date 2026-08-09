"""Read-only orchestration for path-faithful profit-protection validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.research.paper_profit_maximization.metrics import prepare_profit_dataset
from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)
from smartcrypto.research.profit_research_dataset.candle_alignment import (
    align_trades_to_candles,
    load_candles,
    timeframe_seconds,
)

from .contracts import (
    MIN_CAUSAL_PATH_COVERAGE_RATIO,
    MIN_ELIGIBLE_TRADES,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    TIMEFRAME_PREFERENCE,
    PathFaithfulValidationResult,
)
from .simulation import validate_path_faithful_candidates


def run_path_faithful_walkforward(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    paper_db: str | Path | None = None,
    paper_snapshot_db: str | Path | None = None,
    candle_root: str | Path | None = None,
    allow_runtime_read: bool = False,
) -> PathFaithfulValidationResult:
    """Use the finest sufficiently covered candle path and validate fixed policies."""
    root = Path(project_root).resolve()
    attempts: list[dict[str, Any]] = []
    last_dataset = pd.DataFrame()

    for timeframe in TIMEFRAME_PREFERENCE:
        paths = resolve_build_paths(
            root,
            source_profile=source_profile,
            paper_db=paper_db,
            paper_snapshot_db=paper_snapshot_db,
            candle_root=candle_root,
            output_root=root / "data",
        )
        dataset_result = build_profit_research_dataset(
            paths,
            timeframe=timeframe,
            allow_runtime_read=allow_runtime_read,
            write_report=False,
            write_dataset=False,
        )
        last_dataset = dataset_result.dataset
        attempt: dict[str, Any] = {
            "timeframe": timeframe,
            "dataset_status": dataset_result.report.get("status"),
            "dataset_reason": dataset_result.report.get("reason"),
            "dataset_rows": int(len(dataset_result.dataset)),
            "paths_by_trade_count": 0,
            "causally_usable_path_count": 0,
            "causal_path_coverage_ratio": 0.0,
        }
        if dataset_result.report.get("status") not in {"ok", "warning"}:
            attempts.append(attempt)
            continue

        candle_load = load_candles(paths.candle_root, timeframe=timeframe)
        if candle_load.frame.empty:
            attempt["dataset_status"] = "blocked"
            attempt["dataset_reason"] = "timeframe_candles_unavailable"
            attempts.append(attempt)
            continue

        alignment = align_trades_to_candles(
            dataset_result.dataset,
            candle_load.frame,
            timeframe=timeframe,
        )
        path_count = len(alignment.paths_by_trade)
        usable_count, eligible_count, coverage = _causal_path_coverage(
            dataset_result.dataset,
            alignment.paths_by_trade,
            timeframe=timeframe,
        )
        attempt["paths_by_trade_count"] = path_count
        attempt["causally_usable_path_count"] = usable_count
        attempt["profit_optimization_eligible_count"] = eligible_count
        attempt["causal_path_coverage_ratio"] = coverage
        attempts.append(attempt)
        if (
            usable_count < MIN_ELIGIBLE_TRADES
            or coverage < MIN_CAUSAL_PATH_COVERAGE_RATIO
        ):
            continue

        dataset, analysis = validate_path_faithful_candidates(
            dataset_result.dataset,
            paths_by_trade=alignment.paths_by_trade,
            timeframe=timeframe,
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            **analysis,
            "selected_timeframe": timeframe,
            "timeframe_attempts": attempts,
            "dataset_report": dataset_result.report,
            "runtime_read_requested": allow_runtime_read,
            "write_performed": False,
            **SAFETY_FLAGS,
        }
        return PathFaithfulValidationResult(dataset=dataset, report=report)

    return PathFaithfulValidationResult(
        dataset=last_dataset,
        report={
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": "no_timeframe_with_sufficient_causal_path_coverage",
            "selected_timeframe": None,
            "timeframe_attempts": attempts,
            "runtime_read_requested": allow_runtime_read,
            "write_performed": False,
            "path_faithful_validation_passed": False,
            "ready_for_paper_wiring": False,
            **SAFETY_FLAGS,
        },
    )


def _causal_path_coverage(
    frame: pd.DataFrame,
    paths_by_trade: Mapping[str, pd.DataFrame],
    *,
    timeframe: str,
) -> tuple[int, int, float]:
    prepared, _ = prepare_profit_dataset(frame)
    eligible = prepared.loc[prepared["profit_optimization_eligible"]]
    if eligible.empty:
        return 0, 0, 0.0
    seconds = timeframe_seconds(timeframe)
    usable = 0
    for _, trade in eligible.iterrows():
        stable_id = str(trade.get("stable_trade_id"))
        path = paths_by_trade.get(stable_id)
        if path is None or path.empty:
            continue
        open_time = pd.to_datetime(trade.get("open_time_utc"), utc=True, errors="coerce")
        close_time = pd.to_datetime(trade.get("close_time_utc"), utc=True, errors="coerce")
        if pd.isna(open_time) or pd.isna(close_time):
            continue
        timestamps = pd.to_datetime(path["ts"], utc=True, errors="coerce")
        candle_end = timestamps + pd.to_timedelta(seconds, unit="s")
        if bool((timestamps.ge(open_time) & candle_end.le(close_time)).any()):
            usable += 1
    count = int(len(eligible))
    return usable, count, float(usable / count)
