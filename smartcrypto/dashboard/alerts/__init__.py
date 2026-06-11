"""Notification routing and delivery simulations with no network access."""

from .contracts import (
    NotificationChannel,
    NotificationDispatchResult,
    NotificationDispatchStatus,
    NotificationIntent,
    NotificationRoutingPolicy,
    NotificationSeverity,
)
from .notification_stub_dispatcher import evaluate_notification_intent
from .routing import list_notification_routing_policies, resolve_notification_routing

__all__ = [
    "NotificationChannel", "NotificationDispatchResult", "NotificationDispatchStatus",
    "NotificationIntent", "NotificationRoutingPolicy", "NotificationSeverity",
    "evaluate_notification_intent", "list_notification_routing_policies",
    "resolve_notification_routing",
]
