from __future__ import annotations

import json
from pathlib import Path

from smartcrypto.dashboard.critical_notifications_panel import (
    load_critical_notifications_panel_state,
    render_critical_notifications_panel,
    sanitize_payload,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeStreamlit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def subheader(self, value: str) -> None:
        self.calls.append(("subheader", value))

    def caption(self, value: str) -> None:
        self.calls.append(("caption", value))

    def success(self, value: str) -> None:
        self.calls.append(("success", value))

    def warning(self, value: str) -> None:
        self.calls.append(("warning", value))

    def error(self, value: str) -> None:
        self.calls.append(("error", value))

    def columns(self, count: int) -> list["FakeStreamlit"]:
        self.calls.append(("columns", count))
        return [self for _ in range(count)]

    def metric(self, label: str, value: object) -> None:
        self.calls.append(("metric", {"label": label, "value": value}))

    def markdown(self, value: str) -> None:
        self.calls.append(("markdown", value))

    def json(self, value: object) -> None:
        self.calls.append(("json", value))


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def source_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "alert_report": tmp_path / "critical_alerting_report.json",
        "dispatch_report": tmp_path / "critical_notification_dispatch_report.json",
    }


def alert_report(**overrides) -> dict:
    payload = {
        "status": "blocked",
        "reason": "kill_switch_triggered_critical",
        "total_events": 2,
        "critical_alerts": [
            {
                "alert_level": "critical",
                "reason": "kill_switch_triggered_critical",
                "event_type": "kill_switch_triggered",
                "event_severity": "critical",
                "event_status": "open",
                "correlation_id": "corr-1",
                "symbol": "BTC/USDT:USDT",
            }
        ],
        "warning_alerts": [],
        "summary": {"total_events": 2, "latest_event_at_utc": "2026-06-09T00:00:00Z"},
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    payload.update(overrides)
    return payload


def dispatch_report(**overrides) -> dict:
    payload = {
        "status": "ok",
        "reason": "sent",
        "dispatch_attempted": True,
        "dry_run": True,
        "message": {
            "title": "FUTUROS alerting: blocked",
            "body": "status=blocked",
            "severity": "critical",
        },
        "results": [
            {
                "channel": "ntfy",
                "enabled": True,
                "status": "sent",
                "reason": "dry_run",
                "dry_run": True,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "changes_risk": False,
            },
            {
                "channel": "telegram",
                "enabled": True,
                "status": "sent",
                "reason": "dry_run",
                "dry_run": True,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "changes_risk": False,
            },
        ],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    payload.update(overrides)
    return payload


def secure_env() -> dict[str, str]:
    return {
        "SMARTCRYPTO_NTFY_ENABLED": "true",
        "SMARTCRYPTO_NTFY_TOPIC": "secret-topic-prod",
        "SMARTCRYPTO_NTFY_TOKEN": "tk_super_secret",
        "SMARTCRYPTO_TELEGRAM_ENABLED": "true",
        "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "123456:SECRET_TOKEN",
        "SMARTCRYPTO_TELEGRAM_CHAT_ID": "987654321",
    }


def write_reports(tmp_path: Path, **overrides) -> dict[str, Path]:
    paths = source_paths(tmp_path)
    write_json(paths["alert_report"], overrides.get("alert_report", alert_report()))
    write_json(paths["dispatch_report"], overrides.get("dispatch_report", dispatch_report()))
    return paths


def test_panel_handles_missing_reports_as_degraded_read_only(tmp_path: Path) -> None:
    state = load_critical_notifications_panel_state(source_paths=source_paths(tmp_path), env={})

    assert state["status"] == "degraded"
    assert state["read_only"] is True
    assert state["dry_run_only"] is True
    assert state["real_dispatch_enabled"] is False
    assert state["sends_orders"] is False
    assert state["changes_risk"] is False
    assert "alert_report" in state["missing_sources"]


def test_panel_reads_reports_and_keeps_safety_flags_false(tmp_path: Path) -> None:
    paths = write_reports(tmp_path)
    state = load_critical_notifications_panel_state(source_paths=paths, env=secure_env())

    assert state["safety_flags"]["paper_only"] is True
    assert state["safety_flags"]["shadow_only"] is True
    assert state["safety_flags"]["sends_orders"] is False
    assert state["safety_flags"]["changes_risk"] is False
    assert state["dispatch_report"]["dry_run"] is True
    assert state["dispatch_report"]["channels_sent"] == 2


def test_panel_does_not_expose_ntfy_or_telegram_secrets(tmp_path: Path) -> None:
    paths = write_reports(
        tmp_path,
        dispatch_report=dispatch_report(
            message={
                "title": "secret",
                "topic": "secret-topic-prod",
                "chat_id": "987654321",
                "bot_token": "123456:SECRET_TOKEN",
                "url": "https://api.telegram.org/bot123456:SECRET_TOKEN/sendMessage",
                "headers": {"Authorization": "Bearer tk_super_secret"},
            }
        ),
    )
    state = load_critical_notifications_panel_state(source_paths=paths, env=secure_env())
    serialized = json.dumps(state, sort_keys=True)

    assert "secret-topic-prod" not in serialized
    assert "tk_super_secret" not in serialized
    assert "123456:SECRET_TOKEN" not in serialized
    assert "987654321" not in serialized
    assert "Authorization" not in serialized


def test_panel_blocks_unsafe_dispatch_flags(tmp_path: Path) -> None:
    paths = write_reports(tmp_path, dispatch_report=dispatch_report(sends_orders=True))
    state = load_critical_notifications_panel_state(source_paths=paths, env=secure_env())

    assert state["status"] == "blocked"
    assert "unsafe_flag:sends_orders_not_false" in state["blocked_reasons"]


def test_panel_handles_invalid_json_as_safe_degraded(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    paths["alert_report"].write_text("{invalid", encoding="utf-8")
    write_json(paths["dispatch_report"], dispatch_report())

    state = load_critical_notifications_panel_state(source_paths=paths, env={})

    assert state["status"] == "degraded"
    assert state["sends_orders"] is False
    assert any(item.startswith("invalid_source:alert_report") for item in state["warnings"])


def test_sanitize_payload_redacts_nested_sensitive_keys() -> None:
    payload = {
        "token": "abc",
        "nested": {"chat_id": "123", "headers": {"Authorization": "Bearer abc"}},
        "results": [{"topic": "private"}],
    }

    sanitized = sanitize_payload(payload)
    serialized = json.dumps(sanitized, sort_keys=True)

    assert "abc" not in serialized
    assert "123" not in serialized
    assert "private" not in serialized
    assert "Authorization" not in serialized


def test_render_panel_is_read_only_and_does_not_require_actions(tmp_path: Path) -> None:
    paths = write_reports(tmp_path)
    state = load_critical_notifications_panel_state(source_paths=paths, env=secure_env())
    fake = FakeStreamlit()

    render_critical_notifications_panel(fake, state=state)

    assert state["forbidden_actions_present"] == []
    assert state["dashboard_dispatch_enabled"] is False
    assert any(call[0] == "metric" for call in fake.calls)


def test_dashboard_notification_panel_source_has_no_trading_or_exchange_calls() -> None:
    source = (ROOT / "smartcrypto" / "dashboard" / "critical_notifications_panel.py").read_text(encoding="utf-8")

    forbidden_fragments = ("ccxt", "create_order", "fetch_balance", "market_buy", "cancel_order")
    assert not any(fragment in source for fragment in forbidden_fragments)
