from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EVENT_LOG_PATH = Path("data/reports/financial_event_log.jsonl")
EVENT_TYPES = {
    "signal_generated",
    "signal_rejected",
    "risk_approved",
    "risk_rejected",
    "capital_reserved",
    "capital_released",
    "order_intent_created",
    "order_submitted_simulated",
    "order_rejected_simulated",
    "state_divergence_detected",
    "kill_switch_triggered",
    "drift_detected",
    "prediction_stale",
    "market_data_stale",
    "spread_blocked",
    "liquidity_blocked",
    "latency_blocked",
    "backup_failed",
    "restore_failed",
    "model_promotion_blocked",
    "registry_updated_shadow",
    "paper_session_started",
    "paper_session_blocked",
    "reconciliation_required",
}
SEVERITIES = {"info", "warning", "critical", "blocked"}
STATUSES = {"ok", "warning", "blocked", "open", "closed", "acknowledged"}
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


class FinancialEventLogError(ValueError):
    pass


@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    correlation_id: str
    event_type: str
    event_severity: str
    event_status: str
    occurred_at_utc: str
    source: str
    symbol: str | None = None
    side: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    config_version: str | None = None
    risk_mode: str | None = None
    runtime_mode: str = "paper"
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    paper_only: bool = True
    shadow_only: bool = True
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FinancialEventLog:
    def __init__(self, path: str | Path = DEFAULT_EVENT_LOG_PATH) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        event_type: str,
        correlation_id: str,
        event_severity: str = "info",
        event_status: str = "ok",
        occurred_at_utc: str | None = None,
        source: str = "smartcrypto",
        symbol: str | None = None,
        side: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        config_version: str | None = None,
        risk_mode: str | None = None,
        runtime_mode: str = "paper",
        state_before: dict[str, Any] | None = None,
        state_after: dict[str, Any] | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        safety_overrides: dict[str, Any] | None = None,
    ) -> FinancialEvent:
        safety = safety_payload(safety_overrides)
        safety["runtime_mode"] = str(runtime_mode or safety["runtime_mode"])
        event = FinancialEvent(
            event_id=str(uuid.uuid4()),
            correlation_id=str(correlation_id or "").strip(),
            event_type=str(event_type).strip(),
            event_severity=str(event_severity).strip().lower(),
            event_status=str(event_status).strip().lower(),
            occurred_at_utc=occurred_at_utc or utc_timestamp(),
            source=str(source or "smartcrypto"),
            symbol=normalize_text(symbol),
            side=normalize_text(side),
            model_id=normalize_text(model_id),
            model_version=normalize_text(model_version),
            config_version=normalize_text(config_version),
            risk_mode=normalize_text(risk_mode),
            state_before=state_before,
            state_after=state_after,
            reason=normalize_text(reason),
            metadata=dict(metadata or {}),
            **safety,
        )
        validate_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def read_events(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        symbol: str | None = None,
        correlation_id: str | None = None,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = read_event_log(self.path)
        start = parse_utc(start_time) if start_time else None
        end = parse_utc(end_time) if end_time else None
        filtered: list[dict[str, Any]] = []
        for row in rows:
            timestamp = parse_utc(row.get("occurred_at_utc"))
            if event_type and row.get("event_type") != event_type:
                continue
            if severity and row.get("event_severity") != severity:
                continue
            if symbol and row.get("symbol") != symbol:
                continue
            if correlation_id and row.get("correlation_id") != correlation_id:
                continue
            if status and row.get("event_status") != status:
                continue
            if start and (timestamp is None or timestamp < start):
                continue
            if end and (timestamp is None or timestamp > end):
                continue
            filtered.append(row)
        return filtered

    def summary(self) -> dict[str, Any]:
        return summarize_events(self.read_events())


def validate_event(event: FinancialEvent) -> None:
    payload = event.to_dict()
    if event.event_type not in EVENT_TYPES:
        raise FinancialEventLogError(f"invalid_event_type:{event.event_type}")
    if event.event_severity not in SEVERITIES:
        raise FinancialEventLogError(f"invalid_event_severity:{event.event_severity}")
    if event.event_status not in STATUSES:
        raise FinancialEventLogError(f"invalid_event_status:{event.event_status}")
    if not event.correlation_id:
        raise FinancialEventLogError("missing_correlation_id")
    if parse_utc(event.occurred_at_utc) is None:
        raise FinancialEventLogError("invalid_occurred_at_utc")
    unsafe = unsafe_safety_flags(payload)
    if unsafe:
        raise FinancialEventLogError("unsafe_safety_flags:" + ",".join(unsafe))


def read_event_log(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest = max((row.get("occurred_at_utc") for row in events if row.get("occurred_at_utc")), default=None)
    return {
        "total_events": len(events),
        "critical_events": count_where(events, "event_severity", "critical"),
        "warning_events": count_where(events, "event_severity", "warning"),
        "blocked_events": count_where(events, "event_status", "blocked") + count_where(events, "event_severity", "blocked"),
        "latest_event_at_utc": latest,
        "events_by_type": counts(events, "event_type"),
        "events_by_severity": counts(events, "event_severity"),
        "events_by_status": counts(events, "event_status"),
        "events_by_symbol": counts(events, "symbol"),
        "open_incidents": sum(
            1
            for row in events
            if row.get("event_status") == "open" and row.get("event_severity") in {"critical", "blocked"}
        ),
        "correlation_ids_count": len({row.get("correlation_id") for row in events if row.get("correlation_id")}),
    }


def count_where(events: list[dict[str, Any]], key: str, value: str) -> int:
    return sum(1 for row in events if row.get(key) == value)


def counts(events: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in events:
        value = row.get(key)
        if value in (None, ""):
            continue
        result[str(value)] = result.get(str(value), 0) + 1
    return dict(sorted(result.items()))


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in SAFE_FALSE_FLAGS:
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()
