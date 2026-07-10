"""Contracts for the evidence-bundle secret redaction gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "evidence_bundle_secret_redaction_gate_v1"


@dataclass(frozen=True)
class SecretFinding:
    finding_id: str
    category: str
    severity: str
    relative_path: str
    line_number: int
    column_number: int
    matched_pattern_name: str
    redacted_preview: str
    secret_fingerprint_sha256: str
    blocking: bool
    remediation: str

    def to_dict(self, *, blocking: bool | None = None) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "relative_path": self.relative_path,
            "line_number": self.line_number,
            "column_number": self.column_number,
            "matched_pattern_name": self.matched_pattern_name,
            "redacted_preview": self.redacted_preview,
            "secret_fingerprint_sha256": self.secret_fingerprint_sha256,
            "blocking": self.blocking if blocking is None else blocking,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    findings: tuple[SecretFinding, ...]

    @property
    def changed(self) -> bool:
        return bool(self.findings)


@dataclass
class ScanResult:
    source_type: str
    scanned_file_count: int = 0
    scanned_archive_entry_count: int = 0
    allowed_file_count: int = 0
    redacted_file_count: int = 0
    blocked_file_count: int = 0
    forbidden_file_count: int = 0
    allowlist_violation_count: int = 0
    unsafe_archive_entry_count: int = 0
    findings: list[SecretFinding] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sanitized_files: dict[str, bytes] = field(default_factory=dict, repr=False)
    compose_file_count: int = 0
    compose_interpolation_detected: bool = False

    def merge_blocker(self, blocker: str) -> None:
        if blocker not in self.blockers:
            self.blockers.append(blocker)
