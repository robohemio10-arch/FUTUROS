from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from smartcrypto.ops.notification_channels import (
    NotificationSettings,
    settings_from_env,
    validate_ntfy_config,
    validate_telegram_config,
)


DEFAULT_ALERT_REPORT_PATH = Path("data/reports/critical_alerting_report.json")
DEFAULT_DISPATCH_REPORT_PATH = Path("data/reports/critical_notification_dispatch_report.json")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
SAFE_TRUE_FLAGS = ("paper_only", "shadow_only")
SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "auth",
    "chat_id",
    "topic",
    "header",
    "cookie",
    "api_key",
    "apikey",
    "credential",
)
OPTIONAL_REPORTS = {"alert_report", "dispatch_report"}


def load_critical_notifications_panel_state(
    *,
    source_paths: Mapping[str, str | Path | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    paths = build_source_paths(source_paths)
    settings = settings_from_env(env or {})
    sources = {
        "alert_report": load_json_source("alert_report", paths["alert_report"]),
        "dispatch_report": load_json_source("dispatch_report", paths["dispatch_report"]),
    }
    alert_payload = dict_or_empty(sources["alert_report"].get("payload"))
    dispatch_payload = dict_or_empty(sources["dispatch_report"].get("payload"))
    channel_settings = summarize_channel_settings(settings)
    alert_summary = summarize_alert_report(alert_payload, sources["alert_report"])
    dispatch_summary = summarize_dispatch_report(dispatch_payload, sources["dispatch_report"])
    safety_flags = collect_safety_flags(alert_payload, dispatch_payload)
    safety_alerts = unsafe_safety_flags(safety_flags)
    blocked_reasons = list(safety_alerts)
    warnings = aggregate_warnings(sources, channel_settings, dispatch_summary, alert_summary)
    status = aggregate_status(blocked_reasons, warnings)
    return {
        "status": status,
        "reason": reason_for(status, blocked_reasons, warnings),
        "generated_at_utc": utc_timestamp(),
        "read_only": True,
        "is_read_only": True,
        "dry_run_only": True,
        "real_dispatch_enabled": False,
        "dashboard_dispatch_enabled": False,
        "forbidden_actions_present": [],
        "sources": sanitize_payload(sources),
        "channel_settings": channel_settings,
        "alert_report": alert_summary,
        "dispatch_report": dispatch_summary,
        "safety_flags": safety_flags,
        "safety_alerts": safety_alerts,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "missing_sources": sorted(name for name, source in sources.items() if source["status"] == "missing"),
        "paper_only": safety_flags["paper_only"],
        "shadow_only": safety_flags["shadow_only"],
        "live_trading_enabled": safety_flags["live_trading_enabled"],
        "live_release_allowed": safety_flags["live_release_allowed"],
        "canary_release_allowed": safety_flags["canary_release_allowed"],
        "order_submission_enabled": safety_flags["order_submission_enabled"],
        "real_order_submission_enabled": safety_flags["real_order_submission_enabled"],
        "exchange_private_access": safety_flags["exchange_private_access"],
        "sends_orders": safety_flags["sends_orders"],
        "changes_risk": safety_flags["changes_risk"],
    }


def build_source_paths(overrides: Mapping[str, str | Path | None] | None) -> dict[str, Path]:
    result: dict[str, Path] = {
        "alert_report": DEFAULT_ALERT_REPORT_PATH,
        "dispatch_report": DEFAULT_DISPATCH_REPORT_PATH,
    }
    for key, value in (overrides or {}).items():
        if key in result and value is not None:
            result[key] = Path(value)
    return result


def load_json_source(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "status": "missing", "payload": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": f"json_read_failed:{type(exc).__name__}:{exc}",
        }
    if not isinstance(payload, dict):
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": "json_root_not_object",
        }
    return {"name": name, "path": str(path), "exists": True, "status": "ok", "payload": payload}


def summarize_channel_settings(settings: NotificationSettings) -> dict[str, Any]:
    ntfy_error = validate_ntfy_config(settings.ntfy) if settings.ntfy.enabled else None
    telegram_error = validate_telegram_config(settings.telegram) if settings.telegram.enabled else None
    return {
        "ntfy": {
            "enabled": bool(settings.ntfy.enabled),
            "configured": bool(settings.ntfy.enabled and ntfy_error is None),
            "validation_error": ntfy_error,
            "server_host": safe_url_host(settings.ntfy.server_url),
            "topic_configured": bool(settings.ntfy.topic.strip()),
            "token_configured": bool(settings.ntfy.token.strip()),
            "basic_auth_configured": bool(settings.ntfy.username.strip() or settings.ntfy.password.strip()),
            "timeout_seconds": float(settings.ntfy.timeout_seconds),
        },
        "telegram": {
            "enabled": bool(settings.telegram.enabled),
            "configured": bool(settings.telegram.enabled and telegram_error is None),
            "validation_error": telegram_error,
            "api_host": safe_url_host(settings.telegram.api_base_url),
            "bot_token_configured": bool(settings.telegram.bot_token.strip()),
            "chat_id_configured": bool(settings.telegram.chat_id.strip()),
            "parse_mode_configured": bool(settings.telegram.parse_mode.strip()),
            "disable_notification": bool(settings.telegram.disable_notification),
            "timeout_seconds": float(settings.telegram.timeout_seconds),
        },
    }


def summarize_alert_report(payload: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    if source.get("status") != "ok":
        return {
            "exists": bool(source.get("exists")),
            "status": source.get("status"),
            "reason": source.get("error") or "missing_alert_report",
            "path": source.get("path"),
        }
    critical_alerts = list_of_dicts(payload.get("critical_alerts"))
    warning_alerts = list_of_dicts(payload.get("warning_alerts"))
    summary = dict_or_empty(payload.get("summary"))
    return {
        "exists": True,
        "status": normalize_status(payload.get("status")),
        "reason": payload.get("reason"),
        "total_events": payload.get("total_events", summary.get("total_events")),
        "critical_alerts_count": len(critical_alerts),
        "warning_alerts_count": len(warning_alerts),
        "latest_event_at_utc": summary.get("latest_event_at_utc") or payload.get("latest_event_at_utc"),
        "top_alerts": [sanitize_alert(row) for row in [*critical_alerts, *warning_alerts][:10]],
    }


def summarize_dispatch_report(payload: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    if source.get("status") != "ok":
        return {
            "exists": bool(source.get("exists")),
            "status": source.get("status"),
            "reason": source.get("error") or "missing_dispatch_report",
            "path": source.get("path"),
            "results": [],
        }
    results = [summarize_delivery_result(row) for row in list_of_dicts(payload.get("results"))]
    return {
        "exists": True,
        "status": normalize_status(payload.get("status")),
        "reason": payload.get("reason"),
        "dispatch_attempted": as_bool(payload.get("dispatch_attempted"), default=False),
        "dry_run": as_bool(payload.get("dry_run"), default=False),
        "results": results,
        "channels_total": len(results),
        "channels_sent": sum(1 for row in results if row.get("status") == "sent"),
        "channels_blocked": sum(1 for row in results if row.get("status") == "blocked"),
        "channels_failed": sum(1 for row in results if row.get("status") == "failed"),
        "channels_disabled": sum(1 for row in results if row.get("status") == "disabled"),
        "message": sanitize_payload(dict_or_empty(payload.get("message"))),
    }


def summarize_delivery_result(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "channel": row.get("channel"),
        "enabled": as_bool(row.get("enabled"), default=False),
        "status": normalize_status(row.get("status")),
        "reason": sanitize_scalar("reason", row.get("reason")),
        "http_status": row.get("http_status"),
        "response_excerpt": sanitize_scalar("response_excerpt", row.get("response_excerpt")),
        "dry_run": as_bool(row.get("dry_run"), default=False),
        "paper_only": as_bool(row.get("paper_only"), default=True),
        "shadow_only": as_bool(row.get("shadow_only"), default=True),
        "live_trading_enabled": as_bool(row.get("live_trading_enabled"), default=False),
        "order_submission_enabled": as_bool(row.get("order_submission_enabled"), default=False),
        "real_order_submission_enabled": as_bool(row.get("real_order_submission_enabled"), default=False),
        "exchange_private_access": as_bool(row.get("exchange_private_access"), default=False),
        "sends_orders": as_bool(row.get("sends_orders"), default=False),
        "changes_risk": as_bool(row.get("changes_risk"), default=False),
    }


def sanitize_alert(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "alert_level": row.get("alert_level"),
        "reason": row.get("reason"),
        "event_type": row.get("event_type"),
        "event_severity": row.get("event_severity"),
        "event_status": row.get("event_status"),
        "symbol": row.get("symbol"),
        "correlation_id": row.get("correlation_id"),
    }


def collect_safety_flags(*payloads: Mapping[str, Any]) -> dict[str, bool]:
    flags = {
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
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for key in SAFE_TRUE_FLAGS:
            if key in payload:
                flags[key] = flags[key] and as_bool(payload.get(key), default=True)
        for key in SAFE_FALSE_FLAGS:
            if key in payload:
                flags[key] = flags[key] or as_bool(payload.get(key), default=False)
        for result in list_of_dicts(payload.get("results")):
            for key in SAFE_TRUE_FLAGS:
                if key in result:
                    flags[key] = flags[key] and as_bool(result.get(key), default=True)
            for key in SAFE_FALSE_FLAGS:
                if key in result:
                    flags[key] = flags[key] or as_bool(result.get(key), default=False)
    return flags


def unsafe_safety_flags(flags: Mapping[str, bool]) -> list[str]:
    alerts: list[str] = []
    for key in SAFE_TRUE_FLAGS:
        if flags.get(key) is not True:
            alerts.append(f"unsafe_flag:{key}_not_true")
    for key in SAFE_FALSE_FLAGS:
        if flags.get(key) is not False:
            alerts.append(f"unsafe_flag:{key}_not_false")
    return alerts


def aggregate_warnings(
    sources: Mapping[str, Mapping[str, Any]],
    channel_settings: Mapping[str, Any],
    dispatch_summary: Mapping[str, Any],
    alert_summary: Mapping[str, Any],
) -> list[str]:
    warnings: list[str] = []
    for name, source in sources.items():
        if source.get("status") == "missing":
            warnings.append(f"missing_source:{name}")
        elif source.get("status") != "ok":
            warnings.append(f"invalid_source:{name}")
    for channel, summary in channel_settings.items():
        if summary.get("enabled") and not summary.get("configured"):
            warnings.append(f"channel_misconfigured:{channel}:{summary.get('validation_error')}")
        elif not summary.get("enabled"):
            warnings.append(f"channel_disabled:{channel}")
    if dispatch_summary.get("exists") and dispatch_summary.get("status") in {"blocked", "failed", "warning"}:
        warnings.append(f"dispatch_status:{dispatch_summary.get('status')}")
    if alert_summary.get("exists") and alert_summary.get("status") in {"blocked", "warning", "missing_data"}:
        warnings.append(f"alert_status:{alert_summary.get('status')}")
    return sorted(set(warnings))


def aggregate_status(blocked_reasons: list[str], warnings: list[str]) -> str:
    if blocked_reasons:
        return "blocked"
    if warnings:
        return "degraded"
    return "ok"


def reason_for(status: str, blocked_reasons: list[str], warnings: list[str]) -> str:
    if status == "ok":
        return "ok"
    return ";".join(blocked_reasons or warnings or ["degraded"])


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): sanitize_payload_for_key(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def sanitize_payload_for_key(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        return redact_value(value)
    if isinstance(value, Mapping):
        return {str(child_key): sanitize_payload_for_key(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [sanitize_payload_for_key(key, item) for item in value]
    return sanitize_scalar(key, value)


def sanitize_scalar(key: str, value: Any) -> Any:
    if value is None:
        return None
    if is_sensitive_key(key):
        return redact_value(value)
    if isinstance(value, str):
        lowered = value.lower()
        if "bot" in lowered and "/sendmessage" in lowered:
            return "[REDACTED_URL]"
        if "authorization" in lowered or "bearer " in lowered or "basic " in lowered:
            return "[REDACTED]"
        if len(value) > 240:
            return value[:240] + "..."
    return value


def redact_value(value: Any) -> str | bool:
    if value is None:
        return False
    if isinstance(value, str) and value == "":
        return False
    return "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def safe_url_host(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except Exception:
        return "invalid_url"
    return parsed.netloc or parsed.path or None


def dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def normalize_status(value: Any) -> str:
    status = str(value or "missing").strip().lower()
    return status or "missing"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def render_critical_notifications_panel(st_module: Any, *, state: dict[str, Any] | None = None) -> None:
    panel_state = state or load_critical_notifications_panel_state()
    st_module.subheader("Notificações críticas — NTFY / Telegram")
    st_module.caption("Painel read-only. Não envia notificações reais, não altera risco e não acessa exchange.")
    if panel_state["status"] == "ok":
        st_module.success("Canais e relatórios sem bloqueios críticos.")
    elif panel_state["status"] == "blocked":
        st_module.error("Painel bloqueado por violação de safety flags.")
    else:
        st_module.warning("Painel em modo degradado: relatório ausente, canal desabilitado ou configuração incompleta.")

    col1, col2, col3, col4 = st_module.columns(4)
    col1.metric("Status", panel_state["status"])
    col2.metric("Read-only", "sim" if panel_state["read_only"] else "não")
    col3.metric("Dry-run only", "sim" if panel_state["dry_run_only"] else "não")
    col4.metric("Envio real", "bloqueado")

    st_module.markdown("### Canais")
    st_module.json(panel_state["channel_settings"])
    st_module.markdown("### Último alerting report")
    st_module.json(panel_state["alert_report"])
    st_module.markdown("### Último dispatch report")
    st_module.json(panel_state["dispatch_report"])
    st_module.markdown("### Safety")
    st_module.json(
        {
            "safety_flags": panel_state["safety_flags"],
            "blocked_reasons": panel_state["blocked_reasons"],
            "warnings": panel_state["warnings"],
            "forbidden_actions_present": panel_state["forbidden_actions_present"],
        }
    )
