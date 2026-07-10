"""Deterministic, plan-only remediation for paper autotrain feedback gaps.

The planner consumes the read-only diagnostics report produced by
``paper_autotrain_feedback_gap_diagnostics_v1``. It classifies records and
describes a possible future backfill, but never creates feedback events,
microbatches, model artifacts, registry entries, runtime state, or orders.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "paper_autotrain_feedback_gap_remediation_plan_v1"
SOURCE_SCHEMA_VERSION = "paper_autotrain_feedback_gap_diagnostics_v1"

DEFAULT_DIAGNOSTICS_REPORT = Path("data/reports/paper_autotrain_feedback_gap_diagnostics_v1.json")
DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

FRESH_PAPER_DB_STATUSES = frozenset(
    {
        "snapshot_db_fresh_against_csv",
        "runtime_db_fresh_against_csv",
    }
)

PLAN_ONLY = "PLAN_ONLY_NO_BACKFILL"
BLOCKED_CONFLICTS = "BLOCKED_CONFLICTS_REQUIRE_RECONCILIATION"
BLOCKED_VALIDATION = "BLOCKED_VALIDATION_REJECTION_REQUIRES_REVIEW"
BLOCKED_SOURCE = "BLOCKED_SOURCE_NOT_FRESH"
PLANNED_ACTION = "WOULD_CREATE_FEEDBACK_EVENT_IN_FUTURE_BRANCH"
NO_ACTION = "NO_FEEDBACK_EVENT_PLANNED"


def build_paper_autotrain_feedback_gap_remediation_plan_v1(
    *,
    project_root: str | Path,
    diagnostics_report_path: str | Path | None = None,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Load diagnostics and build a deterministic, non-executing plan."""

    root = Path(project_root).resolve()
    diagnostics_path = _resolve(root, diagnostics_report_path, DEFAULT_DIAGNOSTICS_REPORT)
    output_json = _resolve(root, output_json_path, DEFAULT_OUTPUT_JSON)
    output_markdown = _resolve(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN)
    write_errors = _validate_write_request(root, output_json, output_markdown, write_report)

    diagnostics, load_error, input_report_hash = _load_diagnostics(diagnostics_path)
    report = build_remediation_plan_from_diagnostics(
        diagnostics,
        input_report_hash=input_report_hash,
        input_report_path=_display_path(root, diagnostics_path),
        output_paths={
            "json": _display_path(root, output_json),
            "markdown": _display_path(root, output_markdown),
        },
        generated_at_utc=generated_at_utc,
        source_load_error=load_error,
        write_report_requested=write_report,
        write_validation_errors=write_errors,
    )

    if write_report and not write_errors:
        final_report = dict(report)
        final_report["write_performed"] = True
        final_report["safety_flags"] = safety_flags()
        _atomic_write_text(
            output_json,
            json.dumps(final_report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        )
        _atomic_write_text(output_markdown, render_markdown(final_report))
        report = final_report

    return report


def build_remediation_plan_from_diagnostics(
    diagnostics: Mapping[str, Any] | None,
    *,
    input_report_hash: str | None,
    input_report_path: str,
    output_paths: Mapping[str, str],
    generated_at_utc: str | None = None,
    source_load_error: str | None = None,
    write_report_requested: bool = False,
    write_validation_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a plan from an already-loaded diagnostics payload.

    This function is pure apart from reading the clock when
    ``generated_at_utc`` is omitted. Plan identity and hashes deliberately
    exclude that timestamp and all write-request fields.
    """

    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    source = dict(diagnostics or {})
    missing_records = _mapping_list(source.get("missing_in_feedback_records"))
    declared_missing_count = _as_non_negative_int(source.get("missing_in_feedback_count"))
    conflicting_group_count = _as_non_negative_int(source.get("conflicting_group_count"))
    feedback_count = _as_non_negative_int(source.get("feedback_events_normalized_record_count"))
    validation_summary = source.get("validation_rejection_status")
    validation_summary = validation_summary if isinstance(validation_summary, Mapping) else {}
    declared_validation_rejected_count = _as_non_negative_int(validation_summary.get("rejected_count"))

    contract_errors = _source_contract_errors(
        source,
        source_load_error=source_load_error,
        declared_missing_count=declared_missing_count,
        actual_missing_count=len(missing_records),
    )
    paper_db_status = str(source.get("paper_db_authority_status") or "unknown")
    source_is_fresh = not contract_errors and paper_db_status in FRESH_PAPER_DB_STATUSES

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    validation_rejected_keys: set[str] = set()

    for raw_record in sorted(missing_records, key=_record_sort_key):
        validation_status = raw_record.get("validation_status")
        validation_status = dict(validation_status) if isinstance(validation_status, Mapping) else {}
        would_pass = validation_status.get("would_pass_both_stages") is True
        db_csv_match = str(raw_record.get("db_csv_match_status") or "unknown")

        blocked_reasons: list[str] = []
        if str(raw_record.get("classification") or "") != "missing_in_feedback":
            blocked_reasons.append("classification_not_missing_in_feedback")
        if not would_pass:
            blocked_reasons.append("validation_would_not_pass_both_stages")
            validation_rejected_keys.add(str(raw_record.get("dedup_key") or ""))
        if db_csv_match != "match":
            blocked_reasons.append("paper_db_closed_trades_csv_not_matched")
        if conflicting_group_count:
            blocked_reasons.append("source_conflicts_require_reconciliation")
        if not source_is_fresh:
            blocked_reasons.append("source_not_fresh")

        planned = _planned_record(raw_record, blocked_reasons)
        if blocked_reasons:
            blocked.append(planned)
        else:
            eligible.append(planned)

    validation_rejected_count = max(declared_validation_rejected_count, len(validation_rejected_keys))
    status, reason, decision = _decide(
        source_is_fresh=source_is_fresh,
        conflicting_group_count=conflicting_group_count,
        validation_rejected_count=validation_rejected_count,
        write_validation_errors=write_validation_errors,
    )

    canonical_plan = {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": source.get("schema_version"),
        "input_report_hash": input_report_hash,
        "decision": decision,
        "paper_db_authority_status": paper_db_status,
        "missing_in_feedback_count": declared_missing_count,
        "planned_feedback_event_count": len(eligible),
        "blocked_feedback_event_count": len(blocked),
        "conflicting_group_count": conflicting_group_count,
        "validation_rejected_count": validation_rejected_count,
        "eligible_missing_records": eligible,
        "blocked_missing_records": blocked,
    }
    plan_hash = _canonical_sha256(canonical_plan)
    plan_id = f"feedback-gap-plan-{plan_hash[:16]}"
    safety = safety_flags()

    blockers = sorted(set(contract_errors) | set(write_validation_errors))
    if not source_is_fresh:
        blockers.append("source_not_fresh")
    if conflicting_group_count:
        blockers.append("source_conflicts_require_reconciliation")
    if validation_rejected_count:
        blockers.append("validation_rejections_require_review")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": status,
        "reason": reason,
        "decision": decision,
        "research_only": True,
        "read_only": True,
        "source_diagnostics_status": source.get("status") if source else "missing",
        "source_diagnostics_reason": source.get("reason") if source else source_load_error,
        "source_diagnostics_schema_version": source.get("schema_version") if source else None,
        "paper_db_authority_status": paper_db_status,
        "input_report_path": input_report_path,
        "input_report_hash": input_report_hash,
        "closed_trades_csv_normalized_record_count": _as_non_negative_int(
            source.get("closed_trades_csv_normalized_record_count")
        ),
        "feedback_events_normalized_record_count": feedback_count,
        "paper_db_normalized_record_count": _as_non_negative_int(
            source.get("paper_db_normalized_record_count")
        ),
        "missing_in_feedback_count": declared_missing_count,
        "planned_feedback_event_count": len(eligible),
        "blocked_feedback_event_count": len(blocked),
        "conflicting_group_count": conflicting_group_count,
        "validation_rejected_count": validation_rejected_count,
        "eligible_missing_records": eligible,
        "blocked_missing_records": blocked,
        "conflicting_records": _mapping_list(source.get("conflicting_records")),
        "conflicting_records_included": isinstance(source.get("conflicting_records"), list),
        "already_present_feedback_record_count": feedback_count,
        "already_present_feedback_records": _mapping_list(source.get("already_present_feedback_records")),
        "already_present_feedback_records_included": isinstance(
            source.get("already_present_feedback_records"), list
        ),
        "record_partition_counts": {
            "eligible_missing": len(eligible),
            "blocked_missing": len(blocked),
            "conflicting": conflicting_group_count,
            "already_present_in_feedback": feedback_count,
        },
        "plan_hash": plan_hash,
        "plan_id": plan_id,
        "blockers": sorted(set(blockers)),
        "warnings": _source_warnings(source, source_load_error),
        "output_paths": dict(output_paths),
        "write_report_requested": bool(write_report_requested),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }
    return report


def _planned_record(raw: Mapping[str, Any], blocked_reasons: Sequence[str]) -> dict[str, Any]:
    identity = {
        "dedup_key": raw.get("dedup_key"),
        "native_key": raw.get("native_key"),
        "closed_trades_csv_order_id": raw.get("closed_trades_csv_order_id"),
        "paper_db_trade_id": raw.get("paper_db_trade_id"),
        "close_time_utc": raw.get("close_time_utc"),
        "source_keys": _normalized_source_keys(raw.get("source_keys")),
    }
    return {
        "dedup_key": raw.get("dedup_key"),
        "native_key": raw.get("native_key"),
        "closed_trades_csv_order_id": raw.get("closed_trades_csv_order_id"),
        "paper_db_trade_id": raw.get("paper_db_trade_id"),
        "symbol": raw.get("symbol"),
        "side": raw.get("side"),
        "open_time_utc": raw.get("open_time_utc"),
        "close_time_utc": raw.get("close_time_utc"),
        "net_pnl": raw.get("net_pnl"),
        "profit_ratio": raw.get("profit_ratio"),
        "source_presence": sorted(str(value) for value in (raw.get("source_presence") or [])),
        "source_keys": _normalized_source_keys(raw.get("source_keys")),
        "validation_status": dict(raw.get("validation_status") or {}),
        "planned_action": NO_ACTION if blocked_reasons else PLANNED_ACTION,
        "blocked_reason": ";".join(sorted(set(blocked_reasons))) if blocked_reasons else None,
        "idempotency_key": f"feedback-gap:{_canonical_sha256(identity)}",
    }


def _source_contract_errors(
    source: Mapping[str, Any],
    *,
    source_load_error: str | None,
    declared_missing_count: int,
    actual_missing_count: int,
) -> list[str]:
    errors: list[str] = []
    if source_load_error:
        errors.append(source_load_error)
    if not source:
        errors.append("missing_source_diagnostics")
        return sorted(set(errors))
    if source.get("schema_version") != SOURCE_SCHEMA_VERSION:
        errors.append("unexpected_source_diagnostics_schema")
    if source.get("status") != "ok":
        errors.append("source_diagnostics_not_ok")
    if declared_missing_count != actual_missing_count:
        errors.append("missing_record_count_mismatch")
    return sorted(set(errors))


def _decide(
    *,
    source_is_fresh: bool,
    conflicting_group_count: int,
    validation_rejected_count: int,
    write_validation_errors: Sequence[str],
) -> tuple[str, str, str]:
    if write_validation_errors or not source_is_fresh:
        return "blocked", "source_not_fresh_or_contract_invalid", BLOCKED_SOURCE
    if conflicting_group_count:
        return "blocked", "conflicts_require_reconciliation", BLOCKED_CONFLICTS
    if validation_rejected_count:
        return "blocked", "validation_rejection_requires_review", BLOCKED_VALIDATION
    return "ok", "remediation_plan_built_without_backfill", PLAN_ONLY


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise operator-readable plan without operational authority."""

    lines = [
        "# Paper Autotrain Feedback Gap Remediation Plan V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Source diagnostics: `{report.get('source_diagnostics_status')}`",
        f"- Paper DB authority: `{report.get('paper_db_authority_status')}`",
        f"- Missing in feedback: `{report.get('missing_in_feedback_count')}`",
        f"- Planned future events: `{report.get('planned_feedback_event_count')}`",
        f"- Blocked events: `{report.get('blocked_feedback_event_count')}`",
        f"- Conflicts: `{report.get('conflicting_group_count')}`",
        f"- Validation rejected: `{report.get('validation_rejected_count')}`",
        f"- Plan ID: `{report.get('plan_id')}`",
        f"- Plan hash: `{report.get('plan_hash')}`",
        "",
        "## Boundary",
        "",
        "This report is plan-only. It does not backfill feedback, create a microbatch, train, promote, or change runtime state.",
        "",
        "## Eligible Missing Records",
        "",
        "| dedup_key | symbol | side | close_time_utc | planned_action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report.get("eligible_missing_records") or []:
        lines.append(
            "| {dedup} | {symbol} | {side} | {close_time} | {action} |".format(
                dedup=_markdown_cell(row.get("dedup_key")),
                symbol=_markdown_cell(row.get("symbol")),
                side=_markdown_cell(row.get("side")),
                close_time=_markdown_cell(row.get("close_time_utc")),
                action=_markdown_cell(row.get("planned_action")),
            )
        )
    if not report.get("eligible_missing_records"):
        lines.append("| none | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Blocked Missing Records",
            "",
            "| dedup_key | blocked_reason |",
            "| --- | --- |",
        ]
    )
    for row in report.get("blocked_missing_records") or []:
        lines.append(
            f"| {_markdown_cell(row.get('dedup_key'))} | {_markdown_cell(row.get('blocked_reason'))} |"
        )
    if not report.get("blocked_missing_records"):
        lines.append("| none | - |")
    return "\n".join(lines) + "\n"


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "writes_feedback": False,
        "writes_microbatch": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_models": False,
        "writes_registries": False,
        "alters_watermark": False,
        "runs_training": False,
        "would_create_microbatch": False,
        "would_run_training": False,
        "would_promote_model": False,
        "backfill_performed": False,
    }


def _load_diagnostics(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing_source_diagnostics", None
    except OSError:
        return None, "unreadable_source_diagnostics", None
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_source_diagnostics_json", hashlib.sha256(raw).hexdigest()
    if not isinstance(payload, dict):
        return None, "invalid_source_diagnostics_payload", hashlib.sha256(raw).hexdigest()
    return payload, None, hashlib.sha256(raw).hexdigest()


def _validate_write_request(root: Path, output_json: Path, output_markdown: Path, write: bool) -> list[str]:
    if not write:
        return []
    allowed = (root / ALLOWED_REPORT_ROOT).resolve()
    errors: list[str] = []
    if not _is_under(output_json, allowed) or output_json.suffix.lower() != ".json":
        errors.append("output_json_must_be_json_under_data_reports")
    if not _is_under(output_markdown, allowed) or output_markdown.suffix.lower() != ".md":
        errors.append("output_markdown_must_be_markdown_under_data_reports")
    return errors


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    candidate = Path(value) if value is not None else default
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _normalized_source_keys(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(source): sorted(str(key) for key in keys)
        for source, keys in sorted(value.items(), key=lambda item: str(item[0]))
        if isinstance(keys, (list, tuple, set))
    }


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("dedup_key") or ""),
        str(record.get("close_time_utc") or ""),
        str(record.get("closed_trades_csv_order_id") or ""),
    )


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _source_warnings(source: Mapping[str, Any], source_load_error: str | None) -> list[str]:
    warnings = [str(value) for value in (source.get("warnings") or [])]
    if source_load_error:
        warnings.append(source_load_error)
    if not isinstance(source.get("conflicting_records"), list):
        warnings.append("source_diagnostics_exposes_conflicting_count_only")
    if not isinstance(source.get("already_present_feedback_records"), list):
        warnings.append("source_diagnostics_exposes_feedback_present_count_only")
    return sorted(set(warnings))


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
