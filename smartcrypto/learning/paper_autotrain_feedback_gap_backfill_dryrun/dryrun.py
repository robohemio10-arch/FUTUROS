"""Simulate a paper feedback-gap backfill without applying any event.

The module reads the approved remediation plan and the current feedback JSONL,
materializes candidate events in memory, validates their schema and identity,
and reports whether a future implementation branch could safely proceed. It
has no writer for feedback, microbatches, runtime, SQLite, Parquet, models, or
registries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "paper_autotrain_feedback_gap_backfill_dryrun_v1"
EVENT_SCHEMA_VERSION = "paper_autotrain_feedback_gap_backfill_candidate_v1"
SOURCE_PLAN_SCHEMA_VERSION = "paper_autotrain_feedback_gap_remediation_plan_v1"
SOURCE_PLAN_DECISION = "PLAN_ONLY_NO_BACKFILL"
EVENT_TYPE = "paper_autotrain_feedback_gap_backfill_candidate"
SIMULATION_STATUS = "SIMULATED_ONLY_NOT_WRITTEN"

DEFAULT_EXPECTED_PLAN_HASH = "7a566e9359c55c42d4f9606e35b4359cb0bad345be79ce978c8e848b4f0aaacb"
DEFAULT_PLAN_REPORT = Path("data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json")
DEFAULT_FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")
DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

DRYRUN_READY = "DRYRUN_READY_NO_BACKFILL"
BLOCKED_PLAN = "BLOCKED_PLAN_NOT_READY"
BLOCKED_DUPLICATES = "BLOCKED_DUPLICATE_SIMULATED_EVENTS"
BLOCKED_EXISTING = "BLOCKED_EVENT_ALREADY_EXISTS"
BLOCKED_SCHEMA = "BLOCKED_SCHEMA_VALIDATION_FAILED"
BLOCKED_HASH = "BLOCKED_SOURCE_PLAN_HASH_MISMATCH"

REQUIRED_EVENT_FIELDS = frozenset(
    {
        "event_type",
        "schema_version",
        "idempotency_key",
        "source_plan_id",
        "source_plan_hash",
        "dedup_key",
        "native_key",
        "closed_trades_csv_order_id",
        "paper_db_trade_id",
        "symbol",
        "side",
        "open_time_utc",
        "close_time_utc",
        "net_pnl",
        "profit_ratio",
        "source_presence",
        "source_keys",
        "validation_status",
        "simulation_status",
        "event_hash",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def build_paper_autotrain_feedback_gap_backfill_dryrun_v1(
    *,
    project_root: str | Path,
    plan_report_path: str | Path | None = None,
    feedback_events_path: str | Path | None = None,
    expected_plan_hash: str = DEFAULT_EXPECTED_PLAN_HASH,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Load sources and build the dry-run report."""

    root = Path(project_root).resolve()
    plan_path = _resolve(root, plan_report_path, DEFAULT_PLAN_REPORT)
    feedback_path = _resolve(root, feedback_events_path, DEFAULT_FEEDBACK_EVENTS)
    output_json = _resolve(root, output_json_path, DEFAULT_OUTPUT_JSON)
    output_markdown = _resolve(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN)
    write_errors = _validate_write_request(root, output_json, output_markdown, write_report)

    plan, plan_load_error, input_plan_hash = _load_json_object(plan_path)
    existing_events, feedback_load_errors = _load_jsonl_objects(feedback_path)
    report = build_dryrun_from_plan(
        plan,
        existing_feedback_events=existing_events,
        expected_plan_hash=expected_plan_hash,
        input_plan_hash=input_plan_hash,
        input_plan_path=_display_path(root, plan_path),
        feedback_events_path=_display_path(root, feedback_path),
        output_paths={
            "json": _display_path(root, output_json),
            "markdown": _display_path(root, output_markdown),
        },
        generated_at_utc=generated_at_utc,
        plan_load_error=plan_load_error,
        feedback_load_errors=feedback_load_errors,
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


def build_dryrun_from_plan(
    plan: Mapping[str, Any] | None,
    *,
    existing_feedback_events: Sequence[Mapping[str, Any]],
    expected_plan_hash: str,
    input_plan_hash: str | None,
    input_plan_path: str,
    feedback_events_path: str,
    output_paths: Mapping[str, str],
    generated_at_utc: str | None = None,
    plan_load_error: str | None = None,
    feedback_load_errors: Sequence[str] = (),
    write_report_requested: bool = False,
    write_validation_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Purely simulate candidate events from a loaded remediation plan."""

    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    source = dict(plan or {})
    source_plan_status = str(source.get("status") or "missing")
    source_plan_decision = str(source.get("decision") or "missing")
    source_plan_hash = str(source.get("plan_hash") or "")
    source_plan_id = str(source.get("plan_id") or "")
    planned_count = _as_non_negative_int(source.get("planned_feedback_event_count"))
    eligible_records = _mapping_list(source.get("eligible_missing_records"))

    expected_hash_normalized = str(expected_plan_hash or "").strip().casefold()
    source_hash_normalized = source_plan_hash.strip().casefold()
    plan_hash_mismatch = bool(expected_hash_normalized and source_hash_normalized != expected_hash_normalized)
    plan_errors = _plan_contract_errors(
        source,
        plan_load_error=plan_load_error,
        planned_count=planned_count,
        eligible_count=len(eligible_records),
        feedback_load_errors=feedback_load_errors,
        write_validation_errors=write_validation_errors,
    )
    plan_ready = not plan_hash_mismatch and not plan_errors

    simulated_events: list[dict[str, Any]] = []
    if plan_ready:
        simulated_events = sorted(
            (_build_simulated_event(record, source_plan_id, source_plan_hash) for record in eligible_records),
            key=_event_sort_key,
        )

    schema_errors_by_hash: dict[str, list[str]] = {}
    for event in simulated_events:
        errors = validate_simulated_event(event)
        if errors:
            schema_errors_by_hash[event["event_hash"]] = errors

    event_hashes = [str(event["event_hash"]) for event in simulated_events]
    identity_keys = [str(event["idempotency_key"]) for event in simulated_events]
    duplicate_hashes = {value for value, count in Counter(event_hashes).items() if count > 1}
    duplicate_idempotency_keys = {value for value, count in Counter(identity_keys).items() if count > 1}
    duplicate_indexes = {
        index
        for index, event in enumerate(simulated_events)
        if event["event_hash"] in duplicate_hashes
        or event["idempotency_key"] in duplicate_idempotency_keys
    }
    duplicate_simulated_event_count = max(
        len(event_hashes) - len(set(event_hashes)),
        len(identity_keys) - len(set(identity_keys)),
    )

    existing_index = _build_existing_event_index(existing_feedback_events)
    existing_matches: dict[int, list[str]] = {}
    for index, event in enumerate(simulated_events):
        reasons = _existing_match_reasons(event, existing_index)
        if reasons:
            existing_matches[index] = reasons

    blocked_events: list[dict[str, Any]] = []
    for index, event in enumerate(simulated_events):
        reasons: list[str] = []
        reasons.extend(schema_errors_by_hash.get(str(event["event_hash"]), []))
        if index in duplicate_indexes:
            reasons.append("duplicate_simulated_event")
        reasons.extend(existing_matches.get(index, []))
        if reasons:
            blocked_events.append(
                {
                    "event_hash": event["event_hash"],
                    "idempotency_key": event["idempotency_key"],
                    "dedup_key": event["dedup_key"],
                    "blocked_reasons": sorted(set(reasons)),
                }
            )

    schema_validation_error_count = sum(len(errors) for errors in schema_errors_by_hash.values())
    already_existing_event_count = len(existing_matches)
    status, reason, decision = _decide(
        plan_hash_mismatch=plan_hash_mismatch,
        plan_errors=plan_errors,
        schema_validation_error_count=schema_validation_error_count,
        duplicate_simulated_event_count=duplicate_simulated_event_count,
        already_existing_event_count=already_existing_event_count,
    )
    dryrun_hash = _canonical_sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "source_plan_hash": source_plan_hash,
            "expected_plan_hash": expected_hash_normalized,
            "event_hashes": event_hashes,
            "decision": decision,
        }
    )
    safety = safety_flags()

    blockers = list(plan_errors)
    if plan_hash_mismatch:
        blockers.append("source_plan_hash_mismatch")
    if schema_validation_error_count:
        blockers.append("simulated_event_schema_validation_failed")
    if duplicate_simulated_event_count:
        blockers.append("duplicate_simulated_events")
    if already_existing_event_count:
        blockers.append("simulated_event_already_exists_in_feedback")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": status,
        "reason": reason,
        "decision": decision,
        "research_only": True,
        "read_only": True,
        "dryrun_only": True,
        "source_plan_status": source_plan_status,
        "source_plan_decision": source_plan_decision,
        "source_plan_hash": source_plan_hash or None,
        "expected_plan_hash": expected_hash_normalized or None,
        "source_plan_id": source_plan_id or None,
        "input_plan_path": input_plan_path,
        "input_plan_hash": input_plan_hash,
        "feedback_events_path": feedback_events_path,
        "feedback_events_checked_count": len(existing_feedback_events),
        "feedback_load_errors": list(feedback_load_errors),
        "planned_feedback_event_count": planned_count,
        "simulated_feedback_event_count": len(simulated_events),
        "duplicate_simulated_event_count": duplicate_simulated_event_count,
        "already_existing_event_count": already_existing_event_count,
        "schema_validation_error_count": schema_validation_error_count,
        "blocked_event_count": len(blocked_events),
        "simulated_feedback_events": simulated_events,
        "blocked_events": blocked_events,
        "event_hashes": event_hashes,
        "dryrun_hash": dryrun_hash,
        "blockers": sorted(set(blockers)),
        "warnings": [],
        "output_paths": dict(output_paths),
        "write_report_requested": bool(write_report_requested),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }


def _build_simulated_event(
    record: Mapping[str, Any],
    source_plan_id: str,
    source_plan_hash: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_type": EVENT_TYPE,
        "schema_version": EVENT_SCHEMA_VERSION,
        "idempotency_key": record.get("idempotency_key"),
        "source_plan_id": source_plan_id,
        "source_plan_hash": source_plan_hash,
        "dedup_key": record.get("dedup_key"),
        "native_key": record.get("native_key"),
        "closed_trades_csv_order_id": record.get("closed_trades_csv_order_id"),
        "paper_db_trade_id": record.get("paper_db_trade_id"),
        "symbol": record.get("symbol"),
        "side": record.get("side"),
        "open_time_utc": record.get("open_time_utc"),
        "close_time_utc": record.get("close_time_utc"),
        "net_pnl": record.get("net_pnl"),
        "profit_ratio": record.get("profit_ratio"),
        "source_presence": sorted(str(value) for value in (record.get("source_presence") or [])),
        "source_keys": _normalized_source_keys(record.get("source_keys")),
        "validation_status": dict(record.get("validation_status") or {}),
        "simulation_status": SIMULATION_STATUS,
    }
    event["event_hash"] = _canonical_sha256(event)
    return event


def validate_simulated_event(event: Mapping[str, Any]) -> list[str]:
    """Validate the candidate event contract without invoking operational code."""

    errors: list[str] = []
    missing = sorted(REQUIRED_EVENT_FIELDS - set(event))
    errors.extend(f"missing_field:{field}" for field in missing)
    if event.get("event_type") != EVENT_TYPE:
        errors.append("invalid_event_type")
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        errors.append("invalid_event_schema_version")
    if event.get("simulation_status") != SIMULATION_STATUS:
        errors.append("invalid_simulation_status")
    for field in (
        "idempotency_key",
        "source_plan_id",
        "source_plan_hash",
        "dedup_key",
        "native_key",
        "closed_trades_csv_order_id",
        "paper_db_trade_id",
        "symbol",
        "side",
        "open_time_utc",
        "close_time_utc",
    ):
        if not str(event.get(field) or "").strip():
            errors.append(f"empty_field:{field}")
    if not _is_finite_number(event.get("net_pnl")):
        errors.append("invalid_net_pnl")
    if not _is_finite_number(event.get("profit_ratio")):
        errors.append("invalid_profit_ratio")
    if not isinstance(event.get("source_presence"), list) or not event.get("source_presence"):
        errors.append("invalid_source_presence")
    if not isinstance(event.get("source_keys"), Mapping) or not event.get("source_keys"):
        errors.append("invalid_source_keys")
    validation = event.get("validation_status")
    if not isinstance(validation, Mapping) or validation.get("would_pass_both_stages") is not True:
        errors.append("source_validation_not_approved")
    event_hash = str(event.get("event_hash") or "")
    if not SHA256_PATTERN.fullmatch(event_hash):
        errors.append("invalid_event_hash")
    else:
        payload = {key: value for key, value in event.items() if key != "event_hash"}
        if _canonical_sha256(payload) != event_hash:
            errors.append("event_hash_mismatch")
    return sorted(set(errors))


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Autotrain Feedback Gap Backfill Dry-Run V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Source plan decision: `{report.get('source_plan_decision')}`",
        f"- Source plan hash: `{report.get('source_plan_hash')}`",
        f"- Planned events: `{report.get('planned_feedback_event_count')}`",
        f"- Simulated events: `{report.get('simulated_feedback_event_count')}`",
        f"- Duplicate simulated events: `{report.get('duplicate_simulated_event_count')}`",
        f"- Already existing events: `{report.get('already_existing_event_count')}`",
        f"- Schema validation errors: `{report.get('schema_validation_error_count')}`",
        f"- Blocked events: `{report.get('blocked_event_count')}`",
        f"- Dry-run hash: `{report.get('dryrun_hash')}`",
        "",
        "## Boundary",
        "",
        "All events are simulated in memory. No feedback event is written and no backfill is performed.",
        "",
        "## Simulated Events",
        "",
        "| close_time_utc | dedup_key | idempotency_key | event_hash |",
        "| --- | --- | --- | --- |",
    ]
    for event in report.get("simulated_feedback_events") or []:
        lines.append(
            "| {close_time} | {dedup} | {idempotency} | {event_hash} |".format(
                close_time=_markdown_cell(event.get("close_time_utc")),
                dedup=_markdown_cell(event.get("dedup_key")),
                idempotency=_markdown_cell(event.get("idempotency_key")),
                event_hash=_markdown_cell(event.get("event_hash")),
            )
        )
    if not report.get("simulated_feedback_events"):
        lines.append("| none | - | - | - |")
    return "\n".join(lines) + "\n"


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "dryrun_only": True,
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
        "backfill_performed": False,
        "would_create_microbatch": False,
        "would_run_training": False,
        "would_promote_model": False,
    }


def _plan_contract_errors(
    source: Mapping[str, Any],
    *,
    plan_load_error: str | None,
    planned_count: int,
    eligible_count: int,
    feedback_load_errors: Sequence[str],
    write_validation_errors: Sequence[str],
) -> list[str]:
    errors = list(write_validation_errors)
    if plan_load_error:
        errors.append(plan_load_error)
    if not source:
        errors.append("missing_source_plan")
        return sorted(set(errors))
    if source.get("schema_version") != SOURCE_PLAN_SCHEMA_VERSION:
        errors.append("unexpected_source_plan_schema")
    if source.get("status") != "ok":
        errors.append("source_plan_status_not_ok")
    if source.get("decision") != SOURCE_PLAN_DECISION:
        errors.append("source_plan_decision_not_ready")
    if planned_count != eligible_count:
        errors.append("planned_event_count_mismatch")
    if _as_non_negative_int(source.get("blocked_feedback_event_count")) != 0:
        errors.append("source_plan_contains_blocked_events")
    errors.extend(feedback_load_errors)
    return sorted(set(errors))


def _decide(
    *,
    plan_hash_mismatch: bool,
    plan_errors: Sequence[str],
    schema_validation_error_count: int,
    duplicate_simulated_event_count: int,
    already_existing_event_count: int,
) -> tuple[str, str, str]:
    if plan_hash_mismatch:
        return "blocked", "source_plan_hash_mismatch", BLOCKED_HASH
    if plan_errors:
        return "blocked", "source_plan_not_ready", BLOCKED_PLAN
    if schema_validation_error_count:
        return "blocked", "simulated_event_schema_validation_failed", BLOCKED_SCHEMA
    if duplicate_simulated_event_count:
        return "blocked", "duplicate_simulated_events", BLOCKED_DUPLICATES
    if already_existing_event_count:
        return "blocked", "simulated_event_already_exists", BLOCKED_EXISTING
    return "ok", "dryrun_validated_without_backfill", DRYRUN_READY


def _build_existing_event_index(events: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    index = {
        "event_hash": set(),
        "idempotency_key": set(),
        "order_id": set(),
        "trade_id": set(),
    }
    for event in events:
        event_hash = str(event.get("event_hash") or "").strip().casefold()
        idempotency = str(event.get("idempotency_key") or "").strip()
        order_id = _normalize_identifier(
            event.get("closed_trades_csv_order_id", event.get("order_id"))
        )
        trade_id = _normalize_identifier(event.get("paper_db_trade_id", event.get("trade_id")))
        if event_hash:
            index["event_hash"].add(event_hash)
        if idempotency:
            index["idempotency_key"].add(idempotency)
        if order_id:
            index["order_id"].add(order_id)
        if trade_id:
            index["trade_id"].add(trade_id)
    return index


def _existing_match_reasons(event: Mapping[str, Any], index: Mapping[str, set[str]]) -> list[str]:
    reasons: list[str] = []
    if str(event.get("event_hash") or "").casefold() in index["event_hash"]:
        reasons.append("event_hash_already_exists")
    if str(event.get("idempotency_key") or "") in index["idempotency_key"]:
        reasons.append("idempotency_key_already_exists")
    order_id = _normalize_identifier(event.get("closed_trades_csv_order_id"))
    if order_id and order_id in index["order_id"]:
        reasons.append("closed_trades_csv_order_id_already_exists")
    trade_id = _normalize_identifier(event.get("paper_db_trade_id"))
    if trade_id and trade_id in index["trade_id"]:
        reasons.append("paper_db_trade_id_already_exists")
    return sorted(set(reasons))


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing_source_plan", None
    except OSError:
        return None, "unreadable_source_plan", None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_source_plan_json", digest
    if not isinstance(payload, dict):
        return None, "invalid_source_plan_payload", digest
    return payload, None, digest


def _load_jsonl_objects(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return [], ["missing_feedback_events_source"]
    except (OSError, UnicodeDecodeError):
        return [], ["unreadable_feedback_events_source"]
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"invalid_feedback_jsonl_line:{line_number}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"invalid_feedback_jsonl_object:{line_number}")
            continue
        events.append(payload)
    return events, errors


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
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(raw).hexdigest()


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


def _event_sort_key(event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("close_time_utc") or ""),
        str(event.get("dedup_key") or ""),
        str(event.get("idempotency_key") or ""),
    )


def _normalize_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.casefold()


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {float("inf"), float("-inf")}


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
