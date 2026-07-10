"""Orchestrator for controlled paper-feedback backfill and read-only closeout."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autotrain_feedback_gap_backfill_dryrun import build_dryrun_from_plan
from smartcrypto.learning.paper_autotrain_feedback_gap_diagnostics import (
    build_paper_autotrain_feedback_gap_diagnostics_v1,
)
from smartcrypto.learning.paper_autotrain_feedback_gap_remediation_plan import (
    build_remediation_plan_from_diagnostics,
)

from .contracts import (
    ALLOWED_REPORT_ROOT,
    DEFAULT_BACKUP_DIR,
    DEFAULT_FEEDBACK_EVENTS,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
    Authorization,
    CloseoutPaths,
    SCHEMA_VERSION,
    authorization_errors,
    is_under,
    report_safety,
    resolve_under_root,
    sanitized_report,
)
from .controlled_backfill import (
    BackfillRequest,
    canonical_sha256,
    execute_controlled_backfill,
    load_jsonl,
    source_fingerprint,
)
from .readiness import evaluate_autotrain_readiness


def run_paper_feedback_autotrain_e2e_closeout_v1(
    *,
    project_root: str | Path,
    execute_backfill: bool = False,
    expected_plan_hash: str | None = None,
    expected_dryrun_hash: str | None = None,
    authorization_reference: str | None = None,
    confirmation_text: str | None = None,
    allow_paper_db_read: bool = False,
    paper_db_path: str | Path | None = None,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    diagnostics_override: Mapping[str, Any] | None = None,
    readiness_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    post_write_validator: Callable[[Sequence[Mapping[str, Any]]], bool] | None = None,
    source_change_hook: Callable[[], None] | None = None,
    replace_function: Callable[..., None] = os.replace,
    rollback_replace_function: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Run all phases; mutation requires the complete explicit authorization tuple."""

    root = Path(project_root).resolve()
    try:
        paths = build_paths(
            root,
            paper_db_path=paper_db_path,
            output_json_path=output_json_path,
            output_markdown_path=output_markdown_path,
        )
    except ValueError:
        return base_report("blocked", "unsafe_path", "MANTER_EM_RESEARCH", write_report)

    discovery_errors = validate_discovery(paths)
    existing_events, feedback_errors = load_jsonl(paths.feedback_events)
    diagnostics = (
        dict(diagnostics_override)
        if diagnostics_override is not None
        else build_paper_autotrain_feedback_gap_diagnostics_v1(
            project_root=root,
            paper_db_path=paths.paper_db,
            allow_paper_db_read=allow_paper_db_read,
            write_report=False,
        )
    )
    diagnostics_hash = canonical_sha256(diagnostics)
    plan = build_remediation_plan_from_diagnostics(
        diagnostics,
        input_report_hash=diagnostics_hash,
        input_report_path="in_memory_recomputed_diagnostics",
        output_paths={},
    )
    plan_hash = str(plan.get("plan_hash") or "")
    dryrun = build_dryrun_from_plan(
        plan,
        existing_feedback_events=existing_events,
        expected_plan_hash=plan_hash,
        input_plan_hash=canonical_sha256(plan),
        input_plan_path="in_memory_recomputed_plan",
        feedback_events_path=paths.feedback_events.relative_to(root).as_posix(),
        output_paths={},
    )
    dryrun_hash = str(dryrun.get("dryrun_hash") or "")
    external_sources = tuple(
        path
        for path in (root / "data/trades/inbox/freqtrade_paper_closed_trades.csv", paths.paper_db)
        if path is not None
    )
    try:
        fingerprint = source_fingerprint(external_sources)
    except ValueError as exc:
        discovery_errors.append(str(exc))
        fingerprint = canonical_sha256([])

    candidates = tuple(
        dict(event)
        for event in dryrun.get("simulated_feedback_events") or []
        if isinstance(event, Mapping)
    )
    report = {
        **base_report("blocked", "explicit_backfill_authorization_required", "NO_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION", write_report),
        "plan_hash": plan_hash or None,
        "dryrun_hash": dryrun_hash or None,
        "source_fingerprint_hash": fingerprint,
        "pre_write_feedback_count": len(existing_events),
        "planned_event_count": len(candidates),
        "schema_error_count": int(dryrun.get("schema_validation_error_count", 0) or 0),
        "duplicate_count": int(dryrun.get("duplicate_simulated_event_count", 0) or 0),
        "conflict_count": int(diagnostics.get("conflicting_group_count", 0) or 0),
        "blockers": sorted(set([*discovery_errors, *feedback_errors])),
    }
    authorization = Authorization(
        execute_backfill=execute_backfill,
        expected_plan_hash=expected_plan_hash,
        expected_dryrun_hash=expected_dryrun_hash,
        authorization_reference=authorization_reference,
        confirmation_text=confirmation_text,
    )
    candidate_identities = {str(event.get("event_hash") or "").casefold() for event in candidates}
    existing_identities = {str(event.get("event_hash") or "").casefold() for event in existing_events}
    idempotent_authorization = Authorization(
        execute_backfill=execute_backfill,
        expected_plan_hash=expected_plan_hash,
        expected_dryrun_hash=dryrun_hash if expected_dryrun_hash else None,
        authorization_reference=authorization_reference,
        confirmation_text=confirmation_text,
    )
    idempotent_auth_errors = authorization_errors(
        idempotent_authorization,
        plan_hash=plan_hash,
        dryrun_hash=dryrun_hash,
    )
    if candidates and candidate_identities.issubset(existing_identities) and not idempotent_auth_errors:
        continuity = dict(diagnostics)
        continuity["missing_in_feedback_count"] = 0
        continuity["conflicting_group_count"] = 0
        readiness = evaluate_autotrain_readiness(
            project_root=root,
            continuity_report=continuity,
            report_overrides=readiness_overrides,
        )
        report.update(
            {
                "status": "ok",
                "reason": "authorized_backfill_already_applied",
                "decision": "BACKFILL_ALREADY_APPLIED",
                "authorization_reference": authorization_reference,
                "already_existing_count": len(candidates),
                "missing_after_count": 0,
                "already_applied": True,
            }
        )
        report.update(readiness)
        report["decision"] = "BACKFILL_ALREADY_APPLIED"
        report.update(report_safety(read_only=True))
        report["safety_flags"] = report_safety(read_only=True)
        return maybe_write_report(sanitized_report(report), paths, write_report)
    auth_errors = authorization_errors(authorization, plan_hash=plan_hash, dryrun_hash=dryrun_hash)
    required_missing = {
        "execute_backfill_not_requested",
        "expected_plan_hash_required",
        "expected_dryrun_hash_required",
        "authorization_reference_required",
        "confirmation_text_mismatch",
    }
    if auth_errors:
        report["blockers"] = sorted(set([*report["blockers"], *auth_errors]))
        if not required_missing.intersection(auth_errors):
            report["reason"] = auth_errors[0]
        return maybe_write_report(sanitized_report(report), paths, write_report)

    report["authorization_reference"] = authorization_reference
    if discovery_errors or feedback_errors:
        report["reason"] = "discovery_validation_failed"
        return maybe_write_report(sanitized_report(report), paths, write_report)
    if int(diagnostics.get("unexpected_writer_count", 0) or 0) > 0:
        report["reason"] = "unexpected_feedback_writer_detected"
        report["blockers"] = sorted(set([*report["blockers"], "unexpected_feedback_writer_detected"]))
        return maybe_write_report(sanitized_report(report), paths, write_report)
    dryrun_acceptable = (
        int(dryrun.get("schema_validation_error_count", 0) or 0) == 0
        and int(dryrun.get("duplicate_simulated_event_count", 0) or 0) == 0
        and not plan.get("blocked_feedback_event_count")
    )
    if plan.get("status") != "ok" or not dryrun_acceptable:
        report["reason"] = "recomputation_not_ready"
        report["blockers"] = sorted(set([*report["blockers"], "recomputation_not_ready"]))
        return maybe_write_report(sanitized_report(report), paths, write_report)

    operation_id = canonical_sha256(
        {
            "plan_hash": plan_hash,
            "dryrun_hash": dryrun_hash,
            "source_fingerprint_hash": fingerprint,
            "authorization_reference": authorization_reference,
        }
    )[:20]
    transaction = execute_controlled_backfill(
        BackfillRequest(
            feedback_path=paths.feedback_events,
            backup_dir=paths.backup_dir,
            lock_path=paths.feedback_events.with_suffix(paths.feedback_events.suffix + ".lock"),
            operation_id=operation_id,
            authorization_reference=str(authorization_reference),
            candidate_events=candidates,
            external_source_paths=external_sources,
            source_fingerprint_hash=fingerprint,
        ),
        replace_function=replace_function,
        rollback_replace_function=rollback_replace_function,
        post_write_validator=post_write_validator,
        source_change_hook=source_change_hook,
    )
    report.update(transaction)
    report["plan_hash"] = plan_hash
    report["dryrun_hash"] = dryrun_hash
    report["source_fingerprint_hash"] = fingerprint

    if transaction.get("status") == "ok":
        post_diagnostics = (
            dict(diagnostics_override)
            if diagnostics_override is not None
            else build_paper_autotrain_feedback_gap_diagnostics_v1(
                project_root=root,
                paper_db_path=paths.paper_db,
                allow_paper_db_read=allow_paper_db_read,
                write_report=False,
            )
        )
        if diagnostics_override is not None:
            post_diagnostics["missing_in_feedback_count"] = 0
            post_diagnostics["conflicting_group_count"] = 0
        readiness = evaluate_autotrain_readiness(
            project_root=root,
            continuity_report=post_diagnostics,
            report_overrides=readiness_overrides,
        )
        report.update(readiness)
        report["decision"] = readiness["final_readiness_decision"]
        report["status"] = "ok" if report["decision"] == "READY_FOR_PAPER_OBSERVATION" else "blocked"
        report["reason"] = (
            "paper_autotrain_ready_for_observation"
            if report["status"] == "ok"
            else "institutional_readiness_gates_blocked"
        )
    report.update(report_safety(read_only=not bool(report.get("write_performed"))))
    report["safety_flags"] = report_safety(read_only=not bool(report.get("write_performed")))
    return maybe_write_report(sanitized_report(report), paths, write_report)


def build_paths(
    root: Path,
    *,
    paper_db_path: str | Path | None,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
) -> CloseoutPaths:
    return CloseoutPaths(
        project_root=root,
        feedback_events=resolve_under_root(root, None, DEFAULT_FEEDBACK_EVENTS),
        backup_dir=resolve_under_root(root, None, DEFAULT_BACKUP_DIR),
        report_json=resolve_under_root(root, output_json_path, DEFAULT_REPORT_JSON),
        report_markdown=resolve_under_root(root, output_markdown_path, DEFAULT_REPORT_MARKDOWN),
        paper_db=resolve_under_root(root, paper_db_path, Path(".")) if paper_db_path else None,
    )


def validate_discovery(paths: CloseoutPaths) -> list[str]:
    errors: list[str] = []
    if paths.feedback_events.is_symlink():
        errors.append("feedback_store_symlink_forbidden")
    if paths.feedback_events.suffix.lower() != ".jsonl":
        errors.append("feedback_store_extension_invalid")
    if not paths.feedback_events.is_file():
        errors.append("feedback_store_missing")
    if paths.paper_db is not None:
        if paths.paper_db.is_symlink():
            errors.append("paper_db_symlink_forbidden")
        if paths.paper_db.suffix.lower() not in {".sqlite", ".db"}:
            errors.append("paper_db_extension_invalid")
    allowed_reports = paths.project_root / ALLOWED_REPORT_ROOT
    if not is_under(paths.report_json, allowed_reports) or paths.report_json.suffix.lower() != ".json":
        errors.append("output_json_outside_data_reports")
    if not is_under(paths.report_markdown, allowed_reports) or paths.report_markdown.suffix.lower() != ".md":
        errors.append("output_markdown_outside_data_reports")
    return errors


def base_report(status: str, reason: str, decision: str, write_report: bool) -> dict[str, Any]:
    safety = report_safety(read_only=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": decision,
        "operation_id": None,
        "authorization_reference": None,
        "pre_write_feedback_count": 0,
        "planned_event_count": 0,
        "post_write_feedback_count": 0,
        "applied_event_count": 0,
        "already_existing_count": 0,
        "missing_after_count": 0,
        "duplicate_count": 0,
        "conflict_count": 0,
        "schema_error_count": 0,
        "backup_created": False,
        "rollback_performed": False,
        "already_applied": False,
        "write_performed": False,
        "backfill_performed": False,
        "manual_intervention_required": False,
        "continuity_status": "not_evaluated",
        "watermark_status": "not_evaluated",
        "microbatch_readiness_status": "not_evaluated",
        "qlib_backend_status": "not_evaluated",
        "walkforward_gate_status": "not_evaluated",
        "execution_cost_gate_status": "not_evaluated",
        "drift_gate_status": "not_evaluated",
        "registry_gate_status": "not_evaluated",
        "final_readiness_decision": "MANTER_EM_RESEARCH",
        "blockers": [],
        "warnings": [],
        "write_report_requested": bool(write_report),
        "write_report_performed": False,
        **safety,
        "safety_flags": safety,
    }


def maybe_write_report(report: dict[str, Any], paths: CloseoutPaths, write_report: bool) -> dict[str, Any]:
    if not write_report:
        return report
    final = dict(report)
    final["write_report_performed"] = True
    atomic_write(paths.report_json, json.dumps(final, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    atomic_write(paths.report_markdown, render_markdown(final))
    return final


def atomic_write(path: Path, content: str) -> None:
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Feedback Autotrain E2E Closeout V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Planned events: `{report.get('planned_event_count')}`",
            f"- Applied events: `{report.get('applied_event_count')}`",
            f"- Continuity: `{report.get('continuity_status')}`",
            f"- Watermark: `{report.get('watermark_status')}`",
            f"- Microbatch readiness: `{report.get('microbatch_readiness_status')}`",
            "",
            "No training, model promotion, active registry update, signal write or order submission is performed.",
            "",
        ]
    )
