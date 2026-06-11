from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from smartcrypto.dashboard.alerts.contracts import (
    NotificationDispatchResult,
    NotificationRoutingPolicy,
)
from smartcrypto.dashboard.ui.cards import render_status_card


def render_notification_stub_only_banner(*, ui: Any) -> None:
    ui.warning("STUB ONLY - NO TELEGRAM/NTFY SEND")


def render_notification_routing_table(
    policies: Sequence[NotificationRoutingPolicy],
    *,
    ui: Any,
) -> None:
    with ui.expander("Notification routing policy", expanded=False):
        ui.dataframe([policy.to_dict() for policy in policies], use_container_width=True, hide_index=True)


def render_notification_dispatch_stub(result: NotificationDispatchResult, *, ui: Any) -> None:
    ui.markdown(
        render_status_card(
            "Notification dispatcher stub",
            "READONLY",
            f"{result.status} · sent={str(result.sent).lower()}",
        ),
        unsafe_allow_html=True,
    )
    with ui.expander("Notification audit payload", expanded=False):
        ui.json(result.to_dict())
