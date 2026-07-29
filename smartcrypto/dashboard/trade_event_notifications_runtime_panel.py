from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.runtime.integrity_traceability_v2 import (
    ConsistentReadError,
    read_json_consistent,
)

DEFAULT_TRADE_EVENT_NOTIFICATIONS_REPORT_PATH = Path(
    "data/reports/trade_event_notifications_report.json"
)

DEFAULT_STALE_AFTER_SECONDS = 120

EXPECTED_FALSE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)

EXPECTED_TRUE_FLAGS = (
    "paper_only",
    "shadow_only",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def load_json_object(path: str | Path) -> dict[str, Any]:
    target = Path(path)

    if not target.exists():
        return {
            "exists": False,
            "status": "missing",
            "path": str(target),
            "payload": {},
            "reason": "report_missing",
        }

    try:
        payload = read_json_consistent(target)
    except ConsistentReadError as exc:
        return {
            "exists": True,
            "status": "invalid",
            "path": str(target),
            "payload": {},
            "reason": f"{type(exc).__name__}: {exc.reason}",
        }

    if not isinstance(payload, dict):
        return {
            "exists": True,
            "status": "invalid",
            "path": str(target),
            "payload": {},
            "reason": "json_root_not_object",
        }

    return {
        "exists": True,
        "status": "ok",
        "path": str(target),
        "payload": payload,
        "reason": "loaded",
    }


def bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_safety_alerts(payload: Mapping[str, Any]) -> list[str]:
    alerts: list[str] = []

    for key in EXPECTED_FALSE_FLAGS:
        if bool_or_none(payload.get(key)) is not False:
            alerts.append(f"unsafe_flag:{key}_not_false")

    for key in EXPECTED_TRUE_FLAGS:
        if bool_or_none(payload.get(key)) is not True:
            alerts.append(f"unsafe_flag:{key}_not_true")

    return sorted(set(alerts))


def collect_runtime_alerts(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> tuple[list[str], dict[str, Any]]:
    current = now or utc_now()
    alerts: list[str] = []
    metrics: dict[str, Any] = {}

    created_at = parse_utc_datetime(payload.get("created_at"))
    if created_at is None:
        alerts.append("report_created_at_missing_or_invalid")
        metrics["report_age_seconds"] = None
        metrics["report_created_at"] = None
    else:
        age_seconds = max(0.0, (current - created_at).total_seconds())
        metrics["report_age_seconds"] = round(age_seconds, 3)
        metrics["report_created_at"] = created_at.isoformat()
        if age_seconds > stale_after_seconds:
            alerts.append("report_stale")

    if payload.get("status") != "ok":
        alerts.append("daemon_status_not_ok")

    if payload.get("daemon") is not True:
        alerts.append("daemon_not_true")

    if payload.get("dry_run") is not False:
        alerts.append("dry_run_not_false")

    if str(payload.get("channels") or "").lower() != "all":
        alerts.append("channels_not_all")

    events_pending = int_or_none(payload.get("events_pending"))
    if events_pending is None:
        alerts.append("events_pending_missing_or_invalid")
    elif events_pending > 0:
        alerts.append("events_pending_positive")

    for key in ("events_detected", "events_dispatched", "events_marked_sent", "daemon_iteration"):
        metrics[key] = int_or_none(payload.get(key))

    metrics["status"] = payload.get("status")
    metrics["reason"] = payload.get("reason")
    metrics["daemon"] = payload.get("daemon")
    metrics["dry_run"] = payload.get("dry_run")
    metrics["channels"] = payload.get("channels")
    metrics["events_pending"] = events_pending

    return sorted(set(alerts)), metrics


def summarize_trade_event_notifications_runtime(
    *,
    report_path: str | Path = DEFAULT_TRADE_EVENT_NOTIFICATIONS_REPORT_PATH,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    source = load_json_object(report_path)
    payload = source.get("payload") or {}

    base = {
        "generated_at_utc": utc_now().isoformat(),
        "report_path": source.get("path"),
        "report_exists": bool(source.get("exists")),
        "report_load_status": source.get("status"),
        "report_load_reason": source.get("reason"),
        "stale_after_seconds": int(stale_after_seconds),
        "forbidden_actions_present": [],
    }

    if source.get("status") != "ok":
        return {
            **base,
            "status": "blocked",
            "reason": source.get("reason") or "report_unavailable",
            "alerts": [str(source.get("reason") or "report_unavailable")],
            "warnings": [],
            "metrics": {},
            "payload": {},
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
        }

    runtime_alerts, metrics = collect_runtime_alerts(
        payload,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    safety_alerts = collect_safety_alerts(payload)

    alerts = sorted(set(runtime_alerts + safety_alerts))

    if safety_alerts:
        status = "blocked"
    elif runtime_alerts:
        status = "degraded"
    else:
        status = "ok"

    return {
        **base,
        "status": status,
        "reason": ";".join(alerts or ["ok"]),
        "alerts": alerts,
        "warnings": runtime_alerts,
        "metrics": metrics,
        "payload": {
            "created_at": payload.get("created_at"),
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "daemon": payload.get("daemon"),
            "daemon_iteration": payload.get("daemon_iteration"),
            "dry_run": payload.get("dry_run"),
            "channels": payload.get("channels"),
            "events_detected": payload.get("events_detected"),
            "events_pending": payload.get("events_pending"),
            "events_dispatched": payload.get("events_dispatched"),
            "events_marked_sent": payload.get("events_marked_sent"),
            "paper_only": payload.get("paper_only"),
            "shadow_only": payload.get("shadow_only"),
            "live_trading_enabled": payload.get("live_trading_enabled"),
            "live_release_allowed": payload.get("live_release_allowed"),
            "canary_release_allowed": payload.get("canary_release_allowed"),
            "order_submission_enabled": payload.get("order_submission_enabled"),
            "real_order_submission_enabled": payload.get("real_order_submission_enabled"),
            "exchange_private_access": payload.get("exchange_private_access"),
            "sends_orders": payload.get("sends_orders"),
            "changes_risk": payload.get("changes_risk"),
        },
        "paper_only": payload.get("paper_only") is True,
        "shadow_only": payload.get("shadow_only") is True,
        "live_trading_enabled": payload.get("live_trading_enabled") is True,
        "live_release_allowed": payload.get("live_release_allowed") is True,
        "canary_release_allowed": payload.get("canary_release_allowed") is True,
        "order_submission_enabled": payload.get("order_submission_enabled") is True,
        "real_order_submission_enabled": payload.get("real_order_submission_enabled") is True,
        "exchange_private_access": payload.get("exchange_private_access") is True,
        "sends_orders": payload.get("sends_orders") is True,
        "changes_risk": payload.get("changes_risk") is True,
    }


def render_status_banner(st_module: Any, state: Mapping[str, Any]) -> None:
    status = str(state.get("status") or "unknown")
    reason = str(state.get("reason") or "unknown")

    if status == "ok":
        st_module.success("Daemon de notificações de trade operacional.")
    elif status == "blocked":
        st_module.error(f"Daemon bloqueado: {reason}")
    else:
        st_module.warning(f"Daemon degradado: {reason}")


def render_trade_event_notifications_runtime_panel(
    st_module: Any,
    *,
    report_path: str | Path = DEFAULT_TRADE_EVENT_NOTIFICATIONS_REPORT_PATH,
    state: dict[str, Any] | None = None,
) -> None:
    current_state = state or summarize_trade_event_notifications_runtime(report_path=report_path)
    metrics = current_state.get("metrics") or {}

    st_module.subheader("Trade notifications — Runtime")
    st_module.caption(
        "Monitor read-only do serviço trade-event-notifications-paper. "
        "Não envia ordens, não altera risco e não acessa exchange privada."
    )

    render_status_banner(st_module, current_state)

    cols = st_module.columns(4)
    cols[0].metric("Status", current_state.get("status"))
    cols[1].metric("Daemon", str(metrics.get("daemon")).lower())
    cols[2].metric("Iteração", metrics.get("daemon_iteration"))
    cols[3].metric("Idade report (s)", metrics.get("report_age_seconds"))

    cols = st_module.columns(4)
    cols[0].metric("Channels", metrics.get("channels"))
    cols[1].metric("Pending", metrics.get("events_pending"))
    cols[2].metric("Dispatched", metrics.get("events_dispatched"))
    cols[3].metric("Marked sent", metrics.get("events_marked_sent"))

    st_module.markdown("### Alertas")
    st_module.json(
        {
            "status": current_state.get("status"),
            "reason": current_state.get("reason"),
            "alerts": current_state.get("alerts"),
            "warnings": current_state.get("warnings"),
            "report_path": current_state.get("report_path"),
            "report_load_status": current_state.get("report_load_status"),
            "stale_after_seconds": current_state.get("stale_after_seconds"),
        }
    )

    st_module.markdown("### Safety flags")
    st_module.json(
        {
            "paper_only": current_state.get("paper_only"),
            "shadow_only": current_state.get("shadow_only"),
            "live_trading_enabled": current_state.get("live_trading_enabled"),
            "live_release_allowed": current_state.get("live_release_allowed"),
            "canary_release_allowed": current_state.get("canary_release_allowed"),
            "order_submission_enabled": current_state.get("order_submission_enabled"),
            "real_order_submission_enabled": current_state.get("real_order_submission_enabled"),
            "exchange_private_access": current_state.get("exchange_private_access"),
            "sends_orders": current_state.get("sends_orders"),
            "changes_risk": current_state.get("changes_risk"),
        }
    )

    st_module.markdown("### Último report sanitizado")
    st_module.json(current_state.get("payload") or {})
