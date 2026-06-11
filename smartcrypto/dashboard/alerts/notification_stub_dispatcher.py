from __future__ import annotations

from .contracts import (
    NotificationChannel,
    NotificationDispatchResult,
    NotificationDispatchStatus,
    NotificationIntent,
    NotificationSeverity,
)
from .policies import build_notification_stub_audit
from .routing import resolve_notification_routing


def evaluate_notification_intent(intent: NotificationIntent) -> NotificationDispatchResult:
    audit = build_notification_stub_audit()
    try:
        policy = resolve_notification_routing(intent.severity)
    except (TypeError, ValueError):
        return _rejected(intent, "invalid_severity", audit)
    if not str(intent.message or "").strip():
        return _rejected(intent, "empty_message", audit, severity=policy.severity)
    requested = _normalize_channels(intent.channels)
    if requested is None:
        return _rejected(intent, "invalid_channel", audit, severity=policy.severity)
    channels = policy.channels
    if requested and set(requested) != set(channels):
        return _rejected(intent, "channels_do_not_match_routing_policy", audit, severity=policy.severity)
    return NotificationDispatchResult(
        notification_id=intent.notification_id,
        severity=policy.severity,
        channels=channels,
        status=NotificationDispatchStatus.DRY_RUN_ACCEPTED,
        accepted=True,
        sent=False,
        dry_run=True,
        reason="notification_dry_run_accepted",
        simulated_delivery={
            "mode": "stub_only",
            "channels": [channel.value for channel in channels],
            "delivery_attempted": False,
            "operator_required": NotificationChannel.OPERATOR_REQUIRED in channels,
        },
        audit=audit,
    )


def _normalize_channels(
    channels: list[NotificationChannel | str],
) -> list[NotificationChannel] | None:
    try:
        return [
            channel if isinstance(channel, NotificationChannel) else NotificationChannel(str(channel).upper())
            for channel in channels
        ]
    except (TypeError, ValueError):
        return None


def _rejected(
    intent: NotificationIntent,
    reason: str,
    audit: dict[str, object],
    *,
    severity: NotificationSeverity | str = "UNKNOWN",
) -> NotificationDispatchResult:
    return NotificationDispatchResult(
        notification_id=intent.notification_id,
        severity=severity,
        channels=[],
        status=NotificationDispatchStatus.DRY_RUN_REJECTED,
        accepted=False,
        sent=False,
        dry_run=True,
        reason=reason,
        simulated_delivery={"mode": "stub_only", "delivery_attempted": False},
        audit=audit,
    )
