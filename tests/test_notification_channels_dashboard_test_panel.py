from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.notification_channels_test_panel import (
    load_notification_channels_test_panel_state,
    render_notification_channels_test_panel,
    sanitize_payload,
    dispatch_manual_notification_test,
)


class FakeStreamlit:
    def __init__(self, *, buttons: list[bool] | None = None, confirmation: str = "") -> None:
        self.calls: list[tuple[str, object]] = []
        self.buttons = list(buttons or [])
        self.confirmation = confirmation

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

    def markdown(self, value: str) -> None:
        self.calls.append(("markdown", value))

    def json(self, value: object) -> None:
        self.calls.append(("json", value))

    def columns(self, count: int) -> list["FakeStreamlit"]:
        self.calls.append(("columns", count))
        return [self for _ in range(count)]

    def metric(self, label: str, value: object) -> None:
        self.calls.append(("metric", {"label": label, "value": value}))

    def button(self, label: str) -> bool:
        self.calls.append(("button", label))
        if not self.buttons:
            return False
        return self.buttons.pop(0)

    def text_input(self, label: str, value: str = "") -> str:
        self.calls.append(("text_input", {"label": label, "value": value}))
        return self.confirmation


def secure_env() -> dict[str, str]:
    return {
        "SMARTCRYPTO_NTFY_ENABLED": "true",
        "SMARTCRYPTO_NTFY_SERVER_URL": "https://ntfy.sh",
        "SMARTCRYPTO_NTFY_TOPIC": "secret-topic-prod",
        "SMARTCRYPTO_NTFY_TOKEN": "tk_super_secret",
        "SMARTCRYPTO_TELEGRAM_ENABLED": "true",
        "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "123456:SECRET_TOKEN",
        "SMARTCRYPTO_TELEGRAM_CHAT_ID": "987654321",
        "SMARTCRYPTO_TELEGRAM_API_BASE_URL": "https://api.telegram.org",
        "SMARTCRYPTO_TELEGRAM_PARSE_MODE": "",
        "SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION": "false",
        "SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS": "10",
    }


def test_state_reports_configured_channels_without_exposing_secrets(tmp_path: Path) -> None:
    state = load_notification_channels_test_panel_state(
        env=secure_env(),
        dispatch_report_path=tmp_path / "missing.json",
    )

    assert state["configured_channels"] == 2
    assert state["sends_orders"] is False
    assert state["changes_risk"] is False
    assert state["exchange_private_access"] is False

    serialized = json.dumps(state, sort_keys=True)
    assert "secret-topic-prod" not in serialized
    assert "tk_super_secret" not in serialized
    assert "123456:SECRET_TOKEN" not in serialized
    assert "987654321" not in serialized


def test_state_degraded_when_channels_disabled(tmp_path: Path) -> None:
    state = load_notification_channels_test_panel_state(
        env={},
        dispatch_report_path=tmp_path / "missing.json",
    )

    assert state["status"] == "degraded"
    assert state["configured_channels"] == 0
    assert "channel_disabled:ntfy" in state["warnings"]
    assert "channel_disabled:telegram" in state["warnings"]
    assert state["paper_only"] is True
    assert state["shadow_only"] is True
    assert state["sends_orders"] is False
    assert state["changes_risk"] is False


def test_dry_run_dispatch_writes_sanitized_report(tmp_path: Path) -> None:
    output = tmp_path / "manual_dispatch.json"

    payload = dispatch_manual_notification_test(
        dry_run=True,
        env=secure_env(),
        output_path=output,
        now=datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False
    assert output.exists()

    serialized = output.read_text(encoding="utf-8")
    assert "SECRET_TOKEN" not in serialized
    assert "987654321" not in serialized
    assert "secret-topic-prod" not in serialized
    assert "tk_super_secret" not in serialized


def test_sanitize_payload_redacts_nested_sensitive_values() -> None:
    payload = {
        "topic": "secret-topic-prod",
        "chat_id": "987654321",
        "bot_token": "123456:SECRET_TOKEN",
        "nested": {
            "headers": {"Authorization": "Bearer tk_super_secret"},
            "url": "https://api.telegram.org/bot123456:SECRET_TOKEN/sendMessage",
        },
    }

    sanitized = sanitize_payload(payload)
    serialized = json.dumps(sanitized, sort_keys=True)

    assert "secret-topic-prod" not in serialized
    assert "987654321" not in serialized
    assert "SECRET_TOKEN" not in serialized
    assert "tk_super_secret" not in serialized
    assert "Authorization" not in serialized


def test_render_panel_does_not_dispatch_without_button_click(tmp_path: Path) -> None:
    state = load_notification_channels_test_panel_state(
        env=secure_env(),
        dispatch_report_path=tmp_path / "missing.json",
    )
    fake = FakeStreamlit()

    render_notification_channels_test_panel(fake, state=state, env=secure_env())

    assert any(call[0] == "metric" for call in fake.calls)
    assert state["forbidden_actions_present"] == []
    assert state["sends_orders"] is False
    assert state["changes_risk"] is False


def test_render_panel_blocks_real_send_without_confirmation(tmp_path: Path) -> None:
    state = load_notification_channels_test_panel_state(
        env=secure_env(),
        dispatch_report_path=tmp_path / "missing.json",
    )
    fake = FakeStreamlit(buttons=[False, True], confirmation="ERRADO")

    render_notification_channels_test_panel(fake, state=state, env=secure_env())

    assert any(call[0] == "warning" for call in fake.calls)
