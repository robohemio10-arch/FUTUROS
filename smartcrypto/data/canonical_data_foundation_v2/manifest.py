"""Deterministic execution manifests persisted with the certified B01 writer."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWritePolicy,
    atomic_write_json,
)

from .contracts import SAFETY_FLAGS, canonical_json, json_safe, stable_hash

EXECUTION_MANIFEST_SCHEMA_VERSION = "canonical_execution_manifest_v2"
SUPPORTED_EXECUTION_TYPES = frozenset(
    {
        "dataset_build",
        "feature_build",
        "target_build",
        "split",
        "backtest",
        "training",
        "quantitative_evaluation",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SAFE_EXECUTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SENSITIVE_ARGUMENT_PATTERN = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


class ManifestValidationError(ValueError):
    """Fail-closed invalid or unsafe execution manifest."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExecutionManifest:
    envelope: Mapping[str, Any]
    canonical_payload: Mapping[str, Any]
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **dict(self.envelope),
            "canonical_payload": dict(self.canonical_payload),
            "content_hash": self.content_hash,
        }


def build_execution_manifest(
    *,
    execution_id: str,
    execution_type: str,
    execution_started_at_utc: str,
    execution_completed_at_utc: str,
    project: str,
    branch: str,
    commit_sha: str | None,
    dirty_worktree: bool,
    containerized: bool,
    container_digest: str | None,
    runtime_environment: Mapping[str, Any],
    python_version: str,
    dependency_lock_hash: str | None,
    dataset_id: str,
    dataset_hash: str,
    dataset_manifest_hash: str,
    feature_contract_hash: str | None,
    target_store_hash: str | None,
    split_hash: str | None,
    cost_model_hash: str | None,
    config_hash: str,
    schema_hash: str,
    source_hashes: Mapping[str, str],
    seed: int,
    command: str,
    arguments: Sequence[str],
    row_count: int,
    status: str,
    blockers: Sequence[str] = (),
    warnings: Sequence[str] = (),
    safety_flags: Mapping[str, bool] = SAFETY_FLAGS,
) -> ExecutionManifest:
    """Build an envelope whose content hash excludes volatile execution fields."""

    if execution_type not in SUPPORTED_EXECUTION_TYPES:
        raise ManifestValidationError("unsupported_execution_type")
    if not SAFE_EXECUTION_ID_PATTERN.fullmatch(execution_id):
        raise ManifestValidationError("unsafe_execution_id")
    if row_count < 0:
        raise ManifestValidationError("negative_manifest_row_count")
    _require_sha256("dataset_hash", dataset_hash)
    _require_sha256("dataset_manifest_hash", dataset_manifest_hash)
    _require_sha256("config_hash", config_hash)
    _require_sha256("schema_hash", schema_hash)
    for name, value in source_hashes.items():
        _require_sha256(f"source_hash:{name}", value)
    for name, optional_value in (
        ("dependency_lock_hash", dependency_lock_hash),
        ("feature_contract_hash", feature_contract_hash),
        ("target_store_hash", target_store_hash),
        ("split_hash", split_hash),
        ("cost_model_hash", cost_model_hash),
    ):
        if optional_value is not None:
            _require_sha256(name, optional_value)

    resolved_blockers = sorted(set(str(item) for item in blockers))
    resolved_warnings = sorted(set(str(item) for item in warnings))
    if not commit_sha or not COMMIT_PATTERN.fullmatch(commit_sha.lower()):
        resolved_blockers.append("commit_sha_unresolved")
        normalized_commit = None
    else:
        normalized_commit = commit_sha.lower()
    if dirty_worktree:
        resolved_blockers.append("dirty_worktree_blocks_release")
    if containerized:
        if container_digest is None:
            resolved_blockers.append("container_digest_required")
            container_status = "missing_required_digest"
        else:
            _require_container_digest(container_digest)
            container_status = "containerized_digest_verified"
    else:
        if container_digest is not None:
            raise ManifestValidationError("local_execution_cannot_claim_container_digest")
        container_status = "not_containerized"

    canonical_payload = {
        "schema_version": EXECUTION_MANIFEST_SCHEMA_VERSION,
        "execution_type": execution_type,
        "project": project,
        "branch": branch,
        "commit_sha": normalized_commit,
        "dirty_worktree": bool(dirty_worktree),
        "container": {
            "status": container_status,
            "digest": container_digest,
        },
        "runtime_environment": json_safe(runtime_environment),
        "python_version": python_version,
        "dependency_lock_hash": dependency_lock_hash,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": dataset_manifest_hash,
        "feature_contract_hash": feature_contract_hash,
        "target_store_hash": target_store_hash,
        "split_hash": split_hash,
        "cost_model_hash": cost_model_hash,
        "config_hash": config_hash,
        "schema_hash": schema_hash,
        "source_hashes": dict(sorted(source_hashes.items())),
        "seed": int(seed),
        "command": command,
        "arguments_sanitized": sanitize_arguments(arguments),
        "row_count": int(row_count),
        "status": status,
        "blockers": sorted(set(resolved_blockers)),
        "warnings": resolved_warnings,
        "safety_flags": dict(sorted(safety_flags.items())),
        "release_eligible": not resolved_blockers and status == "ok",
    }
    content_hash = stable_hash(canonical_payload)
    envelope = {
        "execution_id": execution_id,
        "execution_started_at_utc": execution_started_at_utc,
        "execution_completed_at_utc": execution_completed_at_utc,
    }
    canonical_json({"envelope": envelope, "canonical_payload": canonical_payload})
    return ExecutionManifest(
        envelope=envelope,
        canonical_payload=canonical_payload,
        content_hash=content_hash,
    )


def write_execution_manifest(
    *,
    manifest: ExecutionManifest,
    output_root: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Append one immutable manifest using B01 atomic writing."""

    root = Path(project_root).resolve()
    requested_root = Path(output_root)
    target_root = requested_root if requested_root.is_absolute() else root / requested_root
    target_root = target_root.resolve(strict=False)
    reports_root = (root / "data" / "reports").resolve(strict=False)
    try:
        target_root.relative_to(reports_root)
    except ValueError as exc:
        raise ManifestValidationError("manifest_output_outside_research_reports") from exc
    execution_id = str(manifest.envelope["execution_id"])
    target = (
        target_root
        / manifest.content_hash
        / f"{execution_id}.json"
    )
    if target.exists():
        raise ManifestValidationError("execution_manifest_already_exists")
    policy = AtomicWritePolicy.restricted(
        [target_root],
        working_directory=root,
    )
    result = atomic_write_json(
        target,
        manifest.to_dict(),
        policy=policy,
        allow_nan=False,
    )
    return {
        "status": result.status,
        "reason": "execution_manifest_written_atomically",
        "manifest_path": _display(target, root),
        "manifest_content_hash": manifest.content_hash,
        "write_performed": result.write_performed,
        "atomic_writer": "integrity_traceability_v2.atomic_writer",
        "previous_manifest_overwritten": False,
    }


def sanitize_arguments(arguments: Sequence[str]) -> list[str]:
    sanitized: list[str] = []
    redact_next = False
    for raw in arguments:
        value = str(raw)
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        if value.startswith("--") and SENSITIVE_ARGUMENT_PATTERN.search(value):
            if "=" in value:
                name, _separator, _secret = value.partition("=")
                sanitized.append(f"{name}=[REDACTED]")
            else:
                sanitized.append(value)
                redact_next = True
            continue
        sanitized.append(value)
    return sanitized


def hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(name: str, value: str) -> None:
    if not SHA256_PATTERN.fullmatch(str(value).lower()):
        raise ManifestValidationError(f"invalid_sha256:{name}")


def _require_container_digest(value: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value.lower()):
        raise ManifestValidationError("invalid_container_digest")


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()
