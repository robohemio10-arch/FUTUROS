from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from smartcrypto.ops.notification_channels import (
    NotificationDispatcher,
    NotificationMessage,
    settings_from_env,
    validate_ntfy_config,
    validate_telegram_config,
    write_dispatch_report,
)

DEFAULT_MANUAL_DISPATCH_REPORT_PATH = Path("data/reports/manual_notification_test_dispatch_report.json")
CONFIRMATION_TEXT = "ENVIAR TESTE"

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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def base_safety_flags() -> dict[str, bool]:
    return {
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


def is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").lower()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def safe_url_host(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return "configured"
    return parsed.netloc or "configured"


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        redacted = False
        for key, item in value.items():
            text_key = str(key)
            if is_sensitive_key(text_key):
                redacted = True
                continue
            output[text_key] = sanitize_payload(item)
        if redacted:
            output["redacted_sensitive_fields"] = True
        return output
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str) and "/bot" in value and "telegram" in value.lower():
        return "<redacted_url>"
    if isinstance(value, str) and "Bearer " in value:
        return "<redacted>"
    return value


def sanitize_result(row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "channel",
        "enabled",
        "status",
        "reason",
        "http_status",
        "response_excerpt",
        "dry_run",
        "paper_only",
        "shadow_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    )
    return {key: sanitize_payload(row.get(key)) for key in allowed if key in row}


def summarize_channel_settings(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    settings = settings_from_env(env if env is not None else os.environ)
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


def load_json_report(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"exists": False, "status": "missing", "path": str(target), "payload": {}}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "status": "invalid", "path": str(target), "payload": {}, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"exists": True, "status": "invalid", "path": str(target), "payload": {}, "error": "json_root_not_object"}
    return {"exists": True, "status": "ok", "path": str(target), "payload": payload}


def summarize_dispatch_report(report: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(report.get("payload") or {})
    if report.get("status") != "ok":
        return {
            "exists": bool(report.get("exists")),
            "status": report.get("status"),
            "reason": report.get("error") or "dispatch_report_missing",
            "path": report.get("path"),
            "results": [],
        }

    results = [sanitize_result(row) for row in payload.get("results", []) if isinstance(row, Mapping)]
    return {
        "exists": True,
        "status": str(payload.get("status") or "unknown"),
        "reason": sanitize_payload(payload.get("reason")),
        "path": report.get("path"),
        "dispatch_attempted": bool(payload.get("dispatch_attempted", False)),
        "dry_run": bool(payload.get("dry_run", False)),
        "created_at": payload.get("created_at"),
        "results": results,
        "channels_total": len(results),
        "channels_sent": sum(1 for row in results if row.get("status") == "sent"),
        "channels_blocked": sum(1 for row in results if row.get("status") == "blocked"),
        "channels_failed": sum(1 for row in results if row.get("status") == "failed"),
        "channels_disabled": sum(1 for row in results if row.get("status") == "disabled"),
        "message": sanitize_payload(payload.get("message") or {}),
    }


def collect_safety_alerts(payload: Mapping[str, Any]) -> list[str]:
    serialized = json.dumps(payload, sort_keys=True)
    alerts: list[str] = []
    for key in ("live_trading_enabled", "live_release_allowed", "canary_release_allowed", "order_submission_enabled", "real_order_submission_enabled", "exchange_private_access", "sends_orders", "changes_risk"):
        if f'"{key}": true' in serialized.lower():
            alerts.append(f"unsafe_flag:{key}_not_false")
    for key in ("paper_only", "shadow_only"):
        if f'"{key}": false' in serialized.lower():
            alerts.append(f"unsafe_flag:{key}_not_true")
    return sorted(set(alerts))


def load_notification_channels_test_panel_state(
    *,
    env: Mapping[str, str] | None = None,
    dispatch_report_path: str | Path = DEFAULT_MANUAL_DISPATCH_REPORT_PATH,
) -> dict[str, Any]:
    channel_settings = summarize_channel_settings(env)
    dispatch_source = load_json_report(dispatch_report_path)
    dispatch_summary = summarize_dispatch_report(dispatch_source)

    warnings: list[str] = []
    blocked_reasons = collect_safety_alerts(dispatch_source.get("payload") or {})

    if not channel_settings["ntfy"]["enabled"]:
        warnings.append("channel_disabled:ntfy")
    elif not channel_settings["ntfy"]["configured"]:
        warnings.append(f"channel_misconfigured:ntfy:{channel_settings['ntfy']['validation_error']}")

    if not channel_settings["telegram"]["enabled"]:
        warnings.append("channel_disabled:telegram")
    elif not channel_settings["telegram"]["configured"]:
        warnings.append(f"channel_misconfigured:telegram:{channel_settings['telegram']['validation_error']}")

    if dispatch_source.get("status") == "invalid":
        warnings.append("last_dispatch_report_invalid")

    configured_channels = sum(1 for item in channel_settings.values() if item.get("enabled") and item.get("configured"))

    if blocked_reasons:
        status = "blocked"
    elif configured_channels == 0 or warnings:
        status = "degraded"
    else:
        status = "ok"

    safety_flags = base_safety_flags()
    return {
        "status": status,
        "reason": ";".join(blocked_reasons or warnings or ["ok"]),
        "generated_at_utc": utc_now(),
        "dry_run_supported": True,
        "manual_real_dispatch_supported": True,
        "manual_real_dispatch_requires_confirmation": True,
        "confirmation_text": CONFIRMATION_TEXT,
        "configured_channels": configured_channels,
        "channel_settings": channel_settings,
        "last_dispatch_report": dispatch_summary,
        "blocked_reasons": sorted(set(blocked_reasons)),
        "warnings": sorted(set(warnings)),
        "forbidden_actions_present": [],
        "safety_flags": safety_flags,
        **safety_flags,
    }


def build_manual_test_message(*, now: datetime | None = None) -> NotificationMessage:
    current = now or datetime.now(timezone.utc)
    return NotificationMessage(
        title="FUTUROS manual notification test",
        body=(
            "Manual notification test from FUTUROS paper/shadow runtime.\n"
            "No orders sent. No risk changes. Live/canary disabled.\n"
            f"utc={current.isoformat()}"
        ),
        priority="default",
        tags=("test",),
        event_type="manual_dashboard_test",
        severity="info",
    ).normalized()


def aggregate_dispatch_status(results: list[dict[str, Any]]) -> tuple[str, str]:
    failed_or_blocked = [row for row in results if row.get("status") in {"failed", "blocked"}]
    sent_count = sum(1 for row in results if row.get("status") == "sent")
    enabled_count = sum(1 for row in results if row.get("enabled") is True)

    if failed_or_blocked:
        return "blocked", ";".join(sorted({str(row.get("reason") or "failed") for row in failed_or_blocked}))
    if sent_count:
        return "ok", "sent"
    if enabled_count == 0:
        return "disabled", "all_channels_disabled"
    return "warning", "no_channel_sent"


def dispatch_manual_notification_test(
    *,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    output_path: str | Path = DEFAULT_MANUAL_DISPATCH_REPORT_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = settings_from_env(env if env is not None else os.environ)
    message = build_manual_test_message(now=now)
    dispatcher = NotificationDispatcher(settings)
    results = [result.to_dict() for result in dispatcher.send(message, dry_run=bool(dry_run))]
    status, reason = aggregate_dispatch_status(results)

    payload = {
        "status": status,
        "reason": reason,
        "dispatch_attempted": True,
        "dry_run": bool(dry_run),
        "created_at": utc_now(),
        "message": {
            "title": message.title,
            "event_type": message.event_type,
            "severity": message.severity,
        },
        "results": [sanitize_result(row) for row in results],
        **base_safety_flags(),
    }
    write_dispatch_report(output_path, payload)
    return payload


def render_status_banner(st_module: Any, status: str, reason: str) -> None:
    if status == "ok":
        st_module.success("Canais NTFY/Telegram operacionais para teste controlado.")
    elif status == "blocked":
        st_module.error(f"Painel bloqueado por segurança: {reason}")
    else:
        st_module.warning(f"Painel degradado: {reason}")


def render_notification_channels_test_panel(
    st_module: Any,
    *,
    state: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    current_state = state or load_notification_channels_test_panel_state(env=env)

    st_module.subheader("NTFY / Telegram — Testes controlados")
    st_module.caption("Painel paper/shadow para validar canais. Não envia ordens, não altera risco e não acessa exchange privada.")
    render_status_banner(st_module, str(current_state["status"]), str(current_state["reason"]))

    cols = st_module.columns(4)
    cols[0].metric("Status", current_state["status"])
    cols[1].metric("Canais configurados", current_state["configured_channels"])
    cols[2].metric("sends_orders", str(current_state["sends_orders"]).lower())
    cols[3].metric("changes_risk", str(current_state["changes_risk"]).lower())

    st_module.markdown("### Configuração sanitizada")
    st_module.json(
        {
            "ntfy": current_state["channel_settings"]["ntfy"],
            "telegram": current_state["channel_settings"]["telegram"],
            "warnings": current_state["warnings"],
            "blocked_reasons": current_state["blocked_reasons"],
            "safety_flags": current_state["safety_flags"],
        }
    )

    st_module.markdown("### Último dispatch manual")
    st_module.json(current_state["last_dispatch_report"])

    if not hasattr(st_module, "button"):
        return

    if st_module.button("Executar dry-run NTFY/Telegram"):
        result = dispatch_manual_notification_test(dry_run=True, env=env)
        st_module.success("Dry-run executado.")
        st_module.json(sanitize_payload(result))

    confirmation = ""
    if hasattr(st_module, "text_input"):
        confirmation = str(st_module.text_input(f"Digite {CONFIRMATION_TEXT} para liberar envio real manual", value="") or "").strip()

    if st_module.button("Enviar teste real NTFY/Telegram"):
        if confirmation != CONFIRMATION_TEXT:
            st_module.warning("Envio real bloqueado: confirmação textual inválida.")
            return
        result = dispatch_manual_notification_test(dry_run=False, env=env)
        if result.get("status") == "ok":
            st_module.success("Teste real enviado.")
        else:
            st_module.warning(f"Teste real concluído com status={result.get('status')}.")
        st_module.json(sanitize_payload(result))
