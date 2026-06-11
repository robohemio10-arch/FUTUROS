from __future__ import annotations

from typing import Any

from .audit import build_command_stub_audit
from .command_classifier import classify_dashboard_command, validate_command_payload
from .contracts import (
    DashboardCommandIntent,
    DashboardCommandLevel,
    DashboardCommandResult,
    DashboardCommandStatus,
)


def evaluate_dashboard_command_intent(intent: DashboardCommandIntent) -> DashboardCommandResult:
    policy = classify_dashboard_command(intent.command_name)
    audit = build_command_stub_audit(intent, policy)
    if policy.reason == "unknown_command_disabled":
        return DashboardCommandResult(
            command_id=intent.command_id,
            command_name=policy.command_name,
            level=policy.level,
            status=DashboardCommandStatus.DISABLED,
            accepted=False,
            executed=False,
            dry_run=True,
            hard_blocked=True,
            reason=policy.reason,
            simulated_effect={"effect": "disabled_unknown_command"},
            audit=audit,
        )
    if policy.hard_blocked:
        return DashboardCommandResult(
            command_id=intent.command_id,
            command_name=policy.command_name,
            level=policy.level,
            status=DashboardCommandStatus.HARD_BLOCKED,
            accepted=False,
            executed=False,
            dry_run=False,
            hard_blocked=True,
            reason=policy.reason,
            simulated_effect={"effect": "blocked_by_policy"},
            audit=audit,
        )
    valid, reason = validate_command_payload(intent, policy)
    accepted = valid and policy.enabled
    if policy.level is DashboardCommandLevel.N1_LOCAL_INFO:
        effect: dict[str, Any] = {"effect": "local_ui_only"}
    elif policy.level is DashboardCommandLevel.N3_DRY_RUN_STUB_SENSITIVE:
        effect = {
            "effect": "would_request_sensitive_paper_action",
            "manual_confirmation_required": True,
        }
    else:
        effect = {"effect": policy.side_effect.value.lower()}
    return DashboardCommandResult(
        command_id=intent.command_id,
        command_name=policy.command_name,
        level=policy.level,
        status=(
            DashboardCommandStatus.DRY_RUN_ACCEPTED
            if accepted
            else DashboardCommandStatus.DRY_RUN_REJECTED
        ),
        accepted=accepted,
        executed=False,
        dry_run=True,
        hard_blocked=False,
        reason=reason if not accepted else "dry_run_stub_accepted",
        simulated_effect=effect,
        audit=audit,
    )
