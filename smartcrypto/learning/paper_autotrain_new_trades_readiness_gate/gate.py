"""Research-only readiness gate for new closed paper trades after watermark.

The gate answers one narrow question: whether current paper research sources
contain closed trade records that are not yet present in the incremental
watermark. It never creates microbatches, trains, evaluates candidates, writes
runtime state, or changes any operational component.
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

SCHEMA_VERSION = "paper_autotrain_new_trades_readiness_gate_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_new_trades_readiness_gate_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_new_trades_readiness_gate_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

WATERMARK_FIX_REPORT = Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.json")
ACCUMULATION_RECHECK_REPORT = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json")
FRESHNESS_REPORT = Path("data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json")
ACTIVATION_REPORT = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
CANDIDATE_EVALUATION_REPORT = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json")
FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")

DECISION_WAIT_NEW_TRADES = "AGUARDAR_NOVOS_TRADES_PAPER"
DECISION_BOOTSTRAP_WATERMARK = "RODAR_BOOTSTRAP_WATERMARK_RESEARCH_ONLY"
DECISION_WAIT_MICROBATCHES = "AGUARDAR_FONTES_PAPER_FECHADAS"
DECISION_FIX_WATERMARK = "CORRIGIR_WATERMARK_CORROMPIDO"
DECISION_RECHECK_MANUAL_ALLOWED = "NOVOS_TRADES_PAPER_DETECTADOS_RECHECK_MANUAL_PERMITIDO"


@dataclass(frozen=True)
class GatePaths:
    quarantine_dir: Path
    watermark_path: Path
    feedback_events_path: Path
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_new_trades_readiness_gate_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_ready: bool = False,
    fail_on_missing_watermark: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the new-trades readiness gate report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path)
    output_paths = {"json": str(paths.output_json), "markdown": str(paths.output_markdown)}
    write_errors = validate_write_request(root, paths, write_report)
    optional_sources = load_optional_sources(root)
    sources = load_existing_microbatches(paths.quarantine_dir)
    feedback = load_feedback_events(paths.feedback_events_path)

    warnings = [*sources["warnings"], *optional_sources["warnings"], *feedback["warnings"]]
    blockers = [*write_errors]
    source_frame = sources["frame"]
    watermark_read = read_watermark_state(paths.watermark_path)
    seen_keys = set(load_seen_keys(watermark_read.state))

    if source_frame.empty:
        blockers.append("missing_closed_paper_trade_sources")
        report = build_base_report(
            root=root,
            generated_at=generated_at,
            status="blocked",
            reason="missing_closed_paper_trade_sources",
            decision=DECISION_WAIT_MICROBATCHES,
            paths=paths,
            output_paths=output_paths,
            write_report=write_report,
            blockers=sorted_unique([*blockers, *watermark_read.blockers]),
            warnings=sorted_unique([*warnings, *watermark_read.warnings]),
            optional_sources=optional_sources,
            feedback=feedback,
            fail_on_ready=fail_on_ready,
            fail_on_missing_watermark=fail_on_missing_watermark,
        )
        return maybe_write_report(report, paths, write_report, write_errors)

    normalized = normalize_records(source_frame)
    source_summary = summarize_normalized_records(normalized)
    unique_keys = set(str(key) for key in source_summary["unique_record_keys"])
    already_seen_keys = sorted(unique_keys.intersection(seen_keys))
    new_keys = sorted(unique_keys.difference(seen_keys))
    feedback_new_count = count_feedback_new_candidates(feedback["records"], seen_keys)

    status, reason, decision, status_blockers, status_warnings = decide_status(
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        new_unique_record_count=len(new_keys),
        write_errors=write_errors,
        fail_on_ready=fail_on_ready,
        fail_on_missing_watermark=fail_on_missing_watermark,
    )
    blockers.extend(status_blockers)
    blockers.extend(watermark_read.blockers)
    warnings.extend(status_warnings)
    warnings.extend(watermark_read.warnings)

    source_row_count = int(len(source_frame))
    source_unique_count = int(source_summary["unique_record_count"])
    duplicate_count = int(source_summary["duplicate_record_count"])
    duplicate_rate = round(duplicate_count / source_row_count, 10) if source_row_count else 0.0
    ready_for_recheck = status == "ok" and bool(new_keys)
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": bool(watermark_read.exists),
        "watermark_status": watermark_read.status,
        "watermark_seen_record_count": len(seen_keys),
        "watermark_seen_record_keys_sha256": (watermark_read.state or {}).get("seen_record_keys_sha256"),
        "watermark_close_time_utc": (watermark_read.state or {}).get("watermark_close_time_utc"),
        "watermark_order_id": (watermark_read.state or {}).get("watermark_order_id"),
        "watermark_record_hash": (watermark_read.state or {}).get("watermark_record_hash"),
        "source_file_count": int(sources["source_file_count"]),
        "source_row_count": source_row_count,
        "source_unique_record_count": source_unique_count,
        "duplicate_record_count": duplicate_count,
        "duplicate_rate": duplicate_rate,
        "feedback_event_count": int(feedback["event_count"]),
        "feedback_new_candidate_count": feedback_new_count,
        "new_closed_trade_record_count": len(new_keys),
        "new_unique_record_count": len(new_keys),
        "already_seen_record_count": len(already_seen_keys),
        "new_record_key_sample": new_keys[:25],
        "already_seen_record_key_sample": already_seen_keys[:25],
        "ready_for_accumulation_recheck": ready_for_recheck,
        "ready_for_candidate_evaluation_recheck": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "would_create_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "record_key_strategy_counts": source_summary["record_key_strategy_counts"],
        "optional_source_status": optional_sources["source_status"],
        "feedback_source_summary": feedback["summary"],
        "fail_on_ready": bool(fail_on_ready),
        "fail_on_missing_watermark": bool(fail_on_missing_watermark),
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def build_paths(root: Path, output_json_path: str | Path | None, output_markdown_path: str | Path | None) -> GatePaths:
    return GatePaths(
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        watermark_path=root / DEFAULT_WATERMARK_PATH,
        feedback_events_path=root / FEEDBACK_EVENTS,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def decide_status(
    *,
    watermark_exists: bool,
    watermark_status: str,
    new_unique_record_count: int,
    write_errors: Sequence[str],
    fail_on_ready: bool,
    fail_on_missing_watermark: bool,
) -> tuple[str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_FIX_WATERMARK, list(write_errors), []
    if not watermark_exists:
        blockers = ["missing_watermark_state"]
        if fail_on_missing_watermark:
            blockers.append("fail_on_missing_watermark_triggered")
        return "blocked", "missing_watermark_state", DECISION_BOOTSTRAP_WATERMARK, blockers, []
    if watermark_status == "invalid":
        return "blocked", "watermark_state_invalid", DECISION_FIX_WATERMARK, ["watermark_state_invalid"], []
    if new_unique_record_count == 0:
        return (
            "blocked",
            "no_new_closed_paper_trades_after_watermark",
            DECISION_WAIT_NEW_TRADES,
            ["no_new_closed_paper_trades_after_watermark"],
            [],
        )
    if fail_on_ready:
        return (
            "blocked",
            "new_closed_paper_trades_after_watermark_detected",
            DECISION_RECHECK_MANUAL_ALLOWED,
            ["fail_on_ready_triggered"],
            [],
        )
    return "ok", "new_closed_paper_trades_after_watermark_detected", DECISION_RECHECK_MANUAL_ALLOWED, [], []


def build_base_report(
    *,
    root: Path,
    generated_at: str,
    status: str,
    reason: str,
    decision: str,
    paths: GatePaths,
    output_paths: Mapping[str, str],
    write_report: bool,
    blockers: Sequence[str],
    warnings: Sequence[str],
    optional_sources: Mapping[str, Any],
    feedback: Mapping[str, Any],
    fail_on_ready: bool,
    fail_on_missing_watermark: bool,
) -> dict[str, Any]:
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": False,
        "watermark_status": "missing",
        "watermark_seen_record_count": 0,
        "watermark_seen_record_keys_sha256": None,
        "watermark_close_time_utc": None,
        "watermark_order_id": None,
        "watermark_record_hash": None,
        "source_file_count": 0,
        "source_row_count": 0,
        "source_unique_record_count": 0,
        "duplicate_record_count": 0,
        "duplicate_rate": 0.0,
        "feedback_event_count": int(feedback.get("event_count", 0)),
        "feedback_new_candidate_count": 0,
        "new_closed_trade_record_count": 0,
        "new_unique_record_count": 0,
        "already_seen_record_count": 0,
        "new_record_key_sample": [],
        "already_seen_record_key_sample": [],
        "ready_for_accumulation_recheck": False,
        "ready_for_candidate_evaluation_recheck": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "would_create_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "record_key_strategy_counts": {},
        "optional_source_status": optional_sources["source_status"],
        "feedback_source_summary": feedback["summary"],
        "fail_on_ready": bool(fail_on_ready),
        "fail_on_missing_watermark": bool(fail_on_missing_watermark),
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
        "accumulation_recheck_report": ACCUMULATION_RECHECK_REPORT,
        "freshness_report": FRESHNESS_REPORT,
        "activation_report": ACTIVATION_REPORT,
        "candidate_evaluation_report": CANDIDATE_EVALUATION_REPORT,
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
    return {"source_status": source_status, "payloads": payloads, "warnings": sorted_unique(warnings)}


def load_feedback_events(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "records": [],
            "event_count": 0,
            "summary": {"path": str(path), "status": "missing_optional", "event_count": 0},
            "warnings": ["optional_source_missing:feedback_events"],
        }
    records: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return {
            "records": [],
            "event_count": 0,
            "summary": {"path": str(path), "status": "invalid_optional", "event_count": 0, "error": exc.__class__.__name__},
            "warnings": [f"optional_source_invalid:feedback_events:{exc.__class__.__name__}"],
        }
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"feedback_event_invalid_json:line_{index}")
            continue
        if isinstance(payload, Mapping):
            records.append(payload)
    return {
        "records": records,
        "event_count": len(records),
        "summary": {"path": str(path), "status": "ok", "event_count": len(records)},
        "warnings": sorted_unique(warnings),
    }


def count_feedback_new_candidates(records: Sequence[Mapping[str, Any]], seen_keys: set[str]) -> int:
    if not records:
        return 0
    frame = pd.DataFrame([dict(record) for record in records])
    if frame.empty:
        return 0
    normalized = normalize_records(frame)
    summary = summarize_normalized_records(normalized)
    unique_keys = set(str(key) for key in summary["unique_record_keys"])
    return len(unique_keys.difference(seen_keys))


def maybe_write_report(
    report: dict[str, Any],
    paths: GatePaths,
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
            "# Paper Autotrain New Trades Readiness Gate V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Watermark status: `{report.get('watermark_status')}`",
            f"- Watermark seen records: `{report.get('watermark_seen_record_count')}`",
            f"- Source rows: `{report.get('source_row_count')}`",
            f"- Source unique records: `{report.get('source_unique_record_count')}`",
            f"- New records: `{report.get('new_unique_record_count')}`",
            f"- Ready for accumulation recheck: `{report.get('ready_for_accumulation_recheck')}`",
            "",
            "## Conclusao",
            "",
            "O gate permanece aguardando novos trades paper quando `new_unique_record_count=0`.",
            "Se novos registros aparecerem, ele permite apenas recheck manual de acumulacao, sem criar microbatch,",
            "sem treinar, sem avaliar candidato, sem promocao e sem autoridade operacional.",
            "",
        ]
    )


def validate_write_request(root: Path, paths: GatePaths, write_report: bool) -> list[str]:
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
