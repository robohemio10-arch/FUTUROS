"""Research-only recheck after the paper autotrain incremental watermark fix.

The recheck reads already materialized quarantine evidence, compares its
unique record identity against the Branch 67 watermark, and reports whether the
accumulation/evaluation layer should continue waiting for genuinely new paper
trades. It is no-write by default and has no operational authority.
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
    load_existing_microbatches,
    load_seen_keys,
    normalize_records,
    read_watermark_state,
    sorted_unique,
    summarize_normalized_records,
)

SCHEMA_VERSION = "paper_autotrain_watermark_accumulation_recheck_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

WATERMARK_FIX_REPORT = Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.json")
FRESHNESS_REPORT = Path("data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json")
ACCUMULATION_REPORT = Path("data/reports/paper_autotrain_evidence_accumulation_window_v1.json")
CANDIDATE_EVALUATION_REPORT = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json")
ACTIVATION_REPORT = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")
QUARANTINE_REGISTRY = Path("data/registries/quarantine/paper_autotrain_candidate_registry_v1.json")

DECISION_WAIT_NEW_TRADES = "AGUARDAR_NOVOS_TRADES_PAPER"
DECISION_WAIT_MICROBATCHES = "AGUARDAR_MICROBATCHES_DE_QUARENTENA"
DECISION_BOOTSTRAP_WATERMARK = "BOOTSTRAP_WATERMARK_RESEARCH_ONLY_ANTES_DE_NOVO_TREINO"
DECISION_FIX_WATERMARK = "CORRIGIR_WATERMARK_CORROMPIDO"
DECISION_NEW_RECORDS_RESEARCH = "NOVOS_TRADES_PAPER_DETECTADOS_RESEARCH_ONLY"


@dataclass(frozen=True)
class RecheckPaths:
    quarantine_dir: Path
    watermark_path: Path
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_watermark_accumulation_recheck_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_stale: bool = False,
    fail_on_no_new_records: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the post-watermark accumulation recheck report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path)
    output_paths = {"json": str(paths.output_json), "markdown": str(paths.output_markdown)}
    write_errors = validate_write_request(root, paths, write_report)
    optional_sources = load_optional_sources(root)

    sources = load_existing_microbatches(paths.quarantine_dir)
    source_frame = sources["frame"]
    warnings = [*sources["warnings"], *optional_sources["warnings"]]
    blockers = [*write_errors]

    if source_frame.empty:
        blockers.append("missing_quarantine_microbatch_sources")
        report = build_base_report(
            root=root,
            generated_at=generated_at,
            status="blocked",
            reason="missing_quarantine_microbatch_sources",
            decision=DECISION_WAIT_MICROBATCHES,
            paths=paths,
            output_paths=output_paths,
            write_report=write_report,
            blockers=sorted_unique(blockers),
            warnings=sorted_unique(warnings),
            optional_sources=optional_sources,
            fail_on_stale=fail_on_stale,
            fail_on_no_new_records=fail_on_no_new_records,
        )
        return maybe_write_report(report, paths, write_report, write_errors)

    normalized = normalize_records(source_frame)
    summary = summarize_normalized_records(normalized)
    watermark_read = read_watermark_state(paths.watermark_path)
    unique_keys = set(str(key) for key in summary["unique_record_keys"])
    seen_keys = set(load_seen_keys(watermark_read.state))
    already_seen_keys = sorted(unique_keys.intersection(seen_keys))
    new_keys = sorted(unique_keys.difference(seen_keys))
    duplicate_record_count = int(summary["duplicate_record_count"])
    source_row_count = int(len(source_frame))
    unique_record_count = int(summary["unique_record_count"])
    duplicate_rate = round(duplicate_record_count / source_row_count, 10) if source_row_count else 0.0

    status, reason, decision, status_blockers, status_warnings = decide_status(
        source_row_count=source_row_count,
        unique_record_count=unique_record_count,
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        new_unique_records_count=len(new_keys),
        write_errors=write_errors,
        fail_on_stale=fail_on_stale,
        fail_on_no_new_records=fail_on_no_new_records,
    )
    blockers.extend(status_blockers)
    blockers.extend(watermark_read.blockers)
    warnings.extend(status_warnings)
    warnings.extend(watermark_read.warnings)

    all_seen = bool(unique_keys) and len(already_seen_keys) == len(unique_keys)
    raw_rows_should_not_count = source_row_count > unique_record_count and all_seen
    stale_prevented = all_seen and not new_keys and watermark_read.status == "ok"
    upstream_consistency = summarize_upstream_consistency(optional_sources)
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": int(sources["source_file_count"]),
        "source_row_count": source_row_count,
        "raw_source_row_count": source_row_count,
        "unique_record_count": unique_record_count,
        "duplicate_record_count": duplicate_record_count,
        "duplicate_rate": duplicate_rate,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": bool(watermark_read.exists),
        "watermark_status": watermark_read.status,
        "watermark_seen_record_count": len(seen_keys),
        "watermark_seen_record_keys_sha256": (watermark_read.state or {}).get("seen_record_keys_sha256"),
        "watermark_close_time_utc": (watermark_read.state or {}).get("watermark_close_time_utc"),
        "watermark_order_id": (watermark_read.state or {}).get("watermark_order_id"),
        "watermark_record_hash": (watermark_read.state or {}).get("watermark_record_hash"),
        "already_seen_record_count": len(already_seen_keys),
        "new_unique_records_count": len(new_keys),
        "new_record_key_sample": new_keys[:25],
        "already_seen_record_key_sample": already_seen_keys[:25],
        "all_unique_records_seen_by_watermark": all_seen,
        "watermark_prevents_reaccumulation": stale_prevented,
        "raw_rows_should_not_count_as_new_evidence": raw_rows_should_not_count,
        "accumulator_should_ignore_duplicate_raw_rows": raw_rows_should_not_count,
        "stale_duplicate_microbatch_prevented": stale_prevented,
        "training_prevented_by_watermark": stale_prevented,
        "would_write_microbatch": bool(new_keys and watermark_read.status == "ok"),
        "would_run_training": False,
        "would_update_registry": False,
        "would_promote_model": False,
        "candidate_recheck_allowed": False,
        "record_key_strategy_counts": summary["record_key_strategy_counts"],
        "optional_source_status": optional_sources["source_status"],
        "upstream_report_consistency": upstream_consistency,
        "feedback_source_summary": optional_sources["feedback_summary"],
        "fail_on_stale": bool(fail_on_stale),
        "fail_on_no_new_records": bool(fail_on_no_new_records),
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def build_paths(root: Path, output_json_path: str | Path | None, output_markdown_path: str | Path | None) -> RecheckPaths:
    return RecheckPaths(
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        watermark_path=root / DEFAULT_WATERMARK_PATH,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def decide_status(
    *,
    source_row_count: int,
    unique_record_count: int,
    watermark_exists: bool,
    watermark_status: str,
    new_unique_records_count: int,
    write_errors: Sequence[str],
    fail_on_stale: bool,
    fail_on_no_new_records: bool,
) -> tuple[str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_FIX_WATERMARK, list(write_errors), []
    if source_row_count == 0:
        return "blocked", "missing_quarantine_microbatch_sources", DECISION_WAIT_MICROBATCHES, [
            "missing_quarantine_microbatch_sources"
        ], []
    if not watermark_exists:
        return "blocked", "missing_watermark_state", DECISION_BOOTSTRAP_WATERMARK, ["missing_watermark_state"], []
    if watermark_status == "invalid":
        return "blocked", "watermark_state_invalid", DECISION_FIX_WATERMARK, ["watermark_state_invalid"], []
    if new_unique_records_count == 0:
        blockers = ["no_new_incremental_records_after_watermark"]
        if fail_on_no_new_records:
            blockers.append("fail_on_no_new_records_triggered")
        if fail_on_stale and unique_record_count < source_row_count:
            blockers.append("fail_on_stale_triggered")
        return "blocked", "no_new_incremental_records_after_watermark", DECISION_WAIT_NEW_TRADES, blockers, []
    return "ok", "incremental_records_available_after_watermark", DECISION_NEW_RECORDS_RESEARCH, [], []


def build_base_report(
    *,
    root: Path,
    generated_at: str,
    status: str,
    reason: str,
    decision: str,
    paths: RecheckPaths,
    output_paths: Mapping[str, str],
    write_report: bool,
    blockers: Sequence[str],
    warnings: Sequence[str],
    optional_sources: Mapping[str, Any],
    fail_on_stale: bool,
    fail_on_no_new_records: bool,
) -> dict[str, Any]:
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": 0,
        "source_row_count": 0,
        "raw_source_row_count": 0,
        "unique_record_count": 0,
        "duplicate_record_count": 0,
        "duplicate_rate": 0.0,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": False,
        "watermark_status": "missing",
        "watermark_seen_record_count": 0,
        "watermark_seen_record_keys_sha256": None,
        "watermark_close_time_utc": None,
        "watermark_order_id": None,
        "watermark_record_hash": None,
        "already_seen_record_count": 0,
        "new_unique_records_count": 0,
        "new_record_key_sample": [],
        "already_seen_record_key_sample": [],
        "all_unique_records_seen_by_watermark": False,
        "watermark_prevents_reaccumulation": False,
        "raw_rows_should_not_count_as_new_evidence": False,
        "accumulator_should_ignore_duplicate_raw_rows": False,
        "stale_duplicate_microbatch_prevented": False,
        "training_prevented_by_watermark": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_update_registry": False,
        "would_promote_model": False,
        "candidate_recheck_allowed": False,
        "record_key_strategy_counts": {},
        "optional_source_status": optional_sources["source_status"],
        "upstream_report_consistency": summarize_upstream_consistency(optional_sources),
        "feedback_source_summary": optional_sources["feedback_summary"],
        "fail_on_stale": bool(fail_on_stale),
        "fail_on_no_new_records": bool(fail_on_no_new_records),
        "blockers": list(blockers),
        "warnings": list(warnings),
        "output_paths": dict(output_paths),
        **safety,
        "safety_flags": safety,
    }


def load_optional_sources(root: Path) -> dict[str, Any]:
    source_status: dict[str, dict[str, Any]] = {}
    payloads: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []
    for name, relative_path in {
        "watermark_fix_report": WATERMARK_FIX_REPORT,
        "freshness_report": FRESHNESS_REPORT,
        "accumulation_report": ACCUMULATION_REPORT,
        "candidate_evaluation_report": CANDIDATE_EVALUATION_REPORT,
        "activation_report": ACTIVATION_REPORT,
        "quarantine_registry": QUARANTINE_REGISTRY,
    }.items():
        path = root / relative_path
        if not path.exists():
            source_status[name] = {"path": str(path), "status": "missing_optional"}
            warnings.append(f"optional_source_missing:{name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            source_status[name] = {"path": str(path), "status": "invalid_optional", "error": exc.__class__.__name__}
            warnings.append(f"optional_source_invalid:{name}:{exc.__class__.__name__}")
            continue
        payloads[name] = payload
        source_status[name] = {
            "path": str(path),
            "status": "ok",
            "schema_version": payload.get("schema_version"),
            "report_status": payload.get("status"),
            "reason": payload.get("reason"),
            "decision": payload.get("decision"),
        }

    feedback_path = root / FEEDBACK_EVENTS
    feedback_summary: dict[str, Any] = {"path": str(feedback_path), "status": "missing_optional", "event_count": 0}
    if feedback_path.exists():
        try:
            event_count = sum(1 for line in feedback_path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
            feedback_summary = {"path": str(feedback_path), "status": "ok", "event_count": event_count}
        except OSError as exc:
            feedback_summary = {"path": str(feedback_path), "status": "invalid_optional", "error": exc.__class__.__name__}
            warnings.append(f"optional_source_invalid:feedback_events:{exc.__class__.__name__}")
    else:
        warnings.append("optional_source_missing:feedback_events")
    source_status["feedback_events"] = feedback_summary
    return {
        "source_status": source_status,
        "payloads": payloads,
        "feedback_summary": feedback_summary,
        "warnings": sorted_unique(warnings),
    }


def summarize_upstream_consistency(optional_sources: Mapping[str, Any]) -> dict[str, Any]:
    payloads = optional_sources.get("payloads", {})
    if not isinstance(payloads, Mapping):
        payloads = {}

    watermark_fix = dict(payloads.get("watermark_fix_report") or {})
    freshness = dict(payloads.get("freshness_report") or {})
    accumulation = dict(payloads.get("accumulation_report") or {})
    activation = dict(payloads.get("activation_report") or {})
    candidate = dict(payloads.get("candidate_evaluation_report") or {})
    return {
        "watermark_fix_status": watermark_fix.get("status"),
        "watermark_fix_reason": watermark_fix.get("reason"),
        "watermark_fix_decision": watermark_fix.get("decision"),
        "watermark_fix_new_unique_records_count": watermark_fix.get("new_unique_records_count"),
        "freshness_all_runs_reobserve_same_records": freshness.get("all_runs_reobserve_same_records"),
        "freshness_source_row_count": freshness.get("source_row_count"),
        "freshness_unique_record_count": freshness.get("unique_record_count"),
        "accumulation_status": accumulation.get("status"),
        "accumulation_reason": accumulation.get("reason"),
        "accumulation_source_row_count": accumulation.get("source_row_count"),
        "accumulation_duplicate_rate": accumulation.get("duplicate_rate"),
        "activation_status": activation.get("status"),
        "activation_reason": activation.get("reason"),
        "candidate_evaluation_status": candidate.get("status"),
        "candidate_evaluation_reason": candidate.get("reason"),
    }


def maybe_write_report(
    report: dict[str, Any],
    paths: RecheckPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report
    write_json(paths.output_json, report)
    paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True
    write_json(paths.output_json, report)
    paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Autotrain Watermark Accumulation Recheck V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Source rows: `{report.get('source_row_count')}`",
        f"- Unique records: `{report.get('unique_record_count')}`",
        f"- Duplicate records: `{report.get('duplicate_record_count')}`",
        f"- Duplicate rate: `{report.get('duplicate_rate')}`",
        f"- Watermark status: `{report.get('watermark_status')}`",
        f"- Watermark seen records: `{report.get('watermark_seen_record_count')}`",
        f"- New unique records after watermark: `{report.get('new_unique_records_count')}`",
        f"- Already seen records: `{report.get('already_seen_record_count')}`",
        "",
        "## Conclusao",
        "",
        "O recheck demonstra se as linhas brutas acumuladas representam evidencia nova ou apenas reobservacoes.",
        "Este relatorio nao autoriza treino, promocao, runtime, registry ativo, Freqtrade, RiskManager, sinais ou ordens.",
        "",
    ]
    return "\n".join(lines)


def validate_write_request(root: Path, paths: RecheckPaths, write_report: bool) -> list[str]:
    if not write_report:
        return []
    errors: list[str] = []
    errors.extend(validate_path_under(root, paths.output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    errors.extend(validate_path_under(root, paths.output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    return sorted_unique(errors)


def validate_path_under(root: Path, path: Path, allowed: Path, reason: str) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed).resolve())
    except ValueError:
        return [reason]
    return []


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


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
        "promotes_model": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "runtime_allowed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_operational_parquet": False,
        "writes_active_registry": False,
        "writes_quarantine_registry": False,
        "registry_write_performed": False,
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
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
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
