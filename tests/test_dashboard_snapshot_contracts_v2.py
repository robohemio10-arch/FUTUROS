from __future__ import annotations

from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
    DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
    DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
    DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
    DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
    DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
    DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
    DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
    DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
    DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
    DashboardAuditContract,
    DashboardPageId,
    DashboardSectionStatus,
    DashboardSnapshotBase,
    HardBlockStatus,
    RuntimeMode,
    SourceKind,
    validate_dashboard_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.status import (
    is_blocking_status,
    merge_section_statuses,
)


def test_official_schema_versions_are_exact() -> None:
    assert DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION == "dashboard_global_status_snapshot_v1"
    assert DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION == "dashboard_infrastructure_snapshot_v1"
    assert DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION == "dashboard_portfolio_risk_snapshot_v1"
    assert DASHBOARD_GRID_MONITOR_SCHEMA_VERSION == "dashboard_grid_monitor_snapshot_v1"
    assert DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION == "dashboard_opportunity_scanner_snapshot_v1"
    assert DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION == "dashboard_ai_governance_snapshot_v1"
    assert DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION == "dashboard_active_controls_snapshot_v1"
    assert DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION == "dashboard_quantitative_reports_snapshot_v1"
    assert DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION == "dashboard_alerts_messaging_snapshot_v1"
    assert DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION == "dashboard_snapshot_build_summary_v1"


def test_enums_expose_the_institutional_contract() -> None:
    assert {item.value for item in DashboardSectionStatus} == {
        "OK",
        "UNKNOWN",
        "MISSING_OPTIONAL",
        "MISSING_REQUIRED",
        "STALE",
        "WARNING",
        "DEGRADED",
        "BLOCKED",
        "ERROR",
    }
    assert {item.value for item in SourceKind} == {
        "REQUIRED_EXISTING_SOURCE",
        "OPTIONAL_EXISTING_SOURCE",
        "FUTURE_SOURCE",
        "GENERATED_BY_THIS_BRANCH",
    }
    assert {item.value for item in RuntimeMode} == {"paper", "shadow", "backtest", "unknown"}
    assert {item.value for item in HardBlockStatus} == {
        "HARD_BLOCKED",
        "READ_ONLY",
        "DISABLED",
        "UNKNOWN",
    }
    assert len(DashboardPageId) == 8


def test_snapshot_defaults_are_readonly_and_fail_closed() -> None:
    payload = DashboardSnapshotBase(
        schema_version=DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
        runtime_mode=RuntimeMode.paper,
    ).to_dict()

    assert validate_dashboard_snapshot(payload) == []
    assert payload["dashboard_readonly"] is True
    assert payload["live_locked"] is True
    assert payload["order_submission_enabled"] is False
    assert payload["real_order_submission_enabled"] is False
    assert payload["audit"] == DashboardAuditContract().to_dict()


def test_snapshot_validation_reports_every_required_contract_field() -> None:
    errors = validate_dashboard_snapshot({})

    for field in (
        "schema_version",
        "runtime_mode",
        "dashboard_readonly",
        "live_locked",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "audit",
    ):
        assert f"missing_field:{field}" in errors


def test_audit_defaults_have_no_operational_permissions() -> None:
    audit = DashboardAuditContract(snapshot_source="unit_test").to_dict()

    assert audit["dashboard_reads_only"] is True
    for field in (
        "uses_private_exchange",
        "uses_ccxt",
        "sends_orders",
        "changes_risk",
        "promotes_model",
        "changes_config",
        "changes_model",
        "changes_active_signals",
    ):
        assert audit[field] is False


def test_status_helpers_are_fail_closed_without_blocking_optional_sources() -> None:
    assert is_blocking_status(DashboardSectionStatus.ERROR)
    assert is_blocking_status(DashboardSectionStatus.BLOCKED)
    assert is_blocking_status(DashboardSectionStatus.MISSING_REQUIRED)
    assert not is_blocking_status(DashboardSectionStatus.MISSING_OPTIONAL)
    assert not is_blocking_status(DashboardSectionStatus.UNKNOWN)
    assert merge_section_statuses(["OK", "WARNING"]) is DashboardSectionStatus.WARNING


def test_build_context_is_readonly_by_default(tmp_path) -> None:
    context = create_dashboard_build_context(tmp_path, runtime_mode="paper", strict=True)

    assert context.project_root == tmp_path.resolve()
    assert context.runtime_mode is RuntimeMode.paper
    assert context.strict is True
    assert context.allow_writes_to_output_dir is False
