from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import utc_now_iso


class DashboardCommandLevel(str, Enum):
    N1_LOCAL_INFO = "N1_LOCAL_INFO"
    N2_DRY_RUN_STUB = "N2_DRY_RUN_STUB"
    N3_DRY_RUN_STUB_SENSITIVE = "N3_DRY_RUN_STUB_SENSITIVE"
    N4_HARD_BLOCKED = "N4_HARD_BLOCKED"


class DashboardCommandStatus(str, Enum):
    DRY_RUN_ACCEPTED = "DRY_RUN_ACCEPTED"
    DRY_RUN_REJECTED = "DRY_RUN_REJECTED"
    HARD_BLOCKED = "HARD_BLOCKED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class DashboardCommandRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DashboardCommandSideEffect(str, Enum):
    NONE = "NONE"
    WOULD_REQUEST_REFRESH = "WOULD_REQUEST_REFRESH"
    WOULD_REQUEST_PAPER_ACTION = "WOULD_REQUEST_PAPER_ACTION"
    WOULD_REQUEST_RISK_CHANGE = "WOULD_REQUEST_RISK_CHANGE"
    WOULD_REQUEST_NOTIFICATION = "WOULD_REQUEST_NOTIFICATION"
    WOULD_REQUEST_LIVE_ACTION = "WOULD_REQUEST_LIVE_ACTION"


@dataclass(frozen=True)
class DashboardCommandIntent:
    command_id: str
    command_name: str
    requested_level: DashboardCommandLevel | str | None = None
    requested_by: str = "dashboard_operator"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=utc_now_iso)
    source: str = "dashboard_stub"

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class DashboardCommandPolicy:
    command_name: str
    level: DashboardCommandLevel
    risk: DashboardCommandRisk
    side_effect: DashboardCommandSideEffect
    enabled: bool
    dry_run_only: bool
    hard_blocked: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class DashboardCommandResult:
    command_id: str
    command_name: str
    level: DashboardCommandLevel
    status: DashboardCommandStatus
    accepted: bool
    executed: bool = False
    dry_run: bool = True
    hard_blocked: bool = False
    reason: str = "stub_only"
    simulated_effect: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
