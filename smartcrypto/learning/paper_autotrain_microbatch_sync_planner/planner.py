"""Research-only dry-run planner for paper autotrain microbatch synchronization.

The planner consumes the paper runtime source diagnostic primitives and produces
an auditable synchronization plan for quarantine microbatches. It does not create
microbatches, does not train, does not promote, does not write runtime state,
does not write operational parquet/sqlite, and has no authority over Freqtrade,
RiskManager, Qlib, IA Shadow runtime, signals, services, or orders.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_incremental_watermark_fix.watermark import (
    DEFAULT_QUARANTINE_DIR,
    DEFAULT_WATERMARK_PATH,
    load_seen_keys,
    read_watermark_state,
    sorted_unique,
)
from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics.diagnostics import (
    CLOSED_TRADES_CSV,
    FEEDBACK_EVENTS,
    PaperDbResolution,
    SourceRecords,
    build_divergence_summary,
    load_csv_records,
    load_feedback_records,
    load_microbatch_records,
    load_paper_db_records,
    parse_datetime_utc,
    resolve_paper_db,
)

SCHEMA_VERSION = "paper_autotrain_microbatch_sync_planner_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_microbatch_sync_planner_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_microbatch_sync_planner_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

DECISION_PLAN_MICROBATCH_SYNC = "PLANEJAR_SYNC_MICROBATCHES_PAPER_RESEARCH_ONLY"
DECISION_WAIT_NEW_TRADES = "AGUARDAR_NOVOS_TRADES_PAPER"
DECISION_PROVIDE_DB = "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
DECISION_RECONCILE_SOURCES = "RECONCILIAR_FONTES_PAPER_ANTES_DE_SYNC"
DECISION_INDETERMINATE = "MANTER_BLOQUEADO_ESTADO_INDETERMINADO"


@dataclass(frozen=True)
class PlannerPaths:
    watermark_path: Path
    quarantine_dir: Path
    feedback_events_path: Path
    closed_trades_csv_path: Path
    paper_db_path: Path | None
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_microbatch_sync_planner_v1(
    *,
    project_root: str | Path,
    paper_db_path: str | Path | None = None,
    allow_paper_db_read: bool = False,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_missing_paper_db: bool = False,
    fail_on_source_reconciliation_required: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a dry-run synchronization plan for paper autotrain microbatches."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()

    output_json = resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON)
    output_markdown = resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN)
    write_errors = validate_write_request(root, output_json, output_markdown, write_report)

    watermark_path = root / DEFAULT_WATERMARK_PATH
    watermark_read = read_watermark_state(watermark_path)
    watermark_state = watermark_read.state or {}
    seen_keys = set(load_seen_keys(watermark_read.state))
    watermark_close_time = parse_datetime_utc(watermark_state.get("watermark_close_time_utc"))

    closed_csv = load_csv_records("closed_trades_csv", root / CLOSED_TRADES_CSV, seen_keys)
    feedback = load_feedback_records(root / FEEDBACK_EVENTS, seen_keys)
    microbatch = load_microbatch_records(root / DEFAULT_QUARANTINE_DIR, seen_keys)

    explicit_db = resolve_path(root, paper_db_path, Path("")) if paper_db_path else None
    resolution = resolve_paper_db(
        root=root,
        explicit_db=explicit_db,
        read_requested=allow_paper_db_read,
        watermark_close_time=watermark_close_time,
        closed_csv_new_record_count=closed_csv.new_record_count,
        feedback_new_record_count=feedback.new_record_count,
    )
    paper_db = load_paper_db_records(
        path=resolution.selected_path,
        seen_keys=seen_keys,
        read_requested=allow_paper_db_read,
        watermark_close_time=watermark_close_time,
        selected_source_kind=resolution.selected_source_kind,
    )

    sources = {
        "paper_db": paper_db,
        "closed_trades_csv": closed_csv,
        "feedback_events": feedback,
        "microbatch": microbatch,
    }

    missing = build_missing_from_microbatch(sources, microbatch)
    pairwise = build_pairwise_source_differences(
        {
            "paper_db": paper_db,
            "closed_trades_csv": closed_csv,
            "feedback_events": feedback,
        }
    )
    source_reconciliation = build_source_reconciliation(
        paper_db=paper_db,
        closed_csv=closed_csv,
        feedback=feedback,
        resolution=resolution,
        pairwise=pairwise,
    )
    sync_plan = build_sync_plan(
        paper_db=paper_db,
        closed_csv=closed_csv,
        feedback=feedback,
        microbatch=microbatch,
        missing=missing,
        source_reconciliation=source_reconciliation,
        resolution=resolution,
    )

    status, reason, decision, status_blockers, status_warnings = decide_status(
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        paper_db=paper_db,
        closed_csv=closed_csv,
        feedback=feedback,
        microbatch=microbatch,
        missing=missing,
        source_reconciliation=source_reconciliation,
        write_errors=write_errors,
        allow_paper_db_read=allow_paper_db_read,
        fail_on_missing_paper_db=fail_on_missing_paper_db,
        fail_on_source_reconciliation_required=fail_on_source_reconciliation_required,
    )

    warnings = sorted_unique(
        [
            *watermark_read.warnings,
            *resolution.warnings,
            *paper_db.warnings,
            *closed_csv.warnings,
            *feedback.warnings,
            *microbatch.warnings,
            *status_warnings,
        ]
    )
    blockers = sorted_unique([*write_errors, *watermark_read.blockers, *status_blockers])

    paths = PlannerPaths(
        watermark_path=watermark_path,
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        feedback_events_path=root / FEEDBACK_EVENTS,
        closed_trades_csv_path=root / CLOSED_TRADES_CSV,
        paper_db_path=resolution.selected_path,
        output_json=output_json,
        output_markdown=output_markdown,
    )
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "planner_mode": "dry_run_read_only",
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": bool(watermark_read.exists),
        "watermark_status": watermark_read.status,
        "watermark_seen_record_count": len(seen_keys),
        "watermark_seen_record_keys_sha256": watermark_state.get("seen_record_keys_sha256"),
        "watermark_close_time_utc": watermark_state.get("watermark_close_time_utc"),
        "watermark_order_id": watermark_state.get("watermark_order_id"),
        "watermark_record_hash": watermark_state.get("watermark_record_hash"),
        "paper_db_read_requested": bool(allow_paper_db_read),
        "paper_db_path": str(paths.paper_db_path) if paths.paper_db_path else None,
        "paper_db_status": paper_db.status,
        "paper_db_source_kind": paper_db.metadata.get("source_kind"),
        "paper_db_authority_status": resolution.authority_status,
        "paper_db_selected_reason": resolution.selected_reason,
        "paper_db_selected_source_kind": resolution.selected_source_kind,
        "paper_db_runtime_stale": resolution.runtime_stale,
        "paper_db_snapshot_fresh": resolution.snapshot_fresh,
        "paper_db_snapshot_aligned_with_exports": resolution.snapshot_aligned_with_exports,
        "paper_db_row_count": paper_db.row_count,
        "paper_db_unique_record_count": paper_db.unique_record_count,
        "paper_db_new_record_count": paper_db.new_record_count,
        "paper_db_new_after_watermark_count": paper_db.new_record_count,
        "paper_db_max_close_time_utc": paper_db.metadata.get("max_close_time_utc"),
        "closed_trades_csv_status": closed_csv.status,
        "closed_trades_csv_row_count": closed_csv.row_count,
        "closed_trades_csv_unique_record_count": closed_csv.unique_record_count,
        "closed_trades_csv_new_record_count": closed_csv.new_record_count,
        "feedback_status": feedback.status,
        "feedback_event_count": feedback.row_count,
        "feedback_unique_record_count": feedback.unique_record_count,
        "feedback_new_record_count": feedback.new_record_count,
        "microbatch_status": microbatch.status,
        "microbatch_source_file_count": microbatch.source_file_count,
        "microbatch_row_count": microbatch.row_count,
        "microbatch_unique_record_count": microbatch.unique_record_count,
        "microbatch_new_record_count": microbatch.new_record_count,
        "new_records_by_source": {
            name: source.new_record_count for name, source in sources.items()
        },
        "source_rows_by_source": {
            name: source.row_count for name, source in sources.items()
        },
        "source_unique_records_by_source": {
            name: source.unique_record_count for name, source in sources.items()
        },
        "source_status": {
            name: source_to_status(source) for name, source in sources.items()
        },
        "microbatch_missing_counts_by_source": missing["counts"],
        "microbatch_missing_key_samples_by_source": missing["samples"],
        "microbatch_missing_total_unique_native_keys": missing["total_unique_native_key_count"],
        "microbatch_overhang_record_count": missing["microbatch_overhang_record_count"],
        "source_pairwise_divergence_counts": pairwise["counts"],
        "source_pairwise_divergence_samples": pairwise["samples"],
        "source_reconciliation": source_reconciliation,
        "divergence_summary": build_divergence_summary(paper_db, closed_csv, feedback, microbatch),
        "sync_plan": sync_plan,
        "sync_plan_status": sync_plan["plan_status"],
        "sync_plan_reason": sync_plan["plan_reason"],
        "sync_plan_candidate_count": sync_plan["candidate_count"],
        "sync_plan_requires_source_reconciliation": sync_plan["requires_source_reconciliation"],
        "ready_for_sync_execution": False,
        "ready_for_accumulation_recheck": False,
        "ready_for_candidate_evaluation_recheck": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "fail_on_missing_paper_db": bool(fail_on_missing_paper_db),
        "fail_on_source_reconciliation_required": bool(fail_on_source_reconciliation_required),
        "blockers": blockers,
        "warnings": warnings,
        "output_paths": {
            "json": str(output_json),
            "markdown": str(output_markdown),
        },
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def build_missing_from_microbatch(sources: Mapping[str, SourceRecords], microbatch: SourceRecords) -> dict[str, Any]:
    source_names = ("paper_db", "closed_trades_csv", "feedback_events")
    missing_by_source: dict[str, set[str]] = {}
    for name in source_names:
        source = sources[name]
        missing_by_source[name] = set(source.new_record_keys).difference(microbatch.record_keys)

    union_missing = set().union(*missing_by_source.values()) if missing_by_source else set()
    upstream_union = set().union(*(sources[name].new_record_keys for name in source_names))
    microbatch_overhang = set(microbatch.record_keys).difference(upstream_union)

    return {
        "sets": missing_by_source,
        "counts": {name: len(keys) for name, keys in missing_by_source.items()},
        "samples": {name: sorted(keys)[:25] for name, keys in missing_by_source.items()},
        "total_unique_native_key_count": len(union_missing),
        "microbatch_overhang_record_count": len(microbatch_overhang),
        "microbatch_overhang_key_sample": sorted(microbatch_overhang)[:25],
    }


def build_pairwise_source_differences(sources: Mapping[str, SourceRecords]) -> dict[str, Any]:
    names = sorted(sources)
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for left_index, left_name in enumerate(names):
        for right_name in names[left_index + 1 :]:
            left_keys = set(sources[left_name].new_record_keys)
            right_keys = set(sources[right_name].new_record_keys)
            diff = left_keys.symmetric_difference(right_keys)
            key = f"{left_name}_vs_{right_name}"
            counts[key] = len(diff)
            samples[key] = sorted(diff)[:25]
    return {"counts": counts, "samples": samples}


def build_source_reconciliation(
    *,
    paper_db: SourceRecords,
    closed_csv: SourceRecords,
    feedback: SourceRecords,
    resolution: PaperDbResolution,
    pairwise: Mapping[str, Any],
) -> dict[str, Any]:
    counts = {
        "paper_db": paper_db.new_record_count,
        "closed_trades_csv": closed_csv.new_record_count,
        "feedback_events": feedback.new_record_count,
    }
    nonzero_counts = [value for value in counts.values() if value > 0]
    count_range = max(nonzero_counts) - min(nonzero_counts) if nonzero_counts else 0
    pairwise_counts = dict(pairwise.get("counts") or {})
    pairwise_native_key_divergence_detected = any(int(value) > 0 for value in pairwise_counts.values())

    requires_reconciliation = (
        count_range > 0
        or pairwise_native_key_divergence_detected
        or bool(resolution.snapshot_fresh and not resolution.snapshot_aligned_with_exports)
    )

    return {
        "requires_reconciliation": bool(requires_reconciliation),
        "reason": (
            "source_counts_or_native_keys_diverge"
            if requires_reconciliation
            else "source_counts_and_native_keys_aligned"
        ),
        "counts": counts,
        "count_range": int(count_range),
        "pairwise_native_key_divergence_detected": bool(pairwise_native_key_divergence_detected),
        "snapshot_requires_authority_review": bool(
            resolution.selected_source_kind == "snapshot_db"
            and resolution.authority_status == "snapshot_db_fresh_requires_authority_review"
        ),
        "paper_db_snapshot_aligned_with_exports": bool(resolution.snapshot_aligned_with_exports),
        "recommended_reconciliation_order": [
            "paper_db_snapshot_close_time",
            "closed_trades_csv_order_close",
            "feedback_events_order_close",
            "microbatch_existing_keys",
        ],
    }


def build_sync_plan(
    *,
    paper_db: SourceRecords,
    closed_csv: SourceRecords,
    feedback: SourceRecords,
    microbatch: SourceRecords,
    missing: Mapping[str, Any],
    source_reconciliation: Mapping[str, Any],
    resolution: PaperDbResolution,
) -> dict[str, Any]:
    counts = dict(missing["counts"])
    candidate_count = max(counts.values()) if counts else 0
    any_missing = candidate_count > 0
    requires_reconciliation = bool(source_reconciliation["requires_reconciliation"])

    if not any_missing:
        status = "not_required"
        reason = "microbatch_has_no_missing_new_records"
    elif requires_reconciliation:
        status = "blocked_requires_source_reconciliation"
        reason = "source_reconciliation_required_before_sync_execution"
    else:
        status = "dry_run_plan_ready"
        reason = "missing_microbatch_records_identified"

    if resolution.selected_source_kind == "snapshot_db":
        candidate_authority = "paper_db_snapshot"
    elif resolution.selected_source_kind == "runtime_db":
        candidate_authority = "paper_db_runtime"
    else:
        candidate_authority = "exports_feedback"

    return {
        "plan_status": status,
        "plan_reason": reason,
        "candidate_count": int(candidate_count),
        "candidate_authority_source": candidate_authority,
        "candidate_authority_status": resolution.authority_status,
        "requires_source_reconciliation": requires_reconciliation,
        "recommended_source_priority": [
            "paper_db",
            "closed_trades_csv",
            "feedback_events",
            "microbatch",
        ],
        "paper_db_missing_count": int(counts.get("paper_db", 0)),
        "closed_trades_csv_missing_count": int(counts.get("closed_trades_csv", 0)),
        "feedback_missing_count": int(counts.get("feedback_events", 0)),
        "microbatch_existing_unique_record_count": int(microbatch.unique_record_count),
        "paper_db_new_record_count": int(paper_db.new_record_count),
        "closed_trades_csv_new_record_count": int(closed_csv.new_record_count),
        "feedback_new_record_count": int(feedback.new_record_count),
        "execution_authorized": False,
        "write_authorized": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_promote_model": False,
    }


def decide_status(
    *,
    watermark_exists: bool,
    watermark_status: str,
    paper_db: SourceRecords,
    closed_csv: SourceRecords,
    feedback: SourceRecords,
    microbatch: SourceRecords,
    missing: Mapping[str, Any],
    source_reconciliation: Mapping[str, Any],
    write_errors: Sequence[str],
    allow_paper_db_read: bool,
    fail_on_missing_paper_db: bool,
    fail_on_source_reconciliation_required: bool,
) -> tuple[str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_INDETERMINATE, list(write_errors), []
    if not watermark_exists:
        return "blocked", "missing_watermark_state", DECISION_INDETERMINATE, ["missing_watermark_state"], []
    if watermark_status == "invalid":
        return "blocked", "watermark_state_invalid", DECISION_INDETERMINATE, ["watermark_state_invalid"], []
    if not allow_paper_db_read:
        return "blocked", "paper_db_read_not_requested", DECISION_PROVIDE_DB, ["paper_db_read_not_requested"], []
    if paper_db.status in {"missing", "unreadable", "invalid_schema"}:
        blockers = ["paper_db_source_missing_or_unreadable"]
        if fail_on_missing_paper_db:
            blockers.append("fail_on_missing_paper_db_triggered")
        return "blocked", "paper_db_source_missing_or_unreadable", DECISION_PROVIDE_DB, blockers, []
    missing_counts = dict(missing["counts"])
    if max(missing_counts.values() or [0]) == 0:
        return "blocked", "no_microbatch_sync_required", DECISION_WAIT_NEW_TRADES, ["no_microbatch_sync_required"], []
    if bool(source_reconciliation["requires_reconciliation"]):
        blockers = ["source_reconciliation_required_before_sync_execution"]
        if fail_on_source_reconciliation_required:
            blockers.append("fail_on_source_reconciliation_required_triggered")
        return (
            "blocked",
            "source_reconciliation_required_before_sync_execution",
            DECISION_RECONCILE_SOURCES,
            blockers,
            ["dry_run_plan_available_no_execution_authority"],
        )
    if max(paper_db.new_record_count, closed_csv.new_record_count, feedback.new_record_count) > 0 and microbatch.new_record_count == 0:
        return (
            "blocked",
            "microbatch_sync_plan_ready_no_execution_authority",
            DECISION_PLAN_MICROBATCH_SYNC,
            ["microbatch_sync_plan_ready_no_execution_authority"],
            ["dry_run_plan_available_no_execution_authority"],
        )
    return "blocked", "microbatch_sync_planner_state_indeterminate", DECISION_INDETERMINATE, [
        "microbatch_sync_planner_state_indeterminate"
    ], []


def source_to_status(source: SourceRecords) -> dict[str, Any]:
    return {
        "status": source.status,
        "path": source.path,
        "row_count": source.row_count,
        "unique_record_count": source.unique_record_count,
        "new_record_count": source.new_record_count,
        "already_seen_record_count": source.already_seen_record_count,
        "reason": source.reason,
        "metadata": dict(source.metadata),
    }


def maybe_write_report(
    report: dict[str, Any],
    paths: PlannerPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report

    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))

    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True

    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Autotrain Microbatch Sync Planner V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Planner mode: `{report.get('planner_mode')}`",
            f"- Paper DB source: `{report.get('paper_db_source_kind')}`",
            f"- Paper DB authority: `{report.get('paper_db_authority_status')}`",
            f"- Paper DB new records: `{report.get('paper_db_new_after_watermark_count')}`",
            f"- CSV new records: `{report.get('closed_trades_csv_new_record_count')}`",
            f"- Feedback new records: `{report.get('feedback_new_record_count')}`",
            f"- Microbatch new records: `{report.get('microbatch_new_record_count')}`",
            f"- Sync plan status: `{report.get('sync_plan_status')}`",
            f"- Sync plan candidate count: `{report.get('sync_plan_candidate_count')}`",
            f"- Source reconciliation required: `{report.get('sync_plan_requires_source_reconciliation')}`",
            "",
            "## Conclusao",
            "",
            "Este planner apenas materializa um plano dry-run/read-only.",
            "Ele nao cria microbatch, nao treina, nao promove, nao escreve runtime,",
            "nao altera Freqtrade/RiskManager/Qlib/IA Shadow e nao envia ordens.",
            "",
        ]
    )


def validate_write_request(root: Path, output_json: Path, output_markdown: Path, write_report: bool) -> list[str]:
    if not write_report:
        return []
    errors: list[str] = []
    errors.extend(validate_path_under(root, output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    errors.extend(validate_path_under(root, output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    return sorted_unique(errors)


def validate_path_under(root: Path, path: Path, allowed: Path, reason: str) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed).resolve())
    except ValueError:
        return [reason]
    return []


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def safety_flags(*, write_report_requested: bool, write_report_performed: bool) -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "read_only": not write_report_requested,
        "write_report_requested": bool(write_report_requested),
        "write_report_performed": bool(write_report_performed),
        "write_performed": bool(write_report_performed),
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "runs_training": False,
        "trains_model": False,
        "training_allowed": False,
        "ready_for_training": False,
        "promotes_model": False,
        "promotion_allowed": False,
        "ready_for_promotion": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "runtime_allowed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_operational_parquet": False,
        "writes_active_registry": False,
        "writes_quarantine_registry": False,
        "writes_active_model_artifact": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "updates_freqtrade": False,
        "updates_freqtrade_config": False,
        "updates_freqtrade_strategy": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "qlib_runtime_updated": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "ai_shadow_runtime_updated": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_promote_model": False,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
