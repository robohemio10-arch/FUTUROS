from __future__ import annotations

from .contracts import NotificationChannel, NotificationRoutingPolicy, NotificationSeverity


ROUTING_CHANNELS = {
    NotificationSeverity.INFO: [NotificationChannel.LOG],
    NotificationSeverity.WARNING: [NotificationChannel.TELEGRAM],
    NotificationSeverity.CRITICAL: [NotificationChannel.TELEGRAM, NotificationChannel.NTFY],
    NotificationSeverity.PANIC: [
        NotificationChannel.TELEGRAM,
        NotificationChannel.NTFY,
        NotificationChannel.OPERATOR_REQUIRED,
    ],
}


def resolve_notification_routing(severity: str | NotificationSeverity) -> NotificationRoutingPolicy:
    normalized = severity if isinstance(severity, NotificationSeverity) else NotificationSeverity(str(severity).upper())
    return NotificationRoutingPolicy(severity=normalized, channels=list(ROUTING_CHANNELS[normalized]))


def list_notification_routing_policies() -> list[NotificationRoutingPolicy]:
    return [resolve_notification_routing(severity) for severity in NotificationSeverity]
