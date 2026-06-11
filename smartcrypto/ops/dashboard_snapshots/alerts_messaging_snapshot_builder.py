from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    age_seconds,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    iso_utc,
    load_page_sources,
    parse_timestamp,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import calculate_backoff_seconds, safe_div, safe_mean


ROUTING_POLICY = {
    "INFO": ["log"],
    "WARNING": ["telegram"],
    "CRITICAL": ["telegram", "ntfy"],
    "PANIC": ["telegram", "ntfy", "operator_required"],
}
SEVERITY_SCORES = {"INFO": 10, "WARNING": 30, "CRITICAL": 70, "PANIC": 100}
DELIVERED_STATUSES = {"delivered", "sent"}

REQUIRED_SECTIONS = (
    "dispatcher_status",
    "channels",
    "queue",
    "severity_breakdown",
    "critical_events",
    "retry_backoff",
    "routing_policy",
    "messaging_audit",
    "audit",
)


def calculate_delivery_metrics(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(alert.get("status", "pending")).lower() for alert in alerts)
    delivered = statuses["delivered"]
    sent = statuses["sent"]
    failed = statuses["failed"]
    dead_letter = statuses["dead_letter"]
    total_attempted = sent + delivered + failed + dead_letter
    pending = statuses["pending"] + statuses["retry"]
    critical_undelivered = sum(
        str(alert.get("severity", "INFO")).upper() in {"CRITICAL", "PANIC"}
        and str(alert.get("status", "pending")).lower() not in DELIVERED_STATUSES
        for alert in alerts
    )
    return {
        "sent_count": sent,
        "delivered_count": delivered,
        "failed_count": failed,
        "dead_letter_count": dead_letter,
        "total_attempted": total_attempted,
        "success_rate_pct": safe_div(delivered, total_attempted) * 100.0,
        "failure_rate_pct": safe_div(failed, total_attempted) * 100.0,
        "pending_count": pending,
        "critical_undelivered_count": critical_undelivered,
    }


def calculate_retry_state(
    *,
    retry_count: int,
    max_retries: int,
    base_backoff_seconds: float,
    max_backoff_seconds: float,
    last_attempt_at: Any = None,
    status: str = "pending",
) -> dict[str, Any]:
    backoff = calculate_backoff_seconds(base_backoff_seconds, retry_count, max_backoff_seconds)
    attempted = parse_timestamp(last_attempt_at)
    exhausted = retry_count >= max_retries
    return {
        "retry_count": retry_count,
        "max_retries": max_retries,
        "current_backoff_seconds": backoff,
        "next_retry_at": iso_utc(attempted + timedelta(seconds=backoff)) if attempted else None,
        "retry_exhausted": exhausted,
        "dead_letter": exhausted and status.lower() != "delivered",
    }


def build_alerts_messaging_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.alerts_messaging)
    alerts = records(first_payload(sources, "alert_outbox"))
    delivery_history = records(first_payload(sources, "notification_delivery_history"))
    dispatcher = first_payload(sources, "notification_dispatcher_report")
    metrics = calculate_delivery_metrics(alerts or delivery_history)
    heartbeat = first_value(dispatcher, ("last_heartbeat_utc", "heartbeat_utc", "generated_at_utc"))
    heartbeat_age = age_seconds(context.now_utc, heartbeat)
    max_heartbeat_age = finite_float(first_value(dispatcher, ("max_heartbeat_age_seconds",)), 120.0) or 120.0
    online = heartbeat_age is not None and heartbeat_age <= max_heartbeat_age
    severity_breakdown = Counter(str(alert.get("severity", "INFO")).upper() for alert in alerts)
    channel_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in delivery_history:
        channel_rows[str(row.get("channel", "unknown"))].append(row)
    channels: dict[str, Any] = {}
    for channel, rows in channel_rows.items():
        attempted = len(rows)
        delivered = sum(str(row.get("status", "")).lower() in DELIVERED_STATUSES for row in rows)
        latencies = [value for row in rows if (value := finite_float(row.get("latency_ms"))) is not None]
        channels[channel] = {"attempted": attempted, "delivered": delivered, "success_rate_pct": safe_div(delivered, attempted) * 100.0, "avg_latency_ms": safe_mean(latencies)}
    max_critical_seconds = finite_float(first_value(dispatcher, ("max_critical_delivery_seconds",)), 60.0) or 60.0
    breaches = [
        alert
        for alert in alerts
        if str(alert.get("severity", "INFO")).upper() in {"CRITICAL", "PANIC"}
        and str(alert.get("status", "pending")).lower() not in DELIVERED_STATUSES
        and (age_seconds(context.now_utc, alert.get("created_at_utc", alert.get("created_at"))) or 0.0) > max_critical_seconds
    ]
    center_status = DashboardSectionStatus.BLOCKED if breaches else DashboardSectionStatus.WARNING if metrics["pending_count"] else DashboardSectionStatus.OK
    retry_count = int(first_value(dispatcher, ("retry_count",), 0) or 0)
    retry_state = calculate_retry_state(
        retry_count=retry_count,
        max_retries=int(first_value(dispatcher, ("max_retries",), 3) or 3),
        base_backoff_seconds=finite_float(first_value(dispatcher, ("base_backoff_seconds",)), 5.0) or 5.0,
        max_backoff_seconds=finite_float(first_value(dispatcher, ("max_backoff_seconds",)), 300.0) or 300.0,
        last_attempt_at=first_value(dispatcher, ("last_attempt_at", "last_attempt_at_utc")),
        status=str(first_value(dispatcher, ("status",), "pending")),
    )
    sections = {
        "dispatcher_status": section(DashboardSectionStatus.OK if online else DashboardSectionStatus.UNKNOWN, dispatcher_status="ONLINE" if online else "OFFLINE", last_heartbeat_age_seconds=heartbeat_age, max_heartbeat_age_seconds=max_heartbeat_age),
        "channels": section(DashboardSectionStatus.OK if channels else DashboardSectionStatus.UNKNOWN, channels=channels),
        "queue": section(center_status, alerts=alerts, **metrics),
        "severity_breakdown": section(DashboardSectionStatus.OK, counts=dict(severity_breakdown), scores=SEVERITY_SCORES),
        "critical_events": section(DashboardSectionStatus.BLOCKED if breaches else DashboardSectionStatus.OK, critical_delivery_breach_count=len(breaches), events=breaches),
        "retry_backoff": section(DashboardSectionStatus.WARNING if retry_state["retry_exhausted"] else DashboardSectionStatus.OK, **retry_state),
        "routing_policy": section(DashboardSectionStatus.OK, routes=ROUTING_POLICY, dispatch_enabled=False),
        "messaging_audit": section(DashboardSectionStatus.OK, sends_messages=False, reads_tokens=False, network_calls=False),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.alerts_messaging,
        schema_version=DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )
