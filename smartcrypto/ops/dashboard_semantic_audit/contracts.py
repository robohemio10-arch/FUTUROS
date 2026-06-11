from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION = "dashboard_semantic_coverage_audit_v2"
PROJECT_NAME = "SMART FUTUROS"
DASHBOARD_NAME = "SMART FUTUROS Command Center"


class SemanticAuditStatus(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"


class SemanticRequirementStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class SemanticRequirementSeverity(str, Enum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"


@dataclass(frozen=True)
class DashboardSemanticPageContract:
    page_number: str
    page_id: str
    page_title: str
    page_path: str
    snapshot_filename: str
    required_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DashboardSemanticRequirement:
    requirement_id: str
    description: str
    severity: SemanticRequirementSeverity = SemanticRequirementSeverity.REQUIRED
    paths: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        return payload


@dataclass(frozen=True)
class DashboardSemanticFinding:
    requirement_id: str
    status: SemanticRequirementStatus
    severity: SemanticRequirementSeverity
    description: str
    evidence: tuple[str, ...] = ()
    missing_terms: tuple[str, ...] = ()
    forbidden_terms_found: tuple[str, ...] = ()
    missing_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["severity"] = self.severity.value
        payload["evidence"] = list(self.evidence)
        payload["missing_terms"] = list(self.missing_terms)
        payload["forbidden_terms_found"] = list(self.forbidden_terms_found)
        payload["missing_paths"] = list(self.missing_paths)
        return payload


@dataclass(frozen=True)
class DashboardSemanticAuditReport:
    schema_version: str = DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION
    project_name: str = PROJECT_NAME
    dashboard_name: str = DASHBOARD_NAME
    status: SemanticAuditStatus = SemanticAuditStatus.BLOCKED
    summary: dict[str, Any] = field(default_factory=dict)
    findings: tuple[DashboardSemanticFinding, ...] = ()
    safety: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_name": self.project_name,
            "dashboard_name": self.dashboard_name,
            "status": self.status.value,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "safety": dict(self.safety),
        }


def normalize_project_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")
