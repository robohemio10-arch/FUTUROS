"""Read-only orchestration for momentum/profit-protection A/B research."""

from __future__ import annotations

from pathlib import Path

from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)

from .contracts import MomentumProtectionABResult, SAFETY_FLAGS, SCHEMA_VERSION
from .simulation import evaluate_momentum_protection_ab


def run_momentum_protection_ab(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    paper_db: str | Path | None = None,
    paper_snapshot_db: str | Path | None = None,
    candle_root: str | Path | None = None,
    timeframe: str = "5m",
    allow_runtime_read: bool = False,
) -> MomentumProtectionABResult:
    """Build the causal paper dataset and evaluate A/B entirely in memory."""
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
        timeframe=timeframe,
        allow_runtime_read=allow_runtime_read,
        write_report=False,
        write_dataset=False,
    )
    if dataset_result.report.get("status") not in {"ok", "warning"}:
        return MomentumProtectionABResult(
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

    dataset, analysis = evaluate_momentum_protection_ab(dataset_result.dataset)
    report = {
        "schema_version": SCHEMA_VERSION,
        **analysis,
        "dataset_report": dataset_result.report,
        "runtime_read_requested": allow_runtime_read,
        "write_performed": False,
        **SAFETY_FLAGS,
    }
    return MomentumProtectionABResult(dataset=dataset, report=report)
