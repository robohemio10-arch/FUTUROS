from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.execution.capital_reservation_ledger import (  # noqa: E402
    ACTIVE_RESERVATION_STATUSES,
    DEFAULT_LEDGER_PATH,
    RESERVATION_STATUSES,
    connect,
    ensure_schema,
    unsafe_safety_flags,
)
from smartcrypto.execution.order_intent_ledger import (  # noqa: E402
    ACTIVE_INTENT_STATUSES,
    ORDER_INTENT_STATUSES,
)


DEFAULT_REPORT_PATH = Path("data/reports/order_intent_capital_ledger_audit_report.json")
SAFETY_FLAGS = (
    "paper_only",
    "shadow_only",
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
EXPECTED_SCHEMA = {
    "order_intents": {
        "order_intent_id",
        "correlation_id",
        "client_order_id",
        "idempotency_key",
        "symbol",
        "side",
        "order_type",
        "requested_notional",
        "requested_quantity",
        "reserved_capital",
        "leverage",
        "status",
        "created_at_utc",
        "updated_at_utc",
        "paper_only",
        "shadow_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    },
    "capital_reservations": {
        "reservation_id",
        "order_intent_id",
        "client_order_id",
        "idempotency_key",
        "symbol",
        "quote_asset",
        "reserved_amount",
        "consumed_amount",
        "released_amount",
        "status",
        "created_at_utc",
        "updated_at_utc",
        "paper_only",
        "shadow_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    },
    "order_intent_events": {
        "event_id",
        "order_intent_id",
        "client_order_id",
        "to_status",
        "valid_transition",
        "created_at_utc",
    },
    "capital_reservation_events": {
        "event_id",
        "reservation_id",
        "order_intent_id",
        "client_order_id",
        "event_type",
        "amount",
        "created_at_utc",
    },
}
CORE_TABLES = ("order_intents", "capital_reservations")
EVENT_TABLES = ("order_intent_events", "capital_reservation_events")
TABLE_INFO_SQL = {
    "order_intents": "PRAGMA table_info(order_intents)",
    "capital_reservations": "PRAGMA table_info(capital_reservations)",
    "order_intent_events": "PRAGMA table_info(order_intent_events)",
    "capital_reservation_events": "PRAGMA table_info(capital_reservation_events)",
}
SELECT_ALL_SQL = {
    "order_intents": "SELECT * FROM order_intents",
    "capital_reservations": "SELECT * FROM capital_reservations",
    "order_intent_events": "SELECT * FROM order_intent_events",
    "capital_reservation_events": "SELECT * FROM capital_reservation_events",
}


def run_order_intent_capital_ledger_audit(
    *,
    repository_path: str | Path = DEFAULT_LEDGER_PATH,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    strict: bool = False,
    initialize_empty_repository: bool = False,
) -> dict[str, Any]:
    repository = Path(repository_path)
    report = base_report(repository)
    if not repository.exists():
        if initialize_empty_repository:
            ensure_schema(repository)
            report["repository_materialized"] = True
            report["warnings"].append("empty_repository_initialized_without_events")
        else:
            report["status"] = "missing_data"
            report["reason"] = "repository_missing"
            report["repository_state"] = "repository_missing"
            report["reconciliation_required"] = True
            report["recommended_mode"] = "RECONCILING"
            report["required_sources_missing"].append(str(repository))
            report["missing_sources"].append(str(repository))
            report["blocking_findings"].append(f"missing_repository:{repository}")
            report["next_required_actions"] = next_required_actions(report)
            report["evidence_quality_summary"] = evidence_quality_summary(report)
            write_report(report, report_path)
            return report
    if repository.exists() and repository.stat().st_size == 0:
        report["status"] = "missing_data"
        report["reason"] = "repository_empty"
        report["repository_state"] = "repository_empty"
        report["warnings"].append("repository_empty")
        report["next_required_actions"] = next_required_actions(report)
        report["evidence_quality_summary"] = evidence_quality_summary(report)
        write_report(report, report_path)
        return report

    try:
        schema_report = inspect_schema(repository)
    except sqlite3.DatabaseError as exc:
        report["status"] = "blocked"
        report["reason"] = "schema_invalid"
        report["repository_state"] = "schema_invalid"
        report["schema_findings"].append(f"sqlite_read_failed:{exc}")
        report["blocking_findings"].append("schema_invalid")
        report["next_required_actions"] = next_required_actions(report)
        report["evidence_quality_summary"] = evidence_quality_summary(report)
        write_report(report, report_path)
        return report
    report["schema_findings"] = schema_report["schema_findings"]
    report["event_schema_findings"] = schema_report["event_schema_findings"]
    if schema_report["schema_findings"] or schema_report["event_schema_findings"]:
        report["status"] = "blocked"
        report["reason"] = "schema_invalid" if schema_report["schema_findings"] else "event_schema_invalid"
        report["repository_state"] = report["reason"]
        report["blocking_findings"].extend(schema_report["schema_findings"])
        report["blocking_findings"].extend(schema_report["event_schema_findings"])
        report["next_required_actions"] = next_required_actions(report)
        report["evidence_quality_summary"] = evidence_quality_summary(report)
        write_report(report, report_path)
        return report

    with connect(repository) as connection:
        order_intents = fetch_all(connection, "order_intents")
        reservations = fetch_all(connection, "capital_reservations")
        order_events = fetch_all(connection, "order_intent_events")
        reservation_events = fetch_all(connection, "capital_reservation_events")

    report["order_intents_count"] = len(order_intents)
    report["capital_reservations_count"] = len(reservations)
    report["order_intent_events_count"] = len(order_events)
    report["capital_reservation_events_count"] = len(reservation_events)
    report["active_intents_count"] = sum(1 for row in order_intents if row.get("status") in ACTIVE_INTENT_STATUSES)
    report["active_reservations_count"] = sum(1 for row in reservations if row.get("status") in ACTIVE_RESERVATION_STATUSES)
    report["duplicate_client_order_id_count"] = duplicate_count(row.get("client_order_id") for row in order_intents)
    report["duplicate_idempotency_key_count"] = duplicate_count(
        row.get("idempotency_key")
        for row in order_intents
        if row.get("status") in ACTIVE_INTENT_STATUSES
    )
    report["dispatch_unknown_count"] = sum(1 for row in order_intents if row.get("status") == "DISPATCH_UNKNOWN")
    report["negative_reservation_findings"] = negative_reservation_findings(reservations)
    report["over_consumption_findings"] = over_consumption_findings(reservations)
    report["double_spend_findings"] = double_spend_findings(reservations)
    report["invalid_transition_findings"] = invalid_transition_findings(order_intents, order_events)

    for finding in report["negative_reservation_findings"]:
        report["blocking_findings"].append(finding)
    for finding in report["over_consumption_findings"]:
        report["blocking_findings"].append(finding)
    for finding in report["double_spend_findings"]:
        report["blocking_findings"].append(finding)
    for finding in report["invalid_transition_findings"]:
        report["blocking_findings"].append(finding)
    if report["duplicate_client_order_id_count"] > 0:
        report["blocking_findings"].append("duplicate_client_order_id_detected")
    if report["duplicate_idempotency_key_count"] > 0:
        report["blocking_findings"].append("duplicate_idempotency_key_detected")
    if report["dispatch_unknown_count"] > 0:
        report["blocking_findings"].append("dispatch_unknown_active")

    for row in order_intents + reservations:
        unsafe = unsafe_safety_flags({flag: bool(row.get(flag)) for flag in SAFETY_FLAGS})
        for flag in unsafe:
            report["blocking_findings"].append(f"unsafe_safety_flag:{flag}")

    if strict and report["warnings"]:
        report["blocking_findings"].extend(f"strict_warning:{warning}" for warning in report["warnings"])

    report["blocking_findings"] = sorted(set(report["blocking_findings"]))
    report["reconciliation_required"] = bool(report["blocking_findings"])
    total_events = len(order_intents) + len(reservations) + len(order_events) + len(reservation_events)
    if total_events == 0:
        report["repository_state"] = "repository_present_but_no_events"
        report["warnings"].append("repository_present_but_no_events")
    else:
        report["repository_state"] = "repository_present_with_valid_events"
    report["recommended_mode"] = "NORMAL" if total_events == 0 and not report["blocking_findings"] else recommended_mode(report)
    if report["blocking_findings"]:
        report["status"] = "blocked"
        report["reason"] = "ledger_audit_blocked"
    elif report["repository_state"] == "repository_present_but_no_events":
        report["status"] = "warning"
        report["reason"] = "repository_present_but_no_events"
    elif report["warnings"]:
        report["status"] = "warning"
        report["reason"] = "ledger_audit_warnings"
    else:
        report["status"] = "ok"
        report["reason"] = "ledger_audit_ok"
    report["next_required_actions"] = next_required_actions(report)
    report["evidence_quality_summary"] = evidence_quality_summary(report)
    write_report(report, report_path)
    return report


def base_report(repository: Path) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": None,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repository_path": str(repository),
        "repository_state": "unknown",
        "repository_materialized": False,
        "expected_schema": {table: sorted(columns) for table, columns in EXPECTED_SCHEMA.items()},
        "schema_findings": [],
        "event_schema_findings": [],
        "order_intents_count": 0,
        "capital_reservations_count": 0,
        "order_intent_events_count": 0,
        "capital_reservation_events_count": 0,
        "active_intents_count": 0,
        "active_reservations_count": 0,
        "duplicate_client_order_id_count": 0,
        "duplicate_idempotency_key_count": 0,
        "dispatch_unknown_count": 0,
        "double_spend_findings": [],
        "negative_reservation_findings": [],
        "over_consumption_findings": [],
        "invalid_transition_findings": [],
        "reconciliation_required": True,
        "recommended_mode": "RECONCILING",
        "blocking_findings": [],
        "warnings": [],
        "missing_sources": [],
        "optional_sources_missing": [],
        "required_sources_missing": [],
        "next_required_actions": [],
        "evidence_quality_summary": {},
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def inspect_schema(repository: Path) -> dict[str, list[str]]:
    with connect(repository) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        schema_findings: list[str] = []
        event_schema_findings: list[str] = []
        for table in CORE_TABLES:
            if table not in tables:
                schema_findings.append(f"missing_table:{table}")
                continue
            missing_columns = EXPECTED_SCHEMA[table] - table_columns(connection, table)
            schema_findings.extend(f"missing_column:{table}.{column}" for column in sorted(missing_columns))
        for table in EVENT_TABLES:
            if table not in tables:
                event_schema_findings.append(f"missing_event_table:{table}")
                continue
            missing_columns = EXPECTED_SCHEMA[table] - table_columns(connection, table)
            event_schema_findings.extend(f"missing_event_column:{table}.{column}" for column in sorted(missing_columns))
    return {
        "schema_findings": sorted(schema_findings),
        "event_schema_findings": sorted(event_schema_findings),
    }


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(TABLE_INFO_SQL[table]).fetchall()}


def fetch_all(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(SELECT_ALL_SQL[table_name]).fetchall()
    return [dict(row) for row in rows]


def duplicate_count(values: Any) -> int:
    normalized = [str(value).strip() for value in values if str(value or "").strip()]
    counts = Counter(normalized)
    return sum(1 for count in counts.values() if count > 1)


def negative_reservation_findings(reservations: list[dict[str, Any]]) -> list[str]:
    findings = []
    for row in reservations:
        if (
            numeric(row.get("reserved_amount")) < 0
            or numeric(row.get("consumed_amount")) < 0
            or numeric(row.get("released_amount")) < 0
        ):
            findings.append(f"negative_reservation:{row.get('reservation_id')}")
    return findings


def over_consumption_findings(reservations: list[dict[str, Any]]) -> list[str]:
    findings = []
    for row in reservations:
        reserved = numeric(row.get("reserved_amount"))
        consumed = numeric(row.get("consumed_amount"))
        released = numeric(row.get("released_amount"))
        if consumed > reserved + 1e-9:
            findings.append(f"over_consumption:{row.get('reservation_id')}")
        if released > reserved - consumed + 1e-9:
            findings.append(f"over_release:{row.get('reservation_id')}")
        if consumed + released > reserved + 1e-9:
            findings.append(f"double_spend_balance:{row.get('reservation_id')}")
        if row.get("status") not in RESERVATION_STATUSES:
            findings.append(f"invalid_reservation_status:{row.get('reservation_id')}:{row.get('status')}")
    return findings


def double_spend_findings(reservations: list[dict[str, Any]]) -> list[str]:
    active = [row for row in reservations if row.get("status") in ACTIVE_RESERVATION_STATUSES]
    findings = []
    for field in ("idempotency_key", "order_intent_id", "client_order_id"):
        counts = Counter(str(row.get(field) or "").strip() for row in active if str(row.get(field) or "").strip())
        for value, count in counts.items():
            if count > 1:
                findings.append(f"double_spend_active_{field}:{value}")
    return sorted(set(findings))


def invalid_transition_findings(
    order_intents: list[dict[str, Any]],
    order_events: list[dict[str, Any]],
) -> list[str]:
    findings = []
    for row in order_intents:
        if row.get("status") not in ORDER_INTENT_STATUSES:
            findings.append(f"invalid_order_intent_status:{row.get('order_intent_id')}:{row.get('status')}")
    for event in order_events:
        if not bool(event.get("valid_transition")):
            findings.append(
                f"invalid_status_transition:{event.get('order_intent_id')}:{event.get('from_status')}->{event.get('to_status')}"
            )
    return findings


def recommended_mode(report: dict[str, Any]) -> str:
    if not report["blocking_findings"] and not report["warnings"]:
        return "NORMAL"
    if report["dispatch_unknown_count"] > 0 or report["duplicate_idempotency_key_count"] > 0:
        return "RECONCILING"
    if report["negative_reservation_findings"] or report["over_consumption_findings"] or report["double_spend_findings"]:
        return "PANIC"
    return "PROTECTION"


def next_required_actions(report: dict[str, Any]) -> list[str]:
    state = str(report.get("repository_state") or "")
    actions = []
    if state == "repository_missing":
        actions.append("initialize_empty_ledger_repository_if_runtime_needs_materialization")
    if state in {"repository_empty", "repository_present_but_no_events"}:
        actions.append("wait_for_real_paper_order_intent_or_capital_reservation_event")
    if state in {"schema_invalid", "event_schema_invalid"} or report.get("schema_findings") or report.get("event_schema_findings"):
        actions.append("repair_or_recreate_ledger_schema_without_fabricating_events")
    if report.get("blocking_findings"):
        actions.append("investigate_blocking_ledger_findings_before_readiness")
    actions.append("keep_live_and_order_submission_disabled")
    return sorted(set(actions))


def evidence_quality_summary(report: dict[str, Any]) -> dict[str, Any]:
    state = str(report.get("repository_state") or "unknown")
    complete = state == "repository_present_with_valid_events" and not report.get("blocking_findings")
    return {
        "state": state,
        "operational_evidence_complete": bool(complete),
        "has_repository": state not in {"repository_missing", "unknown"},
        "has_schema": state not in {"repository_missing", "repository_empty", "schema_invalid", "event_schema_invalid", "unknown"},
        "has_real_events": int(report.get("order_intents_count") or 0) > 0
        or int(report.get("capital_reservations_count") or 0) > 0
        or int(report.get("order_intent_events_count") or 0) > 0
        or int(report.get("capital_reservation_events_count") or 0) > 0,
        "missing_sources": list(report.get("missing_sources") or []),
        "required_sources_missing": list(report.get("required_sources_missing") or []),
        "optional_sources_missing": list(report.get("optional_sources_missing") or []),
        "next_required_actions": list(report.get("next_required_actions") or []),
    }


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita ledger idempotente de intenção de ordem e reserva de capital paper/shadow."
    )
    parser.add_argument("--repository", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--initialize-empty-repository",
        action="store_true",
        help="Cria somente a estrutura vazia do ledger paper/shadow, sem eventos falsos.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_order_intent_capital_ledger_audit(
        repository_path=args.repository,
        report_path=args.report,
        strict=args.strict,
        initialize_empty_repository=args.initialize_empty_repository,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "missing_data"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
