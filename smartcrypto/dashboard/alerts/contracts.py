from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import utc_now_iso


class NotificationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    PANIC = "PANIC"


class NotificationChannel(str, Enum):
    LOG = "LOG"
    TELEGRAM = "TELEGRAM"
    NTFY = "NTFY"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


class NotificationDispatchStatus(str, Enum):
    DRY_RUN_ACCEPTED = "DRY_RUN_ACCEPTED"
    DRY_RUN_REJECTED = "DRY_RUN_REJECTED"
    HARD_BLOCKED = "HARD_BLOCKED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NotificationIntent:
    notification_id: str
    severity: NotificationSeverity | str
    channels: list[NotificationChannel | str] = field(default_factory=list)
    title: str = "SMART FUTUROS notification stub"
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=utc_now_iso)
    source: str = "dashboard_stub"

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class NotificationRoutingPolicy:
    severity: NotificationSeverity
    channels: list[NotificationChannel]
    dry_run_only: bool = True
    enabled: bool = True
    reason: str = "notification_stub_policy"

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class NotificationDispatchResult:
    notification_id: str
    severity: NotificationSeverity | str
    channels: list[NotificationChannel]
    status: NotificationDispatchStatus
    accepted: bool
    sent: bool = False
    dry_run: bool = True
    reason: str = "notification_stub_only"
    simulated_delivery: dict[str, Any] = field(default_factory=dict)
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
