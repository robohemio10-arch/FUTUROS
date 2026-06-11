from __future__ import annotations

from typing import Any

from .contracts import DashboardCommandIntent, DashboardCommandPolicy, DashboardCommandResult


def build_controls_safety_footer() -> dict[str, Any]:
    return {
        "project_name": "SMART FUTUROS",
        "dashboard_name": "SMART FUTUROS Command Center",
        "command_stub_only": True,
        "dashboard_reads_only": True,
        "uses_command_bus": False,
        "uses_private_exchange": False,
        "uses_ccxt": False,
        "sends_orders": False,
        "sends_notifications": False,
        "changes_risk": False,
        "changes_config": False,
        "changes_model": False,
        "changes_active_signals": False,
        "writes_runtime": False,
        "runs_ocr": False,
        "imports_trades": False,
        "rebuilds_dataset": False,
        "cleans_sqlite": False,
        "changes_readiness": False,
        "enables_canary": False,
        "enables_live": False,
        "executed": False,
    }


def build_command_stub_audit(
    intent: DashboardCommandIntent,
    policy: DashboardCommandPolicy,
) -> dict[str, Any]:
    return {
        **build_controls_safety_footer(),
        "command_id": intent.command_id,
        "command_name": policy.command_name,
        "dry_run": policy.dry_run_only,
        "hard_blocked": policy.hard_blocked,
    }


def build_command_result_audit(result: DashboardCommandResult) -> dict[str, Any]:
    return {
        **build_controls_safety_footer(),
        "command_id": result.command_id,
        "command_name": result.command_name,
        "dry_run": result.dry_run,
        "hard_blocked": result.hard_blocked,
    }
