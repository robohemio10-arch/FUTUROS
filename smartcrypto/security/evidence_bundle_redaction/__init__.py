"""Fail-closed sanitized evidence bundle security gate."""

from .builder import build_sanitized_evidence_bundle_v1
from .contracts import SCHEMA_VERSION, RedactionResult, ScanResult, SecretFinding
from .redactor import contains_secret, redact_text
from .scanner import scan_directory, scan_source, scan_zip

__all__ = [
    "SCHEMA_VERSION",
    "RedactionResult",
    "ScanResult",
    "SecretFinding",
    "build_sanitized_evidence_bundle_v1",
    "contains_secret",
    "redact_text",
    "scan_directory",
    "scan_source",
    "scan_zip",
]
