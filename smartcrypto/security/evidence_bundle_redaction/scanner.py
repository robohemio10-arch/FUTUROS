"""Allowlist scanner for files, directories, and ZIP archives."""

from __future__ import annotations

import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from .contracts import ScanResult
from .redactor import redact_text

FORBIDDEN_SUFFIXES = frozenset(
    {
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".parquet",
        ".joblib",
        ".pkl",
        ".pickle",
    }
)
FORBIDDEN_NAMES = frozenset({".env", "credentials.json", "credential.json", "auth.json", "secrets.json"})
FORBIDDEN_PARTS = frozenset({".git", "runtime", "__pycache__"})
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:/")
COMPOSE_NAME = re.compile(r"(?i)(?:docker[-_]?compose|compose[-_]?config)")


def scan_source(
    source: Path,
    *,
    allowed_files: Iterable[str] = (),
    max_file_bytes: int = 2_000_000,
    compose_output_mode: str = "not-compose",
) -> ScanResult:
    """Scan a source without copying or extracting it."""

    normalized_allowlist = normalize_allowlist(allowed_files)
    if source.is_dir():
        return scan_directory(
            source,
            allowed_files=normalized_allowlist,
            max_file_bytes=max_file_bytes,
            compose_output_mode=compose_output_mode,
        )
    if source.is_file() and source.suffix.casefold() == ".zip":
        return scan_zip(
            source,
            allowed_files=normalized_allowlist,
            max_file_bytes=max_file_bytes,
            compose_output_mode=compose_output_mode,
        )
    if source.is_file():
        effective = normalized_allowlist or {source.name.replace("\\", "/")}
        result = ScanResult(source_type="file")
        _scan_path(
            source,
            relative_path=source.name.replace("\\", "/"),
            result=result,
            allowed_files=effective,
            max_file_bytes=max_file_bytes,
            compose_output_mode=compose_output_mode,
        )
        return result
    result = ScanResult(source_type="missing")
    result.merge_blocker("input_not_found")
    return result


def scan_directory(
    source: Path,
    *,
    allowed_files: set[str],
    max_file_bytes: int,
    compose_output_mode: str,
) -> ScanResult:
    result = ScanResult(source_type="directory")
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            result.unsafe_archive_entry_count += 1
            result.blocked_file_count += 1
            result.merge_blocker(f"unsafe_symlink:{relative}")
            continue
        if not path.is_file():
            continue
        _scan_path(
            path,
            relative_path=relative,
            result=result,
            allowed_files=allowed_files,
            max_file_bytes=max_file_bytes,
            compose_output_mode=compose_output_mode,
        )
    return result


def scan_zip(
    source: Path,
    *,
    allowed_files: set[str],
    max_file_bytes: int,
    compose_output_mode: str,
) -> ScanResult:
    """Inspect ZIP members in-place; no extraction is performed."""

    result = ScanResult(source_type="zip")
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            result.scanned_archive_entry_count = len([info for info in infos if not info.is_dir()])
            names = [normalize_relative_path(info.filename) for info in infos if not info.is_dir()]
            duplicates = {name for name in names if names.count(name) > 1}
            for duplicate in sorted(duplicates):
                result.unsafe_archive_entry_count += 1
                result.merge_blocker(f"duplicate_archive_entry:{duplicate}")

            for info in sorted(infos, key=lambda item: normalize_relative_path(item.filename)):
                if info.is_dir():
                    continue
                relative = normalize_relative_path(info.filename)
                unsafe_reason = archive_entry_unsafe_reason(info, relative)
                if unsafe_reason:
                    result.unsafe_archive_entry_count += 1
                    result.blocked_file_count += 1
                    result.merge_blocker(f"{unsafe_reason}:{relative}")
                    continue
                if relative in duplicates:
                    result.blocked_file_count += 1
                    continue
                if info.file_size > max_file_bytes:
                    result.blocked_file_count += 1
                    result.merge_blocker(f"file_too_large:{relative}")
                    continue
                policy_reason = file_policy_block_reason(relative, allowed_files)
                if policy_reason:
                    _record_policy_block(result, relative, policy_reason)
                    if policy_reason == "forbidden_file":
                        try:
                            payload = archive.read(info)
                        except (OSError, RuntimeError, zipfile.BadZipFile):
                            continue
                        _scan_findings_only(payload, relative_path=relative, result=result)
                    continue
                try:
                    payload = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    result.blocked_file_count += 1
                    result.merge_blocker(f"unreadable_archive_entry:{relative}")
                    continue
                _scan_bytes(
                    payload,
                    relative_path=relative,
                    result=result,
                    compose_output_mode=compose_output_mode,
                )
    except (OSError, zipfile.BadZipFile):
        result.merge_blocker("invalid_or_unreadable_zip")
        result.unsafe_archive_entry_count += 1
    return result


def normalize_allowlist(values: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        path = normalize_relative_path(str(value))
        if path and not is_unsafe_relative_path(path):
            normalized.add(path)
    return normalized


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_unsafe_relative_path(relative_path: str) -> bool:
    raw = relative_path.replace("\\", "/")
    if raw.startswith("/") or WINDOWS_ABSOLUTE.match(raw):
        return True
    return ".." in PurePosixPath(raw).parts


def archive_entry_unsafe_reason(info: zipfile.ZipInfo, relative_path: str) -> str | None:
    if is_unsafe_relative_path(info.filename.replace("\\", "/")):
        return "unsafe_archive_path"
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        return "archive_symlink"
    if not relative_path:
        return "empty_archive_path"
    return None


def file_policy_block_reason(relative_path: str, allowed_files: set[str]) -> str | None:
    normalized = normalize_relative_path(relative_path)
    path = PurePosixPath(normalized)
    lowered_name = path.name.casefold()
    lowered_parts = {part.casefold() for part in path.parts}
    suffix = path.suffix.casefold()
    if (
        lowered_name in FORBIDDEN_NAMES
        or lowered_name.startswith(".env.")
        or suffix in FORBIDDEN_SUFFIXES
        or lowered_parts & FORBIDDEN_PARTS
        or ("registries" in lowered_parts and "active" in lowered_parts)
        or ("models" in lowered_parts and "active" in lowered_parts)
        or "environment_dump" in lowered_name
        or "process_dump" in lowered_name
        or (lowered_name.endswith(".json") and any(term in lowered_name for term in ("credential", "auth")))
    ):
        return "forbidden_file"
    if normalized not in allowed_files:
        return "allowlist_violation"
    return None


def _scan_path(
    path: Path,
    *,
    relative_path: str,
    result: ScanResult,
    allowed_files: set[str],
    max_file_bytes: int,
    compose_output_mode: str,
) -> None:
    if path.stat().st_size > max_file_bytes:
        result.blocked_file_count += 1
        result.merge_blocker(f"file_too_large:{relative_path}")
        return
    policy_reason = file_policy_block_reason(relative_path, allowed_files)
    if policy_reason:
        _record_policy_block(result, relative_path, policy_reason)
        if policy_reason == "forbidden_file":
            try:
                payload = path.read_bytes()
            except OSError:
                return
            _scan_findings_only(payload, relative_path=relative_path, result=result)
        return
    try:
        payload = path.read_bytes()
    except OSError:
        result.blocked_file_count += 1
        result.merge_blocker(f"unreadable_file:{relative_path}")
        return
    _scan_bytes(
        payload,
        relative_path=relative_path,
        result=result,
        compose_output_mode=compose_output_mode,
    )


def _scan_bytes(
    payload: bytes,
    *,
    relative_path: str,
    result: ScanResult,
    compose_output_mode: str,
) -> None:
    result.scanned_file_count += 1
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.blocked_file_count += 1
        result.forbidden_file_count += 1
        result.merge_blocker(f"binary_or_non_utf8_file:{relative_path}")
        return
    if "\x00" in text:
        result.blocked_file_count += 1
        result.forbidden_file_count += 1
        result.merge_blocker(f"binary_file:{relative_path}")
        return

    redaction = redact_text(text, relative_path=relative_path)
    result.allowed_file_count += 1
    result.findings.extend(redaction.findings)
    result.sanitized_files[relative_path] = redaction.redacted_text.encode("utf-8")
    if redaction.changed:
        result.redacted_file_count += 1

    if COMPOSE_NAME.search(PurePosixPath(relative_path).name):
        result.compose_file_count += 1
        if compose_output_mode == "interpolated" or (
            compose_output_mode != "no-interpolate" and redaction.changed
        ):
            result.compose_interpolation_detected = True
            result.merge_blocker("compose_interpolation_detected")


def _record_policy_block(result: ScanResult, relative_path: str, reason: str) -> None:
    result.blocked_file_count += 1
    if reason == "forbidden_file":
        result.forbidden_file_count += 1
    else:
        result.allowlist_violation_count += 1
    result.merge_blocker(f"{reason}:{relative_path}")


def _scan_findings_only(payload: bytes, *, relative_path: str, result: ScanResult) -> None:
    """Detect secrets in an explicitly supplied forbidden text file.

    The content is never added to ``sanitized_files`` and therefore can never
    enter a bundle. This preserves both requirements: the file is blocked by
    policy even when clean, and a secret finding is still reported safely.
    """

    result.scanned_file_count += 1
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return
    if "\x00" in text:
        return
    result.findings.extend(redact_text(text, relative_path=relative_path).findings)
