from __future__ import annotations

from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    all_source_payloads,
    bool_value,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    load_page_sources,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
    HardBlockStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import safe_div


LEVEL1_COMMANDS = (
    "REFRESH_VIEW",
    "FILTER_PERIOD",
    "SELECT_SYMBOL",
    "EXPORT_READONLY_REPORT",
    "OPEN_RUNBOOK",
)
LEVEL2_COMMANDS = (
    "PAUSE_PAPER_ENTRIES",
    "RESUME_PAPER_ENTRIES",
    "REQUEST_SNAPSHOT_REFRESH",
    "REQUEST_PAPER_FEEDBACK_REFRESH",
    "REQUEST_READONLY_REPORT_REBUILD",
)
LEVEL3_COMMANDS = (
    "PAPER_KILL_SWITCH",
    "RESTART_GRID_PAPER",
    "CHANGE_GRID_PARAMETERS_PAPER",
    "PAUSE_STRATEGY_PAPER",
    "RESUME_STRATEGY_PAPER",
    "CLOSE_PAPER_SIMULATION",
)
LEVEL4_COMMANDS = (
    "LIVE_ORDER",
    "MARKET_SELL_ALL_REAL",
    "SNIPER_REAL",
    "CANCEL_ALL_LIVE_ORDERS",
    "LIQUIDATE_REAL_INVENTORY",
    "CHANGE_LIVE_RISK",
    "ENABLE_LIVE_TRADING",
    "ENABLE_PRIVATE_READ_REAL",
    "PROMOTE_MODEL_TO_PRODUCTION",
    "AUTO_INCREASE_CAPITAL",
    "RELEASE_REAL_SAFETY_ORDER",
)

REQUIRED_SECTIONS = (
    "active_layer_status",
    "level1_commands",
    "level2_commands",
    "level3_commands",
    "level4_hard_blocks",
    "kill_switch",
    "grid_parameter_change",
    "security_state",
    "readiness_gap_accounting",
    "command_events",
    "audit",
)


def calculate_grid_parameter_diff(old_value: float, new_value: float) -> dict[str, float]:
    difference = new_value - old_value
    return {"diff_abs": difference, "diff_pct": safe_div(difference, old_value) * 100.0}


def build_active_controls_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.active_controls)
    data = all_source_payloads(sources)
    kill = first_payload(sources, "kill_switch")
    reconciliation = first_payload(sources, "state_reconciliation_audit_report")
    kill_active = bool_value(first_value(kill, ("active", "enabled")), False)
    reconciliation_status = str(first_value(reconciliation, ("status", "reconciliation_status"), "unknown")).upper()
    reconciliation_lock = reconciliation_status not in {"OK", "PASS", "PASSED", "VALID"}
    live_authority = bool_value(first_value(data, ("live_authority", "live_trading_enabled")), False)
    real_orders = bool_value(first_value(data, ("real_order_submission_enabled",)), False)
    risk_approval = bool_value(first_value(data, ("riskmanager_approval", "risk_approved")), False)
    active_status = DashboardSectionStatus.BLOCKED if live_authority or real_orders else DashboardSectionStatus.WARNING if sources["future_sources_pending"] else DashboardSectionStatus.OK
    old_grid = finite_float(first_value(data, ("current_grid_capital_usdt", "old_value")), 0.0) or 0.0
    new_grid = finite_float(first_value(data, ("new_grid_capital_usdt", "new_value")), old_grid) or 0.0
    grid_diff = calculate_grid_parameter_diff(old_grid, new_grid)
    gap_payload = first_payload(sources, "paper_shadow_soak_gap_accounting_report")
    if not gap_payload:
        gap_payload = first_payload(sources, "readiness_snapshot_v2")
    gap_status = str(first_value(gap_payload, ("status",), "unknown")).lower()
    critical_gaps = int(finite_float(first_value(gap_payload, ("critical_gap_count",), 0), 0) or 0)
    gap_section_status = (
        DashboardSectionStatus.BLOCKED
        if gap_status in {"blocked", "critical", "failed"} or critical_gaps > 0
        else DashboardSectionStatus.OK if gap_payload else DashboardSectionStatus.UNKNOWN
    )

    sections = {
        "active_layer_status": section(active_status, command_execution_enabled=False, paper_entry_allowed=not kill_active and not reconciliation_lock and risk_approval),
        "level1_commands": section(DashboardSectionStatus.OK, commands=[{"command": command, "status": HardBlockStatus.READ_ONLY.value} for command in LEVEL1_COMMANDS]),
        "level2_commands": section(DashboardSectionStatus.UNKNOWN, commands=[{"command": command, "status": "FUTURE_COMMAND_BUS"} for command in LEVEL2_COMMANDS]),
        "level3_commands": section(DashboardSectionStatus.UNKNOWN, commands=[{"command": command, "status": "FUTURE_PAPER_ONLY"} for command in LEVEL3_COMMANDS]),
        "level4_hard_blocks": section(DashboardSectionStatus.OK, "hard_blocks_enforced", commands=[{"command": command, "status": HardBlockStatus.HARD_BLOCKED.value, "allowed": False} for command in LEVEL4_COMMANDS]),
        "kill_switch": section(DashboardSectionStatus.BLOCKED if kill_active else DashboardSectionStatus.OK, global_kill_switch_active=kill_active, kill_switch_effective=kill_active),
        "grid_parameter_change": section(DashboardSectionStatus.OK, current_grid_capital_usdt=old_grid, new_grid_capital_usdt=new_grid, grid_capital_change_usdt=grid_diff["diff_abs"], grid_capital_change_pct=grid_diff["diff_pct"], informational_only=True),
        "security_state": section(DashboardSectionStatus.BLOCKED if reconciliation_lock else DashboardSectionStatus.OK, reconciliation_lock_active=reconciliation_lock, riskmanager_authority=True, live_authority=False, real_order_submission_enabled=False),
        "readiness_gap_accounting": section(
            gap_section_status,
            "gap_accounting_blocks_readiness" if gap_section_status is DashboardSectionStatus.BLOCKED else "gap_accounting_readonly",
            continuous_valid_soak_days=finite_float(first_value(gap_payload, ("continuous_valid_soak_days",), 0.0), 0.0),
            observed_calendar_days=finite_float(first_value(gap_payload, ("observed_calendar_days",), 0.0), 0.0),
            critical_gap_count=critical_gaps,
            warning_gap_count=int(finite_float(first_value(gap_payload, ("warning_gap_count",), 0), 0) or 0),
            max_gap_minutes=finite_float(first_value(gap_payload, ("max_gap_minutes",), 0.0), 0.0),
            seven_day_diagnostic_status=first_value(gap_payload, ("seven_day_diagnostic_status",), "unknown"),
            thirty_day_readiness_status=first_value(gap_payload, ("thirty_day_readiness_status",), "blocked"),
            readiness_gap_free=first_value(gap_payload, ("readiness_gap_free",), False) is True and critical_gaps == 0,
            canary_release_allowed=False,
            live_release_allowed=False,
            manual_go_no_go_required=True,
        ),
        "command_events": section(DashboardSectionStatus.OK, events=records(first_payload(sources, "dashboard_command_audit_log"))[-50:]),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True, command_bus_called=False, changes_config=False),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.active_controls,
        schema_version=DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )
