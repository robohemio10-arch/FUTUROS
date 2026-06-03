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


def run_order_intent_capital_ledger_audit(
    *,
    repository_path: str | Path = DEFAULT_LEDGER_PATH,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    repository = Path(repository_path)
    report = base_report(repository)
    if not repository.exists():
        report["status"] = "missing_data"
        report["reason"] = "missing_repository"
        report["reconciliation_required"] = True
        report["recommended_mode"] = "RECONCILING"
        report["blocking_findings"].append(f"missing_repository:{repository}")
        write_report(report, report_path)
        return report

    ensure_schema(repository)
    with connect(repository) as connection:
        order_intents = fetch_all(connection, "order_intents")
        reservations = fetch_all(connection, "capital_reservations")
        order_events = fetch_all(connection, "order_intent_events")

    report["order_intents_count"] = len(order_intents)
    report["capital_reservations_count"] = len(reservations)
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
    report["recommended_mode"] = recommended_mode(report)
    if report["blocking_findings"]:
        report["status"] = "blocked"
        report["reason"] = "ledger_audit_blocked"
    elif report["warnings"]:
        report["status"] = "warning"
        report["reason"] = "ledger_audit_warnings"
    else:
        report["status"] = "ok"
        report["reason"] = "ledger_audit_ok"
    write_report(report, report_path)
    return report


def base_report(repository: Path) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": None,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repository_path": str(repository),
        "order_intents_count": 0,
        "capital_reservations_count": 0,
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
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def fetch_all(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_order_intent_capital_ledger_audit(
        repository_path=args.repository,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "missing_data"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
