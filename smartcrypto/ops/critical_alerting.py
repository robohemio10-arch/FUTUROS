from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.financial_event_log import (
    DEFAULT_EVENT_LOG_PATH,
    read_event_log,
    safety_payload,
    summarize_events,
    unsafe_safety_flags,
)


DEFAULT_ALERT_REPORT_PATH = Path("data/reports/critical_alerting_report.json")
CRITICAL_EVENT_TYPES = {
    "kill_switch_triggered",
    "state_divergence_detected",
    "backup_failed",
    "restore_failed",
    "spread_blocked",
    "liquidity_blocked",
    "latency_blocked",
    "reconciliation_required",
}
MARKET_DATA_CRITICAL_TYPES = {"market_data_stale"}


def build_critical_alerting_report(
    *,
    event_log_path: str | Path = DEFAULT_EVENT_LOG_PATH,
    report_path: str | Path | None = DEFAULT_ALERT_REPORT_PATH,
    max_risk_rejections: int = 5,
    max_prediction_stale: int = 3,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_log = Path(event_log_path)
    safety = safety_payload(safety_overrides)
    validation_errors = [f"unsafe_safety_flag:{item}" for item in unsafe_safety_flags(safety)]
    if not event_log.exists():
        status = "blocked" if strict else "missing_data"
        report = {
            "status": status,
            "reason": "missing_event_log",
            "generated_at_utc": utc_timestamp(),
            "event_log_path": str(event_log),
            "total_events": 0,
            "alerts": [],
            "critical_alerts": [],
            "warning_alerts": [],
            "blocked_findings": validation_errors + (["missing_event_log"] if strict else []),
            "summary": summarize_events([]),
            **safety,
        }
        write_json_if_requested(report, Path(report_path) if report_path is not None else None)
        return report

    events = read_event_log(event_log)
    summary = summarize_events(events)
    alerts = classify_alerts(
        events,
        max_risk_rejections=int(max_risk_rejections),
        max_prediction_stale=int(max_prediction_stale),
    )
    critical_alerts = [alert for alert in alerts if alert["alert_level"] in {"critical", "blocked"}]
    warning_alerts = [alert for alert in alerts if alert["alert_level"] == "warning"]
    blocked_findings = validation_errors + [alert["reason"] for alert in critical_alerts]
    status = "blocked" if blocked_findings else "warning" if warning_alerts else "ok"
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(blocked_findings or [alert["reason"] for alert in warning_alerts]))),
        "generated_at_utc": utc_timestamp(),
        "event_log_path": str(event_log),
        "total_events": len(events),
        "alerts": alerts,
        "critical_alerts": critical_alerts,
        "warning_alerts": warning_alerts,
        "blocked_findings": sorted(set(blocked_findings)),
        "summary": summary,
        **safety,
    }
    write_json_if_requested(report, Path(report_path) if report_path is not None else None)
    return report


def classify_alerts(
    events: list[dict[str, Any]],
    *,
    max_risk_rejections: int,
    max_prediction_stale: int,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        severity = event.get("event_severity")
        if event_type in CRITICAL_EVENT_TYPES:
            alerts.append(alert(event, "critical", f"{event_type}_critical"))
        elif event_type in MARKET_DATA_CRITICAL_TYPES and severity in {"critical", "blocked"}:
            alerts.append(alert(event, "critical", "market_data_stale_critical"))
        elif has_unsafe_flags(event):
            alerts.append(alert(event, "blocked", "unsafe_safety_flags"))
        elif severity == "warning":
            alerts.append(alert(event, "warning", f"{event_type}_warning"))

    risk_rejected_count = count_type(events, "risk_rejected")
    if risk_rejected_count > max_risk_rejections:
        alerts.append(
            {
                "alert_level": "critical",
                "reason": "repeated_risk_rejected",
                "event_type": "risk_rejected",
                "count": risk_rejected_count,
                "threshold": int(max_risk_rejections),
            }
        )
    prediction_stale_count = count_type(events, "prediction_stale")
    if prediction_stale_count > max_prediction_stale:
        alerts.append(
            {
                "alert_level": "critical",
                "reason": "repeated_prediction_stale",
                "event_type": "prediction_stale",
                "count": prediction_stale_count,
                "threshold": int(max_prediction_stale),
            }
        )
    return alerts


def alert(event: dict[str, Any], level: str, reason: str) -> dict[str, Any]:
    return {
        "alert_level": level,
        "reason": reason,
        "event_id": event.get("event_id"),
        "correlation_id": event.get("correlation_id"),
        "event_type": event.get("event_type"),
        "event_severity": event.get("event_severity"),
        "event_status": event.get("event_status"),
        "occurred_at_utc": event.get("occurred_at_utc"),
        "symbol": event.get("symbol"),
    }


def count_type(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


def has_unsafe_flags(event: dict[str, Any]) -> bool:
    return bool(unsafe_safety_flags(event))


def write_json_if_requested(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
