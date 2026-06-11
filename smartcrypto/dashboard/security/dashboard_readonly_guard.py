from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardAuditContract


GLOBAL_READONLY_BANNERS = (
    "PAPER / SHADOW ONLY",
    "LIVE LOCKED",
    "ORDER SUBMISSION DISABLED",
    "REAL ORDER SUBMISSION DISABLED",
    "RISKMANAGER AUTHORITY",
    "DASHBOARD READ-ONLY",
)

PROHIBITED_TRUE_AUDIT_FLAGS = (
    "uses_private_exchange",
    "uses_ccxt",
    "sends_orders",
    "changes_risk",
    "promotes_model",
    "changes_config",
    "changes_model",
    "changes_active_signals",
)


class DashboardReadonlyViolation(RuntimeError):
    pass


def validate_no_operational_permissions(audit: Mapping[str, Any]) -> list[str]:
    violations = [name for name in PROHIBITED_TRUE_AUDIT_FLAGS if audit.get(name) is True]
    if audit.get("dashboard_reads_only") is not True:
        violations.append("dashboard_reads_only")
    return violations


def assert_dashboard_readonly(snapshot_or_audit: Mapping[str, Any]) -> None:
    audit_value = snapshot_or_audit.get("audit", snapshot_or_audit)
    if not isinstance(audit_value, Mapping):
        raise DashboardReadonlyViolation("dashboard_audit_missing")
    violations = validate_no_operational_permissions(audit_value)
    if snapshot_or_audit.get("live_locked", True) is not True:
        violations.append("live_locked")
    if snapshot_or_audit.get("order_submission_enabled", False) is True:
        violations.append("order_submission_enabled")
    if snapshot_or_audit.get("real_order_submission_enabled", False) is True:
        violations.append("real_order_submission_enabled")
    if violations:
        raise DashboardReadonlyViolation("unsafe_dashboard_permissions:" + ",".join(violations))


def build_readonly_audit_footer() -> dict[str, Any]:
    return {
        **DashboardAuditContract(snapshot_source="dashboard_ui").to_dict(),
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "risk_authority": "RiskManager",
    }


def get_global_banners() -> tuple[str, ...]:
    return GLOBAL_READONLY_BANNERS
