from __future__ import annotations

import pytest

from smartcrypto.dashboard.alerts.contracts import NotificationDispatchStatus, NotificationIntent
from smartcrypto.dashboard.alerts.notification_stub_dispatcher import evaluate_notification_intent


@pytest.mark.parametrize(
    ("severity", "channels"),
    [
        ("INFO", ["LOG"]),
        ("WARNING", ["TELEGRAM"]),
        ("CRITICAL", ["TELEGRAM", "NTFY"]),
        ("PANIC", ["TELEGRAM", "NTFY", "OPERATOR_REQUIRED"]),
    ],
)
def test_notification_routing_is_dry_run_only(severity: str, channels: list[str]) -> None:
    result = evaluate_notification_intent(
        NotificationIntent(notification_id=severity, severity=severity, message="test")
    )
    assert result.status is NotificationDispatchStatus.DRY_RUN_ACCEPTED
    assert [channel.value for channel in result.channels] == channels
    assert result.sent is False
    assert result.dry_run is True
    assert result.simulated_delivery["delivery_attempted"] is False


def test_invalid_severity_and_empty_message_are_rejected() -> None:
    invalid = evaluate_notification_intent(
        NotificationIntent(notification_id="x", severity="INVALID", message="test")
    )
    empty = evaluate_notification_intent(
        NotificationIntent(notification_id="y", severity="INFO", message="")
    )
    assert invalid.status is NotificationDispatchStatus.DRY_RUN_REJECTED
    assert empty.status is NotificationDispatchStatus.DRY_RUN_REJECTED
    assert invalid.sent is empty.sent is False
