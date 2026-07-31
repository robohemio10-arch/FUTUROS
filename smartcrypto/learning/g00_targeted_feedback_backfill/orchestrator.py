"""Target-only G00 feedback backfill orchestrator."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autotrain_feedback_gap_backfill_dryrun.dryrun import (
    build_dryrun_from_plan,
    validate_simulated_event,
)
from smartcrypto.learning.paper_autotrain_feedback_gap_diagnostics import (
    build_paper_autotrain_feedback_gap_diagnostics_v1,
)
from smartcrypto.learning.paper_autotrain_feedback_gap_remediation_plan import (
    build_remediation_plan_from_diagnostics,
)
from smartcrypto.learning.paper_feedback_autotrain_e2e_closeout.controlled_backfill import (
    BackfillRequest,
    canonical_sha256,
    event_identity,
    execute_controlled_backfill,
    load_jsonl,
    source_fingerprint,
)

from .contracts import (
    ALLOWED_REPORT_ROOT,
    CONFIRMATION_TEXT,
    DEFAULT_BACKUP_DIR,
    DEFAULT_FEEDBACK_EVENTS,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
    SCHEMA_VERSION,
    TARGET_ORDER_IDS,
    TARGET_TRADE_IDS,
    TargetedAuthorization,
    authorization_errors,
    idempotent_authorization_errors,
    is_under,
    report_safety,
    resolve_under_root,
    sanitized_report,
)


def run_g00_targeted_feedback_backfill_v1(
    *,
    project_root: str | Path,
    execute_targeted_backfill: bool = False,
    expected_plan_hash: str | None = None,
    expected_dryrun_hash: str | None = None,
    expected_target_batch_hash: str | None = None,
    expected_source_fingerprint_hash: str | None = None,
    authorization_reference: str | None = None,
    confirmation_text: str | None = None,
    allow_paper_db_read: bool = False,
    paper_db_path: str | Path | None = None,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    diagnostics_override: Mapping[str, Any] | None = None,
    post_write_validator: Callable[[Sequence[Mapping[str, Any]]], bool]
    | None = None,
    source_change_hook: Callable[[], None] | None = None,
    replace_function: Callable[..., None] = os.replace,
    rollback_replace_function: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Recompute, authorize and apply only feedback events 599 and 600."""

    root = Path(project_root).resolve()
    try:
        feedback_path = resolve_under_root(
            root,
            None,
            DEFAULT_FEEDBACK_EVENTS,
        )
        backup_dir = resolve_under_root(
            root,
            None,
            DEFAULT_BACKUP_DIR,
        )
        report_json = resolve_under_root(
            root,
            output_json_path,
            DEFAULT_REPORT_JSON,
        )
        report_markdown = resolve_under_root(
            root,
            output_markdown_path,
            DEFAULT_REPORT_MARKDOWN,
        )
        resolved_paper_db = (
            resolve_under_root(root, paper_db_path, Path("."))
            if paper_db_path is not None
            else None
        )
    except ValueError:
        return _base_report(
            reason="unsafe_path",
            decision="MANTER_B06_BLOQUEADA",
            write_report=write_report,
        )

    discovery_errors = _validate_paths(
        root=root,
        feedback_path=feedback_path,
        report_json=report_json,
        report_markdown=report_markdown,
        paper_db_path=resolved_paper_db,
    )
    existing_events, feedback_errors = load_jsonl(feedback_path)

    diagnostics = (
        dict(diagnostics_override)
        if diagnostics_override is not None
        else build_paper_autotrain_feedback_gap_diagnostics_v1(
            project_root=root,
            paper_db_path=resolved_paper_db,
            allow_paper_db_read=allow_paper_db_read,
            write_report=False,
        )
    )
    diagnostics_identity = _diagnostics_identity_payload(diagnostics)
    diagnostics_hash = canonical_sha256(diagnostics_identity)
    plan = build_remediation_plan_from_diagnostics(
        diagnostics,
        input_report_hash=diagnostics_hash,
        input_report_path="in_memory_recomputed_diagnostics",
        output_paths={},
    )
    plan_hash = _text(plan.get("plan_hash"))
    dryrun = build_dryrun_from_plan(
        plan,
        existing_feedback_events=existing_events,
        expected_plan_hash=plan_hash,
        input_plan_hash=canonical_sha256(plan),
        input_plan_path="in_memory_recomputed_plan",
        feedback_events_path=feedback_path.relative_to(root).as_posix(),
        output_paths={},
    )
    dryrun_hash = _text(dryrun.get("dryrun_hash"))

    simulated_events = [
        dict(item)
        for item in (dryrun.get("simulated_feedback_events") or [])
        if isinstance(item, Mapping)
    ]
    candidate_targets = _group_targets(simulated_events)
    existing_targets = _group_targets(existing_events)
    target_validation = _target_validation(
        candidate_targets=candidate_targets,
        existing_targets=existing_targets,
    )
    effective_targets, effective_errors = _effective_target_batch(
        candidate_targets=candidate_targets,
        existing_targets=existing_targets,
    )
    effective_events = [
        effective_targets[target]
        for target in TARGET_TRADE_IDS
        if target in effective_targets
    ]
    target_batch_hash = (
        canonical_sha256(effective_events)
        if len(effective_events) == len(TARGET_TRADE_IDS)
        else None
    )

    external_sources, external_source_errors = _external_sources(
        root=root,
        diagnostics=diagnostics,
        explicit_paper_db=resolved_paper_db,
    )
    try:
        fingerprint = source_fingerprint(external_sources)
    except ValueError as exc:
        external_source_errors.append(str(exc))
        fingerprint = canonical_sha256([])

    full_planned_count = int(
        dryrun.get("simulated_feedback_event_count", 0) or 0
    )
    target_planned_count = sum(
        len(candidate_targets[target])
        for target in TARGET_TRADE_IDS
    )
    other_planned_count = max(
        0,
        full_planned_count - target_planned_count,
    )
    target_existing_count = sum(
        len(existing_targets[target])
        for target in TARGET_TRADE_IDS
    )

    report = {
        **_base_report(
            reason="explicit_targeted_backfill_authorization_required",
            decision="NO_TARGETED_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION",
            write_report=write_report,
        ),
        "diagnostics_identity_hash": diagnostics_hash,
        "plan_hash": plan_hash or None,
        "dryrun_hash": dryrun_hash or None,
        "target_batch_hash": target_batch_hash,
        "source_fingerprint_hash": fingerprint,
        "full_planned_event_count": full_planned_count,
        "target_planned_event_count": target_planned_count,
        "other_planned_event_count": other_planned_count,
        "target_existing_event_count": target_existing_count,
        "target_effective_event_count": len(effective_events),
        "target_validation": target_validation,
        "pre_write_feedback_count": len(existing_events),
        "conflict_count": int(
            diagnostics.get("conflicting_group_count", 0) or 0
        ),
        "schema_error_count": sum(
            item["schema_error_count"]
            for item in target_validation.values()
        ),
        "blockers": sorted(
            set(
                [
                    *discovery_errors,
                    *feedback_errors,
                    *external_source_errors,
                    *effective_errors,
                ]
            )
        ),
        "warnings": (
            ["non_target_feedback_events_intentionally_excluded"]
            if other_planned_count > 0
            else []
        ),
    }

    preflight_errors = _preflight_errors(
        diagnostics=diagnostics,
        plan=plan,
        dryrun=dryrun,
        target_validation=target_validation,
        effective_errors=effective_errors,
        discovery_errors=discovery_errors,
        feedback_errors=feedback_errors,
        external_source_errors=external_source_errors,
        target_existing_count=target_existing_count,
    )

    authorization = TargetedAuthorization(
        execute_targeted_backfill=execute_targeted_backfill,
        expected_plan_hash=expected_plan_hash,
        expected_dryrun_hash=expected_dryrun_hash,
        expected_target_batch_hash=expected_target_batch_hash,
        expected_source_fingerprint_hash=(
            expected_source_fingerprint_hash
        ),
        authorization_reference=authorization_reference,
        confirmation_text=confirmation_text,
    )

    target_identity_sets = {
        target: {
            event_identity(event)
            for event in existing_targets[target]
        }
        for target in TARGET_TRADE_IDS
    }
    effective_identity_sets = {
        target: (
            {event_identity(effective_targets[target])}
            if target in effective_targets
            else set()
        )
        for target in TARGET_TRADE_IDS
    }
    fully_applied = all(
        len(target_identity_sets[target]) == 1
        and target_identity_sets[target] == effective_identity_sets[target]
        for target in TARGET_TRADE_IDS
    )

    if fully_applied and not effective_errors:
        stored_plan_hashes = {
            _text(existing_targets[target][0].get("source_plan_hash")).casefold()
            for target in TARGET_TRADE_IDS
        }
        idempotent_errors = idempotent_authorization_errors(
            authorization,
            target_batch_hash=target_batch_hash,
            source_fingerprint_hash=fingerprint,
            stored_source_plan_hashes=stored_plan_hashes,
        )
        if not idempotent_errors:
            report.update(
                {
                    "status": "ok",
                    "reason": "targeted_backfill_already_applied",
                    "decision": "TARGETED_BACKFILL_ALREADY_APPLIED",
                    "authorization_reference": authorization_reference,
                    "already_existing_count": len(TARGET_TRADE_IDS),
                    "missing_after_count": 0,
                    "already_applied": True,
                    "blockers": [],
                }
            )
            safety = report_safety(
                read_only=True,
                writes_feedback_store=False,
            )
            report.update(safety)
            report["safety_flags"] = safety
            return _maybe_write_report(
                sanitized_report(report),
                report_json=report_json,
                report_markdown=report_markdown,
                write_report=write_report,
            )

    report["blockers"] = sorted(
        set([*report["blockers"], *preflight_errors])
    )

    auth_errors = authorization_errors(
        authorization,
        plan_hash=plan_hash,
        dryrun_hash=dryrun_hash,
        target_batch_hash=target_batch_hash,
        source_fingerprint_hash=fingerprint,
    )
    if auth_errors:
        report["blockers"] = sorted(
            set([*report["blockers"], *auth_errors])
        )
        mismatch_errors = [
            error
            for error in auth_errors
            if error.endswith("_mismatch")
        ]
        if mismatch_errors:
            report["reason"] = mismatch_errors[0]
        return _maybe_write_report(
            sanitized_report(report),
            report_json=report_json,
            report_markdown=report_markdown,
            write_report=write_report,
        )

    if preflight_errors:
        report["status"] = "blocked"
        report["reason"] = preflight_errors[0]
        report["decision"] = "CORRIGIR_TARGETED_BACKFILL_ANTES_DE_EXECUTAR"
        report["blockers"] = sorted(
            set([*report["blockers"], *preflight_errors])
        )
        return _maybe_write_report(
            sanitized_report(report),
            report_json=report_json,
            report_markdown=report_markdown,
            write_report=write_report,
        )

    operation_id = canonical_sha256(
        {
            "plan_hash": plan_hash,
            "dryrun_hash": dryrun_hash,
            "target_batch_hash": target_batch_hash,
            "source_fingerprint_hash": fingerprint,
            "authorization_reference": authorization_reference,
            "target_trade_ids": list(TARGET_TRADE_IDS),
        }
    )[:20]

    pre_identities = [event_identity(event) for event in existing_events]
    target_identities = [
        event_identity(event)
        for event in effective_events
    ]
    composed_validator = _post_write_validator(
        pre_identities=pre_identities,
        target_identities=target_identities,
        external_validator=post_write_validator,
    )

    transaction = execute_controlled_backfill(
        BackfillRequest(
            feedback_path=feedback_path,
            backup_dir=backup_dir,
            lock_path=feedback_path.with_suffix(
                feedback_path.suffix + ".lock"
            ),
            operation_id=operation_id,
            authorization_reference=str(authorization_reference),
            candidate_events=tuple(effective_events),
            external_source_paths=external_sources,
            source_fingerprint_hash=fingerprint,
        ),
        replace_function=replace_function,
        rollback_replace_function=rollback_replace_function,
        post_write_validator=composed_validator,
        source_change_hook=source_change_hook,
    )
    report.update(transaction)
    report.update(
        {
            "plan_hash": plan_hash,
            "dryrun_hash": dryrun_hash,
            "target_batch_hash": target_batch_hash,
            "source_fingerprint_hash": fingerprint,
            "authorization_reference": authorization_reference,
        }
    )
    if transaction.get("status") == "ok":
        report["decision"] = (
            "TARGETED_G00_FEEDBACK_BACKFILL_APPLIED"
            if transaction.get("write_performed")
            else "TARGETED_BACKFILL_ALREADY_APPLIED"
        )
        report["reason"] = (
            "targeted_g00_feedback_backfill_completed"
            if transaction.get("write_performed")
            else "targeted_backfill_already_applied"
        )
        report["status"] = "ok"
    safety = report_safety(
        read_only=not bool(report.get("write_performed")),
        writes_feedback_store=bool(report.get("write_performed")),
    )
    report.update(safety)
    report["safety_flags"] = safety
    return _maybe_write_report(
        sanitized_report(report),
        report_json=report_json,
        report_markdown=report_markdown,
        write_report=write_report,
    )



_VOLATILE_DIAGNOSTICS_KEYS = frozenset(
    {
        "generated_at_utc",
        "output_paths",
        "write_report_requested",
        "write_performed",
        "safety_flags",
    }
)


def _diagnostics_identity_payload(value: Any) -> Any:
    """Return the deterministic evidence identity used by authorization hashes.

    Runtime/report metadata that does not change the underlying feedback-gap
    evidence is excluded recursively. Financial fields, trade identities,
    source authority, validation results, writer inventory and warnings remain
    bound to the hash.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _diagnostics_identity_payload(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
            if str(key) not in _VOLATILE_DIAGNOSTICS_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [
            _diagnostics_identity_payload(item)
            for item in value
        ]
    return value


def event_target(event: Mapping[str, Any]) -> str | None:
    """Resolve one candidate to the canonical G00 target identity."""

    paper_id = _text(event.get("paper_db_trade_id"))
    order_id = _text(event.get("closed_trades_csv_order_id"))
    searchable = " ".join(
        _text(event.get(field))
        for field in (
            "native_key",
            "dedup_key",
            "idempotency_key",
        )
    )
    for target in TARGET_TRADE_IDS:
        if paper_id == target:
            return target
        if order_id == TARGET_ORDER_IDS[target]:
            return target
        tokens = (
            f"trade_close:{target}",
            f"order_close:{TARGET_ORDER_IDS[target]}",
            TARGET_ORDER_IDS[target],
        )
        if any(token in searchable for token in tokens):
            return target
    return None


def _group_targets(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        target: [] for target in TARGET_TRADE_IDS
    }
    for event in events:
        target = event_target(event)
        if target is not None:
            grouped[target].append(dict(event))
    return grouped


def _target_validation(
    *,
    candidate_targets: Mapping[str, Sequence[Mapping[str, Any]]],
    existing_targets: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for target in TARGET_TRADE_IDS:
        candidates = list(candidate_targets[target])
        existing = list(existing_targets[target])
        schema_errors = sorted(
            {
                error
                for event in [*candidates, *existing]
                for error in validate_simulated_event(event)
            }
        )
        output[target] = {
            "candidate_count": len(candidates),
            "existing_count": len(existing),
            "schema_error_count": len(schema_errors),
            "schema_errors": schema_errors,
            "candidate_ready": (
                len(candidates) == 1
                and len(existing) == 0
                and not schema_errors
                and (
                    candidates[0].get("validation_status") or {}
                ).get("would_pass_both_stages")
                is True
            ),
        }
    return output


def _effective_target_batch(
    *,
    candidate_targets: Mapping[str, Sequence[Mapping[str, Any]]],
    existing_targets: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    effective: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for target in TARGET_TRADE_IDS:
        candidates = list(candidate_targets[target])
        existing = list(existing_targets[target])
        if len(candidates) > 1:
            errors.append(f"target_{target}_multiple_candidates")
            continue
        if len(existing) > 1:
            errors.append(f"target_{target}_multiple_existing_events")
            continue
        if candidates and existing:
            if event_identity(candidates[0]) != event_identity(existing[0]):
                errors.append(f"target_{target}_candidate_existing_conflict")
                continue
            effective[target] = dict(candidates[0])
            continue
        if candidates:
            effective[target] = dict(candidates[0])
            continue
        if existing:
            effective[target] = dict(existing[0])
            continue
        errors.append(f"target_{target}_missing")
    return effective, errors


def _external_sources(
    *,
    root: Path,
    diagnostics: Mapping[str, Any],
    explicit_paper_db: Path | None,
) -> tuple[tuple[Path, ...], list[str]]:
    errors: list[str] = []
    csv_raw = diagnostics.get("closed_trades_csv_path")
    csv_path = (
        Path(str(csv_raw)).resolve()
        if csv_raw
        else (root / "data/trades/inbox/freqtrade_paper_closed_trades.csv")
        .resolve()
    )
    paper_raw = diagnostics.get("paper_db_path")
    paper_path = (
        explicit_paper_db
        if explicit_paper_db is not None
        else (Path(str(paper_raw)).resolve() if paper_raw else None)
    )
    sources = [csv_path]
    if paper_path is not None:
        sources.append(paper_path)

    for path in sources:
        if path.is_symlink():
            errors.append(f"symlink_source_forbidden:{path.name}")
        if not path.is_file():
            errors.append(f"external_source_missing:{path.name}")
    return tuple(sources), errors


def _preflight_errors(
    *,
    diagnostics: Mapping[str, Any],
    plan: Mapping[str, Any],
    dryrun: Mapping[str, Any],
    target_validation: Mapping[str, Mapping[str, Any]],
    effective_errors: Sequence[str],
    discovery_errors: Sequence[str],
    feedback_errors: Sequence[str],
    external_source_errors: Sequence[str],
    target_existing_count: int,
) -> list[str]:
    errors = [
        *discovery_errors,
        *feedback_errors,
        *external_source_errors,
        *effective_errors,
    ]
    if diagnostics.get("status") not in {"ok", "warning"}:
        errors.append("diagnostics_not_ready")
    if plan.get("status") != "ok":
        errors.append("remediation_plan_not_ready")
    if dryrun.get("status") != "ok":
        errors.append("dryrun_not_ready")
    if int(dryrun.get("schema_validation_error_count", 0) or 0):
        errors.append("dryrun_schema_validation_failed")
    if int(dryrun.get("duplicate_simulated_event_count", 0) or 0):
        errors.append("dryrun_duplicate_events")
    if int(diagnostics.get("conflicting_group_count", 0) or 0):
        errors.append("diagnostic_conflicts")
    if target_existing_count not in {0, len(TARGET_TRADE_IDS)}:
        errors.append("partial_target_materialization_detected")
    if target_existing_count == 0:
        for target in TARGET_TRADE_IDS:
            if not target_validation[target]["candidate_ready"]:
                errors.append(f"target_{target}_not_ready")
    return sorted(set(errors))


def _post_write_validator(
    *,
    pre_identities: Sequence[str],
    target_identities: Sequence[str],
    external_validator: Callable[[Sequence[Mapping[str, Any]]], bool]
    | None,
) -> Callable[[Sequence[Mapping[str, Any]]], bool]:
    pre_counts = Counter(pre_identities)
    target_set = set(target_identities)
    expected_identities = set(pre_counts) | target_set
    expected_size = len(pre_identities) + len(
        target_set - set(pre_counts)
    )

    def validate(rows: Sequence[Mapping[str, Any]]) -> bool:
        post_identities = [event_identity(row) for row in rows]
        post_counts = Counter(post_identities)
        structural_ok = (
            len(post_identities) == expected_size
            and set(post_counts) == expected_identities
            and all(
                post_counts[identity] == count
                for identity, count in pre_counts.items()
            )
            and all(
                post_counts[identity] == 1
                for identity in target_set
            )
        )
        external_ok = (
            external_validator(rows)
            if external_validator is not None
            else True
        )
        return structural_ok and external_ok

    return validate


def _validate_paths(
    *,
    root: Path,
    feedback_path: Path,
    report_json: Path,
    report_markdown: Path,
    paper_db_path: Path | None,
) -> list[str]:
    errors: list[str] = []
    if feedback_path.is_symlink():
        errors.append("feedback_store_symlink_forbidden")
    if feedback_path.suffix.lower() != ".jsonl":
        errors.append("feedback_store_extension_invalid")
    if not feedback_path.is_file():
        errors.append("feedback_store_missing")
    if paper_db_path is not None:
        if paper_db_path.is_symlink():
            errors.append("paper_db_symlink_forbidden")
        if paper_db_path.suffix.lower() not in {".sqlite", ".db"}:
            errors.append("paper_db_extension_invalid")
    allowed_reports = root / ALLOWED_REPORT_ROOT
    if (
        not is_under(report_json, allowed_reports)
        or report_json.suffix.lower() != ".json"
    ):
        errors.append("output_json_outside_data_reports")
    if (
        not is_under(report_markdown, allowed_reports)
        or report_markdown.suffix.lower() != ".md"
    ):
        errors.append("output_markdown_outside_data_reports")
    return errors


def _base_report(
    *,
    reason: str,
    decision: str,
    write_report: bool,
) -> dict[str, Any]:
    safety = report_safety(read_only=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "decision": decision,
        "operation_id": None,
        "authorization_reference": None,
        "plan_hash": None,
        "dryrun_hash": None,
        "target_batch_hash": None,
        "source_fingerprint_hash": None,
        "full_planned_event_count": 0,
        "target_planned_event_count": 0,
        "other_planned_event_count": 0,
        "target_existing_event_count": 0,
        "target_effective_event_count": 0,
        "target_validation": {},
        "pre_write_feedback_count": 0,
        "post_write_feedback_count": 0,
        "planned_event_count": 0,
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
        "blockers": [],
        "warnings": [],
        "write_report_requested": bool(write_report),
        "write_report_performed": False,
        **safety,
        "safety_flags": safety,
    }


def _maybe_write_report(
    report: dict[str, Any],
    *,
    report_json: Path,
    report_markdown: Path,
    write_report: bool,
) -> dict[str, Any]:
    if not write_report:
        return report
    final = dict(report)
    final["write_report_performed"] = True
    _atomic_write(
        report_json,
        json.dumps(
            final,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _atomic_write(report_markdown, _render_markdown(final))
    return final


def _atomic_write(path: Path, content: str) -> None:
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


def _render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# G00 Targeted Feedback Backfill V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Target trades: `{', '.join(TARGET_TRADE_IDS)}`",
            f"- Full planned events: `{report.get('full_planned_event_count')}`",
            f"- Target planned events: `{report.get('target_planned_event_count')}`",
            f"- Other events excluded: `{report.get('other_planned_event_count')}`",
            f"- Applied events: `{report.get('applied_event_count')}`",
            f"- Write performed: `{report.get('write_performed')}`",
            f"- Runtime activation: `{report.get('runtime_activation')}`",
            "",
        ]
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "CONFIRMATION_TEXT",
    "event_target",
    "run_g00_targeted_feedback_backfill_v1",
]
