from __future__ import annotations

from smartcrypto.dashboard.alerts.contracts import (
    NotificationChannel,
    NotificationDispatchResult,
    NotificationDispatchStatus,
    NotificationIntent,
    NotificationSeverity,
)
from smartcrypto.dashboard.alerts.policies import build_notification_stub_audit


def test_notification_contracts_are_serializable_and_unsent() -> None:
    intent = NotificationIntent(notification_id="n1", severity="INFO", message="test")
    result = NotificationDispatchResult(
        notification_id="n1", severity=NotificationSeverity.INFO,
        channels=[NotificationChannel.LOG], status=NotificationDispatchStatus.DRY_RUN_ACCEPTED,
        accepted=True,
    )
    assert intent.to_dict()["source"] == "dashboard_stub"
    assert result.to_dict()["sent"] is False
    assert result.to_dict()["dry_run"] is True


def test_notification_audit_denies_delivery_capabilities() -> None:
    audit = build_notification_stub_audit()
    assert audit["notification_stub_only"] is True
    for key in (
        "sends_notifications", "uses_telegram_token", "uses_ntfy_token",
        "uses_requests_post", "uses_httpx_post", "uses_aiohttp", "writes_runtime",
    ):
        assert audit[key] is False
