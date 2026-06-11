from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from smartcrypto.dashboard.controls.contracts import DashboardCommandPolicy, DashboardCommandResult
from smartcrypto.dashboard.ui.cards import render_status_card


def render_stub_only_banner(*, ui: Any) -> None:
    ui.warning("STUB ONLY - NO EXECUTION")


def render_command_policy_table(
    policies: Sequence[DashboardCommandPolicy],
    *,
    ui: Any,
) -> None:
    with ui.expander("Command policy matrix", expanded=False):
        ui.dataframe([policy.to_dict() for policy in policies], use_container_width=True, hide_index=True)


def render_command_result_stub(result: DashboardCommandResult, *, ui: Any) -> None:
    ui.markdown(
        render_status_card(
            result.command_name,
            "READONLY" if result.accepted else "BLOCKED",
            f"{result.status} · executed={str(result.executed).lower()} · {result.reason}",
        ),
        unsafe_allow_html=True,
    )
    with ui.expander(f"Audit {result.command_id}", expanded=False):
        ui.json(result.to_dict())


def render_n4_hard_block_panel(
    policies: Sequence[DashboardCommandPolicy],
    *,
    ui: Any,
) -> None:
    blocked = [policy.to_dict() for policy in policies if policy.hard_blocked]
    ui.error(f"N4 HARD_BLOCKED: {len(blocked)} command(s)")
    with ui.expander("N4 hard-block policy matrix", expanded=False):
        ui.dataframe(blocked, use_container_width=True, hide_index=True)
