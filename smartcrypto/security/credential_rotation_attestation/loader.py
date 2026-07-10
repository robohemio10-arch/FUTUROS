"""Safe JSON input loader integrated with evidence-bundle redaction."""

from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Any, Mapping

from smartcrypto.security.evidence_bundle_redaction import scan_source

from .contracts import LoadedJsonInput

ALLOWED_SUFFIXES = frozenset({".json"})
FORBIDDEN_SEMANTIC_FIELD_TERMS = frozenset(
    {
        "authorization",
        "credential_hash",
        "credential_prefix",
        "credential_suffix",
        "credential_value",
        "fingerprint",
        "last_characters",
        "last_four",
        "password",
        "secret_hash",
        "secret_prefix",
        "secret_suffix",
        "token_hash",
        "token_hint",
        "token_prefix",
        "token_suffix",
        "username",
    }
)


def load_sanitized_json_input(
    *,
    project_root: Path,
    raw_path: str | Path | None,
    max_file_bytes: int,
) -> LoadedJsonInput:
    """Validate metadata and secret safety before parsing JSON."""

    if raw_path is None:
        return LoadedJsonInput(None, None, "blocked", "input_not_found", blockers=("input_not_found",))

    raw_candidate = Path(raw_path)
    if not raw_candidate.is_absolute() and ".." in PurePath(raw_candidate).parts:
        return LoadedJsonInput(
            None,
            str(raw_candidate).replace("\\", "/"),
            "blocked",
            "unsafe_input_path",
            blockers=("relative_path_traversal",),
        )
    candidate = raw_candidate if raw_candidate.is_absolute() else project_root / raw_candidate
    display_path = _display_path(project_root, candidate)
    if candidate.is_symlink():
        return LoadedJsonInput(candidate, display_path, "blocked", "unsafe_input_path", blockers=("symlink_input",))
    if not candidate.exists() or not candidate.is_file():
        return LoadedJsonInput(candidate, display_path, "blocked", "input_not_found", blockers=("input_not_found",))
    if candidate.suffix.casefold() not in ALLOWED_SUFFIXES:
        return LoadedJsonInput(
            candidate,
            display_path,
            "blocked",
            "unsafe_input_path",
            blockers=("unsupported_input_extension",),
        )
    try:
        before = candidate.stat()
    except OSError:
        return LoadedJsonInput(candidate, display_path, "blocked", "unsafe_input_path", blockers=("unreadable_input",))
    if before.st_size > max_file_bytes:
        return LoadedJsonInput(candidate, display_path, "blocked", "unsafe_input_path", blockers=("input_too_large",))

    scan = scan_source(candidate, max_file_bytes=max_file_bytes)
    if scan.findings:
        return LoadedJsonInput(
            candidate,
            display_path,
            "blocked",
            "secret_material_detected",
            secret_finding_count=len(scan.findings),
            blockers=("secret_material_detected",),
        )
    if scan.blockers:
        return LoadedJsonInput(
            candidate,
            display_path,
            "blocked",
            "unsafe_input_path",
            blockers=tuple(sorted(scan.blockers)),
        )

    try:
        raw = candidate.read_bytes()
        after = candidate.stat()
    except OSError:
        return LoadedJsonInput(candidate, display_path, "blocked", "unsafe_input_path", blockers=("unreadable_input",))
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return LoadedJsonInput(candidate, display_path, "blocked", "unsafe_input_path", blockers=("input_changed_during_scan",))
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return LoadedJsonInput(candidate, display_path, "blocked", "invalid_json", blockers=("invalid_json",))
    if not isinstance(payload, dict):
        return LoadedJsonInput(candidate, display_path, "blocked", "invalid_json", blockers=("json_root_not_object",))

    semantic_count = count_forbidden_semantic_fields(payload)
    if semantic_count:
        return LoadedJsonInput(
            candidate,
            display_path,
            "blocked",
            "secret_material_detected",
            semantic_secret_field_count=semantic_count,
            blockers=("forbidden_secret_metadata_field",),
        )
    return LoadedJsonInput(candidate, display_path, "ok", "sanitized_json_loaded", payload=payload)


def count_forbidden_semantic_fields(value: Any) -> int:
    count = 0
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in FORBIDDEN_SEMANTIC_FIELD_TERMS:
                count += 1
            if normalized == "credential_id" and _looks_like_secret_fingerprint(nested):
                count += 1
            count += count_forbidden_semantic_fields(nested)
    elif isinstance(value, list):
        count += sum(count_forbidden_semantic_fields(item) for item in value)
    return count


def _looks_like_secret_fingerprint(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) >= 32 and all(character in "0123456789abcdefABCDEF" for character in text)


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
