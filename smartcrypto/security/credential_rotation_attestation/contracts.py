"""Closed contracts for credential-rotation attestation validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "credential_rotation_attestation_gate_v1"
INVENTORY_SCHEMA_VERSION = "credential_rotation_required_inventory_v1"
ATTESTATION_SCHEMA_VERSION = "credential_rotation_attestation_v1"

ALLOWED_ROTATION_STATUSES = frozenset({"revoked", "rotated", "not_applicable", "unverified"})
ALLOWED_VERIFICATION_METHODS = frozenset(
    {
        "provider_console",
        "provider_admin_api_manual",
        "secret_manager_manual",
        "infrastructure_admin_console",
        "documented_not_applicable",
    }
)
RESOLVED_ROTATION_STATUSES = frozenset({"revoked", "rotated", "not_applicable"})


@dataclass(frozen=True)
class LoadedJsonInput:
    path: Path | None
    display_path: str | None
    status: str
    reason: str
    payload: Mapping[str, Any] | None = None
    secret_finding_count: int = 0
    semantic_secret_field_count: int = 0
    blockers: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status == "ok" and self.payload is not None


@dataclass
class ValidationState:
    inventory_errors: list[str] = field(default_factory=list)
    attestation_errors: list[str] = field(default_factory=list)
    timestamp_errors: list[str] = field(default_factory=list)
    stale_attestation_count: int = 0
    duplicate_credential_count: int = 0
    missing_credential_count: int = 0
    unknown_credential_count: int = 0
    unverified_count: int = 0
    dual_control_failure_count: int = 0
    credential_results: list[dict[str, Any]] = field(default_factory=list)
