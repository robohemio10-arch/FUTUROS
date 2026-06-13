from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import (
    DashboardAuditContract,
    DashboardSectionStatus,
    RuntimeMode,
    utc_now_iso,
    validate_dashboard_snapshot,
)

from .snapshot_json_loader import load_snapshot_json


def load_dashboard_snapshot(
    snapshot_path: str | Path,
    schema_version: str | None = None,
    *,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    result = load_snapshot_json(snapshot_path, project_root=project_root)
    if result.status != "OK" or not isinstance(result.data, Mapping):
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            result.reason,
            schema_version=schema_version,
            source_status=result.status,
            source_path=result.path,
        )

    snapshot = dict(result.data)
    contract_errors = validate_dashboard_snapshot(snapshot)
    if contract_errors:
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            "invalid_schema:" + ";".join(contract_errors),
            schema_version=schema_version or str(snapshot.get("schema_version") or "unknown"),
            source_status="INVALID_SCHEMA",
            source_path=result.path,
        )
    if schema_version is not None and snapshot.get("schema_version") != schema_version:
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            "schema_version_mismatch",
            schema_version=schema_version,
            source_status="INVALID_SCHEMA",
            source_path=result.path,
        )
    return snapshot


def build_unknown_snapshot(
    snapshot_name: str,
    reason: str,
    *,
    schema_version: str | None = None,
    source_status: str = "UNKNOWN",
    source_path: str | None = None,
) -> dict[str, Any]:
    return {
        "status": DashboardSectionStatus.UNKNOWN.value,
        "reason": reason,
        "source_status": source_status,
        "freshness_status": "UNKNOWN",
        "source_path": source_path,
        "schema_version": schema_version or "unknown",
        "runtime_mode": RuntimeMode.unknown.value,
        "paper_only": True,
        "shadow_only": True,
        "dashboard_readonly": True,
        "live_locked": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "last_updated_utc": None,
        "loader_checked_at_utc": utc_now_iso(),
        "sections": {
            "snapshot": {
                "status": DashboardSectionStatus.UNKNOWN.value,
                "reason": reason,
                "source_status": source_status,
                "data": None,
            }
        },
        "audit": DashboardAuditContract(snapshot_source=snapshot_name).to_dict(),
    }


def get_snapshot_last_updated(snapshot: Mapping[str, Any]) -> str | None:
    value = snapshot.get("last_updated_utc")
    return str(value) if value not in (None, "") else None
