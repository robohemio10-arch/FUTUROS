from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_shadow_soak_anchor_continuity_pack_v1"
PROJECT_NAME = "SMART FUTUROS"
DEFAULT_DASHBOARD_NAME = "SMART FUTUROS Command Center"
DEFAULT_OUTPUT_PATH = Path("data/reports/paper_shadow_soak_anchor_continuity_pack.json")
DEFAULT_DIAGNOSTIC_SOAK_DAYS = 7
DEFAULT_REQUIRED_SOAK_DAYS = 30


class SoakAnchorStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    EVIDENCE_MISSING = "evidence_missing"


class SoakGateStatus(str, Enum):
    REACHED = "reached"
    NOT_REACHED = "not_reached"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SoakEvidenceSource:
    name: str
    path: str
    required_for_anchor: bool
    required_for_readiness: bool
    description: str


@dataclass(frozen=True)
class SoakAnchorConfig:
    project_root: Path = Path(".")
    output: Path = DEFAULT_OUTPUT_PATH
    diagnostic_soak_days: int = DEFAULT_DIAGNOSTIC_SOAK_DAYS
    required_soak_days: int = DEFAULT_REQUIRED_SOAK_DAYS
    write: bool = False


@dataclass(frozen=True)
class SoakAnchorAuditResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool
