from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION = "dashboard_global_status_snapshot_v1"
DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION = "dashboard_infrastructure_snapshot_v1"
DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION = "dashboard_portfolio_risk_snapshot_v1"
DASHBOARD_GRID_MONITOR_SCHEMA_VERSION = "dashboard_grid_monitor_snapshot_v1"
DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION = "dashboard_opportunity_scanner_snapshot_v1"
DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION = "dashboard_ai_governance_snapshot_v1"
DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION = "dashboard_active_controls_snapshot_v1"
DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION = "dashboard_quantitative_reports_snapshot_v1"
DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION = "dashboard_alerts_messaging_snapshot_v1"
DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION = "dashboard_snapshot_build_summary_v1"


class DashboardSectionStatus(str, Enum):
    OK = "OK"
    UNKNOWN = "UNKNOWN"
    MISSING_OPTIONAL = "MISSING_OPTIONAL"
    MISSING_REQUIRED = "MISSING_REQUIRED"
    STALE = "STALE"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class SourceKind(str, Enum):
    REQUIRED_EXISTING_SOURCE = "REQUIRED_EXISTING_SOURCE"
    OPTIONAL_EXISTING_SOURCE = "OPTIONAL_EXISTING_SOURCE"
    FUTURE_SOURCE = "FUTURE_SOURCE"
    GENERATED_BY_THIS_BRANCH = "GENERATED_BY_THIS_BRANCH"


class RuntimeMode(str, Enum):
    paper = "paper"
    shadow = "shadow"
    backtest = "backtest"
    unknown = "unknown"


class HardBlockStatus(str, Enum):
    HARD_BLOCKED = "HARD_BLOCKED"
    READ_ONLY = "READ_ONLY"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class DashboardPageId(str, Enum):
    infrastructure = "infrastructure"
    portfolio_risk = "portfolio_risk"
    grid_monitor = "grid_monitor"
    opportunity_scanner = "opportunity_scanner"
    ai_governance = "ai_governance"
    active_controls = "active_controls"
    quantitative_reports = "quantitative_reports"
    alerts_messaging = "alerts_messaging"


REQUIRED_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "runtime_mode",
        "live_locked",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "last_updated_utc",
        "sections",
        "audit",
    }
)

REQUIRED_AUDIT_FIELDS = frozenset(
    {
        "dashboard_reads_only",
        "uses_private_exchange",
        "uses_ccxt",
        "sends_orders",
        "changes_risk",
        "promotes_model",
        "changes_config",
        "changes_model",
        "changes_active_signals",
        "snapshot_source",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DashboardAuditContract:
    dashboard_reads_only: bool = True
    uses_private_exchange: bool = False
    uses_ccxt: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    promotes_model: bool = False
    changes_config: bool = False
    changes_model: bool = False
    changes_active_signals: bool = False
    snapshot_source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DashboardSectionContract:
    status: DashboardSectionStatus = DashboardSectionStatus.UNKNOWN
    reason: str = "not_built"
    data: Any = None
    source_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = _enum_value(self.status)
        payload["source_paths"] = list(self.source_paths)
        return payload


@dataclass(frozen=True)
class DashboardSourceContract:
    page_id: DashboardPageId
    path: str
    source_kind: SourceKind
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "page_id": self.page_id.value,
            "path": self.path,
            "source_kind": self.source_kind.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class DashboardSnapshotBase:
    schema_version: str
    runtime_mode: RuntimeMode = RuntimeMode.unknown
    dashboard_readonly: bool = True
    live_locked: bool = True
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    last_updated_utc: str = field(default_factory=utc_now_iso)
    sections: dict[str, Any] = field(default_factory=dict)
    audit: DashboardAuditContract = field(default_factory=DashboardAuditContract)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "runtime_mode": _enum_value(self.runtime_mode),
            "dashboard_readonly": self.dashboard_readonly,
            "live_locked": self.live_locked,
            "order_submission_enabled": self.order_submission_enabled,
            "real_order_submission_enabled": self.real_order_submission_enabled,
            "last_updated_utc": self.last_updated_utc,
            "sections": _serialize_sections(self.sections),
            "audit": self.audit.to_dict(),
        }


@dataclass(frozen=True)
class DashboardBuildResult:
    status: DashboardSectionStatus
    snapshot: dict[str, Any] | None = None
    reason: str | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardLoadResult:
    exists: bool
    status: DashboardSectionStatus
    path: str
    data: Any = None
    error: str | None = None
    source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE
    loaded_at_utc: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["source_kind"] = self.source_kind.value
        return payload


def validate_dashboard_snapshot(payload: Mapping[str, Any]) -> list[str]:
    errors = [f"missing_field:{name}" for name in sorted(REQUIRED_SNAPSHOT_FIELDS - payload.keys())]
    if "dashboard_readonly" not in payload and "dashboard_readonly_by_default" not in payload:
        errors.append("missing_field:dashboard_readonly")

    audit = payload.get("audit")
    if not isinstance(audit, Mapping):
        if "audit" in payload:
            errors.append("invalid_field:audit")
        return errors

    errors.extend(
        f"missing_audit_field:{name}" for name in sorted(REQUIRED_AUDIT_FIELDS - audit.keys())
    )
    return errors


def _serialize_sections(sections: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in sections.items():
        output[str(key)] = value.to_dict() if isinstance(value, DashboardSectionContract) else value
    return output


def _enum_value(value: Enum | str) -> str:
    return str(value.value if isinstance(value, Enum) else value)
