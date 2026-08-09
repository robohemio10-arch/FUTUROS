"""Read-only orchestration for fixed-threshold momentum validation."""

from __future__ import annotations

from pathlib import Path

from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)

from .contracts import (
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    TIMEFRAME,
    MomentumFixedThresholdValidationResult,
)
from .validation import validate_fixed_threshold_momentum


def run_fixed_threshold_momentum_validation(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    paper_db: str | Path | None = None,
    paper_snapshot_db: str | Path | None = None,
    candle_root: str | Path | None = None,
    allow_runtime_read: bool = False,
) -> MomentumFixedThresholdValidationResult:
    """Build the 5m paper research dataset and validate frozen momentum filters."""
    root = Path(project_root).resolve()
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
        timeframe=TIMEFRAME,
        allow_runtime_read=allow_runtime_read,
        write_report=False,
        write_dataset=False,
    )
    if dataset_result.report.get("status") not in {"ok", "warning"}:
        return MomentumFixedThresholdValidationResult(
            dataset=dataset_result.dataset,
            report={
                "schema_version": SCHEMA_VERSION,
                "status": "blocked",
                "reason": "profit_research_dataset_unavailable",
                "dataset_report": dataset_result.report,
                "runtime_read_requested": allow_runtime_read,
                "write_performed": False,
                **SAFETY_FLAGS,
            },
        )

    dataset, analysis = validate_fixed_threshold_momentum(dataset_result.dataset)
    report = {
        "schema_version": SCHEMA_VERSION,
        **analysis,
        "dataset_report": dataset_result.report,
        "runtime_read_requested": allow_runtime_read,
        "write_performed": False,
        **SAFETY_FLAGS,
    }
    return MomentumFixedThresholdValidationResult(dataset=dataset, report=report)
