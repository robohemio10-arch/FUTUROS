from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from smartcrypto.dashboard.alerts.contracts import (
    NotificationDispatchResult,
    NotificationRoutingPolicy,
)


def render_notification_stub_only_banner(*, ui: Any) -> None:
    ui.warning("STUB ONLY - NO TELEGRAM/NTFY SEND")


def render_notification_routing_table(
    policies: Sequence[NotificationRoutingPolicy],
    *,
    ui: Any,
) -> None:
    ui.dataframe([policy.to_dict() for policy in policies], use_container_width=True, hide_index=True)


def render_notification_dispatch_stub(result: NotificationDispatchResult, *, ui: Any) -> None:
    ui.json(result.to_dict())
