from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.ops.notification_channels import (
    NtfyConfig,
    NotificationDispatcher,
    NotificationMessage,
    NotificationSettings,
    TelegramConfig,
    build_ntfy_url,
    build_telegram_url,
    dispatch_alert_report,
    message_from_alert_report,
    preflight_notification_channels,
    settings_from_env,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return b'{"ok":true}'


def sample_alert_report(status: str = "blocked") -> dict[str, Any]:
    return {
        "status": status,
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
        "summary": {"total_events": 2, "latest_event_at_utc": "2026-06-08T12:00:00Z"},
    }


def test_settings_from_env_defaults_are_disabled_and_paper_only() -> None:
    settings = settings_from_env({})

    assert settings.ntfy.enabled is False
    assert settings.telegram.enabled is False
    assert settings.ntfy.server_url == "https://ntfy.sh"
    assert settings.telegram.api_base_url == "https://api.telegram.org"


def test_settings_from_env_loads_ntfy_and_telegram_without_real_secret_values() -> None:
    settings = settings_from_env(
        {
            "SMARTCRYPTO_NTFY_ENABLED": "true",
            "SMARTCRYPTO_NTFY_TOPIC": "private-topic",
            "SMARTCRYPTO_TELEGRAM_ENABLED": "true",
            "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "SMARTCRYPTO_TELEGRAM_CHAT_ID": "123456",
        }
    )

    assert settings.ntfy.enabled is True
    assert settings.ntfy.topic == "private-topic"
    assert settings.telegram.enabled is True
    assert settings.telegram.bot_token == "123456:ABC-DEF"


def valid_ntfy_config(**overrides: Any) -> NtfyConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "topic": "paper-events",
        "server_url": "https://ntfy.example.invalid",
        "token": "test-auth",
    }
    values.update(overrides)
    return NtfyConfig(**values)


def valid_telegram_config(**overrides: Any) -> TelegramConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "bot_token": "test-bot",
        "chat_id": "test-chat",
    }
    values.update(overrides)
    return TelegramConfig(**values)


def test_telegram_only_does_not_require_ntfy() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=NtfyConfig(enabled=False), telegram=valid_telegram_config()),
        channels="telegram",
    ).to_dict()

    assert result["status"] == "ok"
    assert result["failed_checks"] == []


def test_telegram_only_requires_telegram_credentials() -> None:
    missing_token = preflight_notification_channels(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=False),
            telegram=valid_telegram_config(bot_token=""),
        ),
        channels="telegram",
    ).to_dict()
    missing_chat = preflight_notification_channels(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=False),
            telegram=valid_telegram_config(chat_id=""),
        ),
        channels="telegram",
    ).to_dict()

    assert missing_token["reason"] == "missing_telegram_bot_token"
    assert missing_chat["reason"] == "missing_telegram_chat_id"


def test_ntfy_token_auth_is_allowed() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config()),
        channels="ntfy",
    ).to_dict()

    assert result["status"] == "ok"
    assert result["auth_mode"] == "bearer"


def test_ntfy_basic_auth_is_allowed() -> None:
    result = preflight_notification_channels(
        NotificationSettings(
            ntfy=valid_ntfy_config(token="", username="test-user", password="test-pass")
        ),
        channels="ntfy",
    ).to_dict()

    assert result["status"] == "ok"
    assert result["auth_mode"] == "basic"


def test_ntfy_enabled_without_auth_is_blocked() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config(token="")),
        channels="ntfy",
    ).to_dict()

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_ntfy_authentication"
    assert result["failed_checks"] == ["missing_ntfy_authentication"]


def test_ntfy_partial_basic_auth_is_blocked() -> None:
    for credentials in (
        {"username": "test-user", "password": ""},
        {"username": "", "password": "test-pass"},
    ):
        result = preflight_notification_channels(
            NotificationSettings(ntfy=valid_ntfy_config(token="", **credentials)),
            channels="ntfy",
        ).to_dict()

        assert result["reason"] == "invalid_ntfy_basic_auth_pair"
        assert result["auth_mode"] == "ambiguous"


def test_ntfy_token_and_basic_auth_is_blocked() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config(username="test-user", password="test-pass")),
        channels="ntfy",
    ).to_dict()

    assert result["reason"] == "ambiguous_ntfy_auth"
    assert result["auth_mode"] == "ambiguous"


def test_ntfy_http_url_is_blocked() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config(server_url="http://ntfy.example.invalid")),
        channels="ntfy",
    ).to_dict()

    assert result["reason"] == "ntfy_https_required"


def test_ntfy_preflight_rejects_url_userinfo() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config(server_url="https://user:pass@ntfy.example.invalid")),
        channels="ntfy",
    ).to_dict()

    assert result["reason"] == "ntfy_url_userinfo_not_allowed"


def test_ntfy_preflight_blocks_invalid_timeout_from_environment() -> None:
    settings = settings_from_env(
        {
            "SMARTCRYPTO_NTFY_ENABLED": "true",
            "SMARTCRYPTO_NTFY_TOPIC": "paper-events",
            "SMARTCRYPTO_NTFY_TOKEN": "test-auth",
            "SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS": "invalid",
        }
    )

    result = preflight_notification_channels(settings, channels="ntfy").to_dict()

    assert result["reason"] == "invalid_timeout_seconds"


def test_all_channels_with_ntfy_disabled_is_blocked() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=NtfyConfig(enabled=False), telegram=valid_telegram_config()),
        channels="all",
    ).to_dict()

    assert result["status"] == "blocked"
    assert result["reason"] == "ntfy_disabled"


def test_preflight_payload_is_sanitized() -> None:
    result = preflight_notification_channels(
        NotificationSettings(
            ntfy=valid_ntfy_config(token="test-auth"),
            telegram=valid_telegram_config(bot_token="test-bot", chat_id="test-chat"),
        ),
        channels="all",
    ).to_dict()
    serialized = json.dumps(result, sort_keys=True)

    assert result["status"] == "ok"
    assert "test-auth" not in serialized
    assert "test-bot" not in serialized
    assert "test-chat" not in serialized
    assert "topic" not in result


def test_all_channels_requires_secure_ntfy() -> None:
    result = preflight_notification_channels(
        NotificationSettings(
            ntfy=valid_ntfy_config(token=""),
            telegram=valid_telegram_config(),
        ),
        channels="all",
    ).to_dict()

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_ntfy_authentication"


def test_safe_flags_remain_fail_closed() -> None:
    result = preflight_notification_channels(
        NotificationSettings(ntfy=valid_ntfy_config()),
        channels="ntfy",
    ).to_dict()

    assert result["paper_only"] is True
    assert result["shadow_only"] is True
    assert result["live_trading_enabled"] is False
    assert result["live_release_allowed"] is False
    assert result["canary_release_allowed"] is False
    assert result["order_submission_enabled"] is False
    assert result["real_order_submission_enabled"] is False
    assert result["exchange_private_access"] is False
    assert result["sends_orders"] is False
    assert result["changes_risk"] is False


def test_ntfy_url_quotes_topic() -> None:
    assert build_ntfy_url("https://ntfy.sh/", "topic with spaces") == "https://ntfy.sh/topic%20with%20spaces"


def test_telegram_url_uses_send_message_endpoint() -> None:
    assert (
        build_telegram_url("https://api.telegram.org/", "123:ABC")
        == "https://api.telegram.org/bot123:ABC/sendMessage"
    )


def test_dispatcher_dry_run_does_not_call_network() -> None:
    def fail_network(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise AssertionError("network should not be called during dry-run")

    dispatcher = NotificationDispatcher(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=True, topic="topic", token="test-auth"),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="1"),
        ),
        ntfy_opener=fail_network,
        telegram_opener=fail_network,
    )

    results = dispatcher.send(NotificationMessage(title="Title", body="Body"), dry_run=True)

    assert [result.status for result in results] == ["sent", "sent"]
    assert all(result.dry_run is True for result in results)
    assert all(result.sends_orders is False for result in results)
    assert all(result.changes_risk is False for result in results)


def test_dispatcher_posts_to_ntfy_and_telegram_with_injected_openers() -> None:
    captured: list[tuple[str, str, bytes, dict[str, str]]] = []

    def opener(request: Any, timeout: float) -> FakeResponse:
        captured.append((request.full_url, request.get_method(), request.data, dict(request.header_items())))
        assert timeout == 10.0
        return FakeResponse()

    dispatcher = NotificationDispatcher(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=True, topic="topic", token="test-auth"),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="1"),
        ),
        ntfy_opener=opener,
        telegram_opener=opener,
    )

    results = dispatcher.send(NotificationMessage(title="Critical", body="Body", priority="urgent", tags=("warning",)))

    assert [result.status for result in results] == ["sent", "sent"]
    assert captured[0][0] == "https://ntfy.sh/topic"
    assert captured[0][1] == "POST"
    assert captured[0][2] == b"Body"
    assert captured[1][0] == "https://api.telegram.org/bot123:ABC/sendMessage"
    telegram_payload = json.loads(captured[1][2].decode("utf-8"))
    assert telegram_payload["chat_id"] == "1"
    assert "Critical" in telegram_payload["text"]
    assert telegram_payload["link_preview_options"] == {"is_disabled": True}


def test_missing_ntfy_topic_blocks_only_ntfy_channel() -> None:
    dispatcher = NotificationDispatcher(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=True, topic=""),
            telegram=TelegramConfig(enabled=False),
        )
    )

    results = dispatcher.send(NotificationMessage(title="Title", body="Body"), dry_run=True)

    assert results[0].status == "blocked"
    assert results[0].reason == "missing_ntfy_topic"
    assert results[1].status == "disabled"


def test_missing_telegram_token_blocks_only_telegram_channel() -> None:
    dispatcher = NotificationDispatcher(
        NotificationSettings(
            ntfy=NtfyConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="", chat_id="1"),
        )
    )

    results = dispatcher.send(NotificationMessage(title="Title", body="Body"), dry_run=True)

    assert results[0].status == "disabled"
    assert results[1].status == "blocked"
    assert results[1].reason == "missing_telegram_bot_token"


def test_message_from_alert_report_summarizes_critical_alert() -> None:
    message = message_from_alert_report(sample_alert_report())

    assert message.priority == "urgent"
    assert "status=blocked" in message.body
    assert "kill_switch_triggered" in message.body
    assert message.correlation_id == "corr-1"
    assert message.event_type == "kill_switch_triggered"


def test_dispatch_alert_report_skips_ok_by_default() -> None:
    result = dispatch_alert_report(sample_alert_report(status="ok"), settings=NotificationSettings(), dry_run=True)

    assert result["status"] == "skipped"
    assert result["dispatch_attempted"] is False
    assert result["sends_orders"] is False
    assert result["changes_risk"] is False


def test_dispatch_alert_report_disabled_when_no_channels_enabled() -> None:
    result = dispatch_alert_report(sample_alert_report(), settings=NotificationSettings(), dry_run=True)

    assert result["status"] == "disabled"
    assert result["reason"] == "all_channels_disabled"
    assert result["paper_only"] is True
    assert result["live_trading_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["real_order_submission_enabled"] is False


def test_cli_dry_run_reads_alert_report_and_writes_dispatch_report(tmp_path: Path) -> None:
    alert_report = tmp_path / "critical_alerting_report.json"
    dispatch_report = tmp_path / "critical_notification_dispatch_report.json"
    alert_report.write_text(json.dumps(sample_alert_report()), encoding="utf-8")
    env = {
        **os.environ,
        "SMARTCRYPTO_NTFY_ENABLED": "true",
        "SMARTCRYPTO_NTFY_TOPIC": "topic",
        "SMARTCRYPTO_NTFY_TOKEN": "test-auth",
        "SMARTCRYPTO_TELEGRAM_ENABLED": "true",
        "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "123:ABC",
        "SMARTCRYPTO_TELEGRAM_CHAT_ID": "1",
    }

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_critical_notification_dispatch.py"),
            "--alert-report",
            str(alert_report),
            "--dispatch-report",
            str(dispatch_report),
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    saved = json.loads(dispatch_report.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert saved == payload
    assert {row["channel"] for row in payload["results"]} == {"ntfy", "telegram"}
    assert all(row["dry_run"] is True for row in payload["results"])


def test_cli_no_write_report_does_not_create_runtime_file(tmp_path: Path) -> None:
    alert_report = tmp_path / "critical_alerting_report.json"
    dispatch_report = tmp_path / "should_not_exist.json"
    alert_report.write_text(json.dumps(sample_alert_report()), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_critical_notification_dispatch.py"),
            "--alert-report",
            str(alert_report),
            "--dispatch-report",
            str(dispatch_report),
            "--dry-run",
            "--no-write-report",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert not dispatch_report.exists()

def test_ntfy_header_values_are_latin1_safe_for_unicode_title() -> None:
    captured: list[dict[str, str]] = []

    def opener(request: Any, timeout: float) -> FakeResponse:
        headers = dict(request.header_items())
        captured.append(headers)
        for value in headers.values():
            value.encode("latin-1")
        return FakeResponse()

    dispatcher = NotificationDispatcher(
        NotificationSettings(ntfy=NtfyConfig(enabled=True, topic="topic", token="test-auth")),
        ntfy_opener=opener,
    )

    results = dispatcher.send(
        NotificationMessage(
            title="FUTUROS PAPER — CLOSE_LONG ETH/USDT:USDT",
            body="corpo com unicode preservado: operação — encerrada",
            priority="urgent",
            tags=("warning", "rotating_light"),
        )
    )

    assert results[0].status == "sent"
    assert captured[0]["Title"] == "FUTUROS PAPER - CLOSE_LONG ETH/USDT:USDT"


def test_ntfy_preserves_unicode_body_as_utf8_while_sanitizing_headers() -> None:
    captured: list[tuple[bytes, dict[str, str]]] = []

    def opener(request: Any, timeout: float) -> FakeResponse:
        captured.append((request.data, dict(request.header_items())))
        return FakeResponse()

    dispatcher = NotificationDispatcher(
        NotificationSettings(ntfy=NtfyConfig(enabled=True, topic="topic", token="test-auth")),
        ntfy_opener=opener,
    )

    dispatcher.send(
        NotificationMessage(
            title="Título — com travessão",
            body="Mensagem UTF-8 preservada — ação simulada",
        )
    )

    body, headers = captured[0]
    assert headers["Title"] == "Título - com travessão"
    assert body.decode("utf-8") == "Mensagem UTF-8 preservada — ação simulada"
