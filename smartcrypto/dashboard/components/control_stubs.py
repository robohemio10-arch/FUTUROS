from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from smartcrypto.dashboard.controls.contracts import DashboardCommandPolicy, DashboardCommandResult


def render_stub_only_banner(*, ui: Any) -> None:
    ui.warning("STUB ONLY - NO EXECUTION")


def render_command_policy_table(
    policies: Sequence[DashboardCommandPolicy],
    *,
    ui: Any,
) -> None:
    ui.dataframe([policy.to_dict() for policy in policies], use_container_width=True, hide_index=True)


def render_command_result_stub(result: DashboardCommandResult, *, ui: Any) -> None:
    ui.json(result.to_dict())


def render_n4_hard_block_panel(
    policies: Sequence[DashboardCommandPolicy],
    *,
    ui: Any,
) -> None:
    blocked = [policy.to_dict() for policy in policies if policy.hard_blocked]
    ui.error(f"N4 HARD_BLOCKED: {len(blocked)} command(s)")
    ui.dataframe(blocked, use_container_width=True, hide_index=True)
