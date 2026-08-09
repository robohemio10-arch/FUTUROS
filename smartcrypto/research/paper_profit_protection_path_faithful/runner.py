"""Read-only orchestration for path-faithful profit-protection validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)
from smartcrypto.research.profit_research_dataset.candle_alignment import (
    align_trades_to_candles,
    load_candles,
)

from .contracts import (
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
        attempt: dict[str, Any] = {
            "timeframe": timeframe,
            "dataset_status": dataset_result.report.get("status"),
            "dataset_reason": dataset_result.report.get("reason"),
            "dataset_rows": int(len(dataset_result.dataset)),
            "paths_by_trade_count": 0,
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
        attempt["paths_by_trade_count"] = path_count
        attempts.append(attempt)
        if path_count < MIN_ELIGIBLE_TRADES:
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
        dataset=dataset_result.dataset if "dataset_result" in locals() else None,  # type: ignore[arg-type]
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
