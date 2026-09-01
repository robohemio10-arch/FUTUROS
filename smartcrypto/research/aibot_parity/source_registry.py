"""Deterministic source provenance for AIBOT benchmark artifacts."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from .contracts import (
    LOADER_VERSION,
    SOURCE_INVESTMENT_ID,
    SOURCE_REGISTRY_SCHEMA_VERSION,
    SourceArtifactRecord,
)


ALLOWED_SOURCE_SUFFIXES = frozenset({".parquet", ".xlsx", ".csv"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceRegistryError(ValueError):
    """Fail-closed source registry validation error."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stream_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_investment_id(value: str) -> str:
    normalized = str(value).strip()
    if normalized != SOURCE_INVESTMENT_ID:
        raise SourceRegistryError("source_investment_id_mismatch")
    return normalized


def resolve_source_artifact(project_root: str | Path, artifact_path: str | Path) -> Path:
    root = Path(project_root).resolve()
    requested = Path(artifact_path)
    source = requested if requested.is_absolute() else root / requested
    if source.is_symlink():
        raise SourceRegistryError("source_artifact_symlink_forbidden")
    try:
        resolved = source.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SourceRegistryError("source_artifact_missing") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceRegistryError("source_artifact_outside_project_root") from exc
    if not resolved.is_file():
        raise SourceRegistryError("source_artifact_not_regular_file")
    if resolved.suffix.casefold() not in ALLOWED_SOURCE_SUFFIXES:
        raise SourceRegistryError("source_artifact_extension_invalid")
    return resolved


def source_batch_id_for_sha256(source_sha256: str) -> str:
    normalized = str(source_sha256).strip().casefold()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise SourceRegistryError("source_artifact_sha256_invalid")
    return f"aibot_sha256_{normalized}"


def build_source_record(
    *,
    project_root: str | Path,
    artifact_path: str | Path,
    source_investment_id: str,
    source_row_count: int,
    source_sha256: str | None = None,
    source_size_bytes: int | None = None,
    loaded_at_utc: str | None = None,
) -> SourceArtifactRecord:
    root = Path(project_root).resolve()
    source = resolve_source_artifact(root, artifact_path)
    investment_id = validate_source_investment_id(source_investment_id)
    artifact_sha = source_sha256 or stream_sha256(source)
    batch_id = source_batch_id_for_sha256(artifact_sha)
    stat = source.stat()
    observed_size = stat.st_size if source_size_bytes is None else int(source_size_bytes)
    if observed_size != stat.st_size:
        raise SourceRegistryError("source_artifact_size_mismatch")
    if source_row_count < 0:
        raise SourceRegistryError("source_row_count_invalid")
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
    return SourceArtifactRecord(
        source_investment_id=investment_id,
        source_batch_id=batch_id,
        source_artifact_path=source.relative_to(root).as_posix(),
        source_artifact_name=source.name,
        source_artifact_sha256=artifact_sha,
        source_artifact_size_bytes=observed_size,
        source_artifact_modified_at=modified_at,
        loaded_at_utc=loaded_at_utc or utc_now_iso(),
        source_row_count=int(source_row_count),
        schema_version=SOURCE_REGISTRY_SCHEMA_VERSION,
        loader_version=LOADER_VERSION,
    )
