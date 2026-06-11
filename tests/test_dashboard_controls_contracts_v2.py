from __future__ import annotations

from smartcrypto.dashboard.controls.audit import build_controls_safety_footer
from smartcrypto.dashboard.controls.contracts import (
    DashboardCommandIntent,
    DashboardCommandLevel,
    DashboardCommandResult,
    DashboardCommandRisk,
    DashboardCommandSideEffect,
    DashboardCommandStatus,
)


def test_command_contract_enums_and_serialization() -> None:
    intent = DashboardCommandIntent(command_id="c-1", command_name="REFRESH_VIEW")
    result = DashboardCommandResult(
        command_id="c-1", command_name="REFRESH_VIEW",
        level=DashboardCommandLevel.N1_LOCAL_INFO,
        status=DashboardCommandStatus.DRY_RUN_ACCEPTED, accepted=True,
    )
    assert intent.to_dict()["source"] == "dashboard_stub"
    assert result.to_dict()["executed"] is False
    assert result.to_dict()["dry_run"] is True
    assert DashboardCommandRisk.CRITICAL.value == "CRITICAL"
    assert DashboardCommandSideEffect.NONE.value == "NONE"


def test_controls_audit_is_stub_only_and_safe() -> None:
    audit = build_controls_safety_footer()
    assert audit["command_stub_only"] is True
    assert audit["executed"] is False
    for key in (
        "uses_command_bus", "uses_private_exchange", "uses_ccxt", "sends_orders",
        "sends_notifications", "changes_risk", "changes_config", "changes_model",
        "changes_active_signals", "writes_runtime", "runs_ocr", "imports_trades",
        "rebuilds_dataset", "cleans_sqlite", "changes_readiness", "enables_canary",
        "enables_live",
    ):
        assert audit[key] is False
