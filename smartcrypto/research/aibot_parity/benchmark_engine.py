"""Orchestrator for the research-only AIBOT Trader Master benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .contracts import BENCHMARK_SCHEMA_VERSION, SOURCE_INVESTMENT_ID, safety_flags
from .performance_reconciliation import build_performance_reconciliation
from .persistence import AibotPersistenceError, persist_benchmark_reports, to_json_safe
from .trade_behavior_fingerprint import (
    build_behavior_fingerprint,
    build_rolling_behavior,
)
from .trader_master_loader import (
    DEFAULT_TRADER_MASTER_SOURCE,
    TraderMasterLoadError,
    load_trader_master_readonly,
)


ComponentName = Literal["audit", "fingerprint", "reconciliation", "benchmark"]


def build_aibot_benchmark(
    *,
    project_root: str | Path,
    trader_master_path: str | Path = DEFAULT_TRADER_MASTER_SOURCE,
    source_investment_id: str = SOURCE_INVESTMENT_ID,
    write_reports: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    try:
        loaded = load_trader_master_readonly(
            project_root=root,
            trader_master_path=trader_master_path,
            source_investment_id=source_investment_id,
        )
    except TraderMasterLoadError as exc:
        return _blocked_report(source_investment_id, str(exc), trader_master_path)

    source = loaded.source.to_dict()
    fingerprint = build_behavior_fingerprint(
        loaded.frame,
        source_investment_id=source_investment_id,
        source_batch_id=loaded.source.source_batch_id,
    )
    rolling = build_rolling_behavior(loaded.frame)
    reconciliation = build_performance_reconciliation(
        source_investment_id=source_investment_id,
        source_batch_id=loaded.source.source_batch_id,
        behavior_fingerprint=fingerprint,
    )
    benchmark_summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": "ok" if fingerprint["status"] == "ok" else "blocked",
        "reason": "current_snapshot_benchmark_generated",
        "source_investment_id": source_investment_id,
        "source_batch_id": loaded.source.source_batch_id,
        "source_artifact_sha256": loaded.source.source_artifact_sha256,
        "source_row_count": loaded.source.source_row_count,
        "quality_status": loaded.audit["quality_status"],
        "behavior_global": fingerprint["global"],
        "rolling_summary": rolling["summary"],
        "account_level_reconciliation_status": reconciliation[
            "account_level_reconciliation_status"
        ],
        "benchmark_snapshot_status": "CURRENT_SNAPSHOT_NOT_FINAL",
        "financial_closeout_status": "PENDING_TRADER_MASTER_REFRESH",
        "source_mutated": False,
        "safety_flags": safety_flags(),
    }
    report: dict[str, Any] = {
        **benchmark_summary,
        "source_registry": source,
        "trader_master_audit": loaded.audit,
        "behavior_fingerprint": fingerprint,
        "rolling_behavior": rolling,
        "performance_reconciliation": reconciliation,
        "benchmark_summary": benchmark_summary,
        "write_requested": bool(write_reports),
        "write_performed": False,
        "output_paths": {},
        "p0_findings": 0,
        "p1_findings": 0,
    }
    if write_reports:
        try:
            persisted = persist_benchmark_reports(
                project_root=root,
                source_batch_id=loaded.source.source_batch_id,
                loaded_at_utc=loaded.source.loaded_at_utc,
                payloads={
                    "source_registry": source,
                    "trader_master_audit": loaded.audit,
                    "behavior_fingerprint": fingerprint,
                    "rolling_behavior": rolling,
                    "performance_reconciliation": reconciliation,
                    "benchmark_summary": benchmark_summary,
                },
            )
        except AibotPersistenceError as exc:
            report.update(
                status="blocked",
                reason=str(exc),
                write_performed=False,
                p0_findings=1,
            )
        else:
            report.update(persisted)
    return to_json_safe(report)


def build_cli_payload(report: dict[str, Any], component: ComponentName) -> dict[str, Any]:
    common = {
        "status": report.get("status"),
        "reason": report.get("reason"),
        "schema_version": report.get("schema_version", BENCHMARK_SCHEMA_VERSION),
        "source_investment_id": report.get("source_investment_id"),
        "source_batch_id": report.get("source_batch_id"),
        "source_artifact_sha256": report.get("source_artifact_sha256"),
        "source_row_count": report.get("source_row_count", 0),
        "benchmark_snapshot_status": report.get("benchmark_snapshot_status"),
        "financial_closeout_status": report.get("financial_closeout_status"),
        "write_requested": report.get("write_requested", False),
        "write_performed": report.get("write_performed", False),
        "output_paths": report.get("output_paths", {}),
        "p0_findings": report.get("p0_findings", 0),
        "p1_findings": report.get("p1_findings", 0),
        "safety_flags": report.get("safety_flags", safety_flags()),
    }
    component_map = {
        "audit": "trader_master_audit",
        "fingerprint": "behavior_fingerprint",
        "reconciliation": "performance_reconciliation",
        "benchmark": "benchmark_summary",
    }
    common[component_map[component]] = report.get(component_map[component])
    if component == "fingerprint" and report.get("rolling_behavior"):
        common["rolling_summary"] = report["rolling_behavior"].get("summary")
    return to_json_safe(common)


def _blocked_report(
    source_investment_id: str,
    reason: str,
    trader_master_path: str | Path,
) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "source_investment_id": source_investment_id,
        "source_batch_id": None,
        "source_artifact_path": str(trader_master_path),
        "source_artifact_sha256": None,
        "source_row_count": 0,
        "benchmark_snapshot_status": "CURRENT_SNAPSHOT_NOT_FINAL",
        "financial_closeout_status": "PENDING_TRADER_MASTER_REFRESH",
        "write_requested": False,
        "write_performed": False,
        "output_paths": {},
        "source_mutated": False,
        "p0_findings": 1,
        "p1_findings": 0,
        "safety_flags": safety_flags(),
    }
