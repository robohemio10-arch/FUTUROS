from __future__ import annotations

from collections.abc import Iterable

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardSectionStatus


BLOCKING_STATUSES = {
    DashboardSectionStatus.ERROR,
    DashboardSectionStatus.BLOCKED,
    DashboardSectionStatus.MISSING_REQUIRED,
}

STATUS_SEVERITY = {
    DashboardSectionStatus.OK: 0,
    DashboardSectionStatus.UNKNOWN: 1,
    DashboardSectionStatus.MISSING_OPTIONAL: 1,
    DashboardSectionStatus.STALE: 2,
    DashboardSectionStatus.WARNING: 2,
    DashboardSectionStatus.DEGRADED: 3,
    DashboardSectionStatus.MISSING_REQUIRED: 4,
    DashboardSectionStatus.BLOCKED: 5,
    DashboardSectionStatus.ERROR: 6,
}


def normalize_section_status(value: object) -> DashboardSectionStatus:
    if isinstance(value, DashboardSectionStatus):
        return value
    normalized = str(value or "").strip().upper()
    try:
        return DashboardSectionStatus(normalized)
    except ValueError:
        return DashboardSectionStatus.UNKNOWN


def is_blocking_status(status: object) -> bool:
    return normalize_section_status(status) in BLOCKING_STATUSES


def merge_section_statuses(statuses: Iterable[object]) -> DashboardSectionStatus:
    normalized = [normalize_section_status(status) for status in statuses]
    if not normalized:
        return DashboardSectionStatus.UNKNOWN
    return max(normalized, key=status_to_severity)


def status_to_severity(status: object) -> int:
    return STATUS_SEVERITY[normalize_section_status(status)]


def status_to_label(status: object) -> str:
    return normalize_section_status(status).value.replace("_", " ").title()
