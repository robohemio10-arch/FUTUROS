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
from smartcrypto.ops.dashboard_snapshots.file_loader import load_json_file


def load_dashboard_snapshot(
    snapshot_path: str | Path,
    schema_version: str | None = None,
) -> dict[str, Any]:
    result = load_json_file(snapshot_path)
    if result.status is not DashboardSectionStatus.OK or not isinstance(result.data, Mapping):
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            result.error or result.status.value.lower(),
            schema_version=schema_version,
        )

    snapshot = dict(result.data)
    contract_errors = validate_dashboard_snapshot(snapshot)
    if contract_errors:
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            ";".join(contract_errors),
            schema_version=schema_version or str(snapshot.get("schema_version") or "unknown"),
        )
    if schema_version is not None and snapshot.get("schema_version") != schema_version:
        return build_unknown_snapshot(
            Path(snapshot_path).name,
            "schema_version_mismatch",
            schema_version=schema_version,
        )
    return snapshot


def build_unknown_snapshot(
    snapshot_name: str,
    reason: str,
    *,
    schema_version: str | None = None,
) -> dict[str, Any]:
    return {
        "status": DashboardSectionStatus.UNKNOWN.value,
        "reason": reason,
        "schema_version": schema_version or "unknown",
        "runtime_mode": RuntimeMode.unknown.value,
        "dashboard_readonly": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": utc_now_iso(),
        "sections": {
            "snapshot": {
                "status": DashboardSectionStatus.UNKNOWN.value,
                "reason": reason,
                "data": None,
            }
        },
        "audit": DashboardAuditContract(snapshot_source=snapshot_name).to_dict(),
    }


def get_snapshot_last_updated(snapshot: Mapping[str, Any]) -> str | None:
    value = snapshot.get("last_updated_utc")
    return str(value) if value not in (None, "") else None
