from __future__ import annotations

from .contracts import DashboardCommandIntent, DashboardCommandLevel, DashboardCommandPolicy
from .policies import LEVEL_DEFAULTS, N1_COMMANDS, N2_COMMANDS, N3_COMMANDS, N4_COMMANDS


COMMAND_LEVELS = {
    **{name: DashboardCommandLevel.N1_LOCAL_INFO for name in N1_COMMANDS},
    **{name: DashboardCommandLevel.N2_DRY_RUN_STUB for name in N2_COMMANDS},
    **{name: DashboardCommandLevel.N3_DRY_RUN_STUB_SENSITIVE for name in N3_COMMANDS},
    **{name: DashboardCommandLevel.N4_HARD_BLOCKED for name in N4_COMMANDS},
}

REQUIRED_PAYLOAD_FIELDS = {
    "SELECT_SYMBOL": ("symbol",),
    "FILTER_PERIOD": ("period",),
    "CHANGE_GRID_PARAMETERS_PAPER": ("symbol", "proposed_parameters"),
    "PAPER_KILL_SWITCH": ("scope", "reason"),
    "REQUEST_ALERT_TEST_DRY_RUN": ("severity", "channel"),
    "REQUEST_READINESS_RECHECK_DRY_RUN": ("reason",),
    "REQUEST_DATASET_AUDIT_DRY_RUN": ("dataset_scope", "reason"),
}

SIDE_EFFECT_OVERRIDES = {
    "PAUSE_PAPER_ENTRIES": "WOULD_REQUEST_PAPER_ACTION",
    "RESUME_PAPER_ENTRIES": "WOULD_REQUEST_PAPER_ACTION",
    "REQUEST_ALERT_TEST_DRY_RUN": "WOULD_REQUEST_NOTIFICATION",
    "PAPER_KILL_SWITCH": "WOULD_REQUEST_RISK_CHANGE",
    "REQUEST_RISK_REEVALUATION_DRY_RUN": "WOULD_REQUEST_RISK_CHANGE",
}


def classify_dashboard_command(command_name: str) -> DashboardCommandPolicy:
    normalized = str(command_name or "").strip().upper()
    level = COMMAND_LEVELS.get(normalized)
    if level is None:
        return DashboardCommandPolicy(
            command_name=normalized or "UNKNOWN",
            level=DashboardCommandLevel.N4_HARD_BLOCKED,
            risk=LEVEL_DEFAULTS[DashboardCommandLevel.N4_HARD_BLOCKED][0],
            side_effect=LEVEL_DEFAULTS[DashboardCommandLevel.N4_HARD_BLOCKED][1],
            enabled=False,
            dry_run_only=False,
            hard_blocked=True,
            reason="unknown_command_disabled",
        )
    risk, side_effect, enabled, dry_run_only, hard_blocked = LEVEL_DEFAULTS[level]
    if normalized in SIDE_EFFECT_OVERRIDES:
        side_effect = type(side_effect)(SIDE_EFFECT_OVERRIDES[normalized])
    return DashboardCommandPolicy(
        command_name=normalized,
        level=level,
        risk=risk,
        side_effect=side_effect,
        enabled=enabled,
        dry_run_only=dry_run_only,
        hard_blocked=hard_blocked,
        reason="hard_blocked_by_policy" if hard_blocked else "stub_policy",
    )


def is_n4_hard_blocked(command_name: str) -> bool:
    return classify_dashboard_command(command_name).hard_blocked


def list_dashboard_command_policies() -> list[DashboardCommandPolicy]:
    return [classify_dashboard_command(name) for name in COMMAND_LEVELS]


def validate_command_payload(
    intent: DashboardCommandIntent,
    policy: DashboardCommandPolicy,
) -> tuple[bool, str]:
    if policy.hard_blocked:
        return False, "hard_blocked_by_policy"
    payload = intent.payload if isinstance(intent.payload, dict) else {}
    required = REQUIRED_PAYLOAD_FIELDS.get(policy.command_name, ())
    if policy.command_name == "REQUEST_OCR_STAGING_AUDIT_DRY_RUN":
        if payload.get("batch_id") or payload.get("reason"):
            return True, "payload_valid"
        return False, "missing_payload_field:batch_id_or_reason"
    for field_name in required:
        value = payload.get(field_name)
        if value is None or value == "" or value == {}:
            return False, f"missing_payload_field:{field_name}"
    return True, "payload_valid"
