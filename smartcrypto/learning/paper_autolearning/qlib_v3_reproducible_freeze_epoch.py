"""Immutable and independently reproducible Qlib V3 research freeze.

V2 remains an historical, non-reproducible epoch.  V3 freezes authoritative inputs,
explicit cohorts, canonical matrices, model/calibration/policy artifacts and the exact
implementation lineage before any prospective evidence is admitted.

The freeze and its activation boundary are deliberately separate.  A freeze is first
materialized and certified through two process-isolated rebuilds executed from the
frozen Git source archive.  Only an explicit activation step may later create a
future ``prospective_start_utc``.  The module is research-only and never changes
Freqtrade, RiskManager, active models, registries, runtime state or orders.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning import (
    qlib_long_economic_policy_challenger_v2 as v2,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
)

SCHEMA_VERSION = "qlib_v3_reproducible_freeze_epoch_v1"
FREEZE_SCHEMA_VERSION = "qlib_v3_complete_freeze_manifest_v2"
SOURCE_SCHEMA_VERSION = "qlib_v3_immutable_source_snapshot_v2"
DATASET_SCHEMA_VERSION = "qlib_v3_canonical_semantic_dataset_v1"
MATRIX_SCHEMA_VERSION = "qlib_v3_frozen_imputed_matrix_v1"
CALIBRATION_SCHEMA_VERSION = "qlib_v3_calibration_artifact_v1"
POLICY_SCHEMA_VERSION = "qlib_v3_economic_policy_v2"
REPRODUCTION_SCHEMA_VERSION = "qlib_v3_reproduction_certificate_v1"
LINEAGE_SCHEMA_VERSION = "qlib_v3_git_source_lineage_v1"
ACTIVATION_SCHEMA_VERSION = "qlib_v3_prospective_activation_v1"
EPOCH_VERSION = "v3"
V2_TERMINAL_STATUS = "CLOSED_NONREPRODUCIBLE_EPOCH"
V2_TERMINAL_REASON = "frozen_source_artifact_missing_or_mutated"

DEFAULT_ARTIFACT_ROOT = Path("data/research/qlib_v3")
DEFAULT_REPORT_PATH = Path("data/reports/qlib_v3/qlib_v3_freeze_epoch_v1.json")
DEFAULT_ACTIVATION_REPORT_PATH = Path(
    "data/reports/qlib_v3/qlib_v3_freeze_activation_v1.json"
)
DEFAULT_ACTIVATION_ROOT = Path("data/research/qlib_v3/activations")
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
DEFAULT_MARKET_FEATURES_PATH = Path("data/features/market_features_60d.parquet")
DEFAULT_ACTIVATION_DELAY_SECONDS = 60

MODEL_ARTIFACT_NAME = "model/qlib_lgb_booster.txt"
MODEL_METADATA_NAME = "model/model_metadata.json"
SOURCE_MANIFEST_NAME = "sources/source_manifest.json"
FIT_IDS_NAME = "cohorts/fit_trade_ids.json"
CALIBRATION_IDS_NAME = "cohorts/calibration_trade_ids.json"
DATASET_NAME = "dataset/canonical_dataset.json"
MATRIX_NAME = "dataset/frozen_matrix.json"
IMPUTATION_NAME = "dataset/imputation.json"
CALIBRATION_NAME = "calibration/calibration.json"
POLICY_NAME = "policy/policy.json"
LINEAGE_NAME = "implementation/lineage.json"
SOURCE_ARCHIVE_NAME = "implementation/source_tree.zip"
ENVIRONMENT_NAME = "environment/training_environment.json"
REPRODUCTION_CERTIFICATE_NAME = "certification/reproduction_certificate.json"
FREEZE_CANDIDATE_NAME = "freeze_candidate_v3.json"
FREEZE_MANIFEST_NAME = "freeze_v3.json"

REBUILD_WORKER_PATH = "scripts/rebuild_qlib_v3_frozen_epoch_once_v1.py"
CRITICAL_LINEAGE_PATHS = (
    "smartcrypto/data/feature_builder.py",
    "smartcrypto/execution/freqtrade_contract.py",
    "smartcrypto/learning/paper_autolearning/economic_walkforward_cost_robustness.py",
    "smartcrypto/learning/paper_autolearning/qlib_market_context_economic_challenger.py",
    "smartcrypto/learning/paper_autolearning/qlib_long_economic_policy_challenger_v2.py",
    "smartcrypto/learning/paper_autolearning/qlib_v3_reproducible_freeze_epoch.py",
    "scripts/build_qlib_v3_reproducible_freeze_epoch_v1.py",
    "scripts/rebuild_qlib_v3_frozen_epoch_once_v1.py",
)

LOCKFILE_PATHS = (
    "pyproject.toml",
    "requirements-qlib.lock",
    "requirements-runtime.lock",
    "requirements-dev.lock",
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COPY_CHUNK_BYTES = 8 * 1024 * 1024

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "changes_risk": False,
    "changes_strategy": False,
    "changes_stake": False,
    "changes_leverage": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "historical_backfill_allowed": False,
    "fuzzy_identity_matching_allowed": False,
    "nearest_timestamp_matching_allowed": False,
}


class FreezeV3Error(RuntimeError):
    """Fail-closed V3 error exposing only a stable, non-sensitive reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Clock(Protocol):
    def __call__(self) -> datetime: ...


@dataclass(frozen=True)
class SourceSnapshot:
    logical_id: str
    source_path: Path
    frozen_path: Path
    sha256: str
    bytes: int
    source_modified_at_utc: str
    captured_at_utc: str


@dataclass(frozen=True)
class FrozenPrepared:
    feature_columns: tuple[str, ...]
    train_x: pd.DataFrame
    train_y: pd.Series
    calibration_x: pd.DataFrame
    fit_rows: tuple[dict[str, Any], ...]
    calibration_rows: tuple[dict[str, Any], ...]
    feature_medians: dict[str, float]
    training_cutoff_utc: datetime


@dataclass(frozen=True)
class TrainedEpoch:
    model_text: str
    model_sha256: str
    model_semantic_fingerprint: str
    treatment_semantic_fingerprint: str
    model_metadata: dict[str, Any]
    calibration_scores: np.ndarray
    calibration_payload: dict[str, Any]
    calibration_sha256: str
    threshold: float
    policy_payload: dict[str, Any]
    policy_sha256: str


class FrozenTrainer(Protocol):
    def __call__(
        self,
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> base.NativeQlibLgbFitResult: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _float_hex(value: Any, *, field: str) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise FreezeV3Error(f"non_finite_numeric:{field}")
    return numeric.hex()


def _optional_float_hex(value: Any, *, field: str) -> str | None:
    numeric = base._numeric(value)
    return None if numeric is None else _float_hex(numeric, field=field)


def _required_trade_id(row: Mapping[str, Any]) -> str:
    value = str(row.get("trade_id") or row.get("event_id") or "").strip()
    if not value:
        raise FreezeV3Error("frozen_trade_identity_missing")
    return value


def _time_iso(value: Any, *, field: str) -> str:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise FreezeV3Error(f"datetime_required:{field}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FreezeV3Error(f"timezone_aware_datetime_required:{field}")
    return value.astimezone(UTC).isoformat()


def _parse_utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise FreezeV3Error(f"timestamp_missing:{field}")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FreezeV3Error(f"timestamp_invalid:{field}") from exc
    if parsed.tzinfo is None:
        raise FreezeV3Error(f"timestamp_not_timezone_aware:{field}")
    utc_offset = parsed.utcoffset()
    if utc_offset is None:
        raise FreezeV3Error(f"timestamp_not_timezone_aware:{field}")
    if utc_offset.total_seconds() != 0:
        raise FreezeV3Error(f"timestamp_not_utc:{field}")
    return parsed.astimezone(UTC)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _require_within(path: Path, root: Path, *, reason: str) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise FreezeV3Error(reason) from exc


def _write_new_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FreezeV3Error(f"immutable_artifact_already_exists:{path.name}") from exc


def _write_new_json(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> str:
    content = _canonical_json_bytes(payload)
    _write_new_bytes(path, content)
    return _sha256_bytes(content)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise FreezeV3Error(f"frozen_json_missing_or_unsafe:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FreezeV3Error(f"frozen_json_invalid:{path.name}") from exc
    if not isinstance(payload, dict):
        raise FreezeV3Error(f"frozen_json_object_required:{path.name}")
    return payload


def _stable_copy_source(
    source: Path,
    destination_dir: Path,
    *,
    logical_id: str,
    clock: Clock,
) -> SourceSnapshot:
    if not source.exists() or not source.is_file() or source.is_symlink():
        raise FreezeV3Error(f"source_missing_or_unsafe:{logical_id}")
    if source.suffix.casefold() != ".parquet":
        raise FreezeV3Error(f"source_extension_not_allowed:{logical_id}")

    before = source.stat()
    before_sha = _sha256_file(source)
    destination_dir.mkdir(parents=True, exist_ok=True)
    temporary = destination_dir / f".{logical_id}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            while chunk := reader.read(COPY_CHUNK_BYTES):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())

        copied_sha = _sha256_file(temporary)
        after = source.stat()
        after_sha = _sha256_file(source)
        if (
            before_sha != copied_sha
            or copied_sha != after_sha
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise FreezeV3Error(f"source_changed_during_snapshot:{logical_id}")

        frozen = destination_dir / f"{logical_id}.{copied_sha}.parquet"
        if frozen.exists():
            raise FreezeV3Error(f"content_addressed_source_collision:{logical_id}")
        os.replace(temporary, frozen)
        return SourceSnapshot(
            logical_id=logical_id,
            source_path=source,
            frozen_path=frozen,
            sha256=copied_sha,
            bytes=after.st_size,
            source_modified_at_utc=datetime.fromtimestamp(
                after.st_mtime, tz=UTC
            ).isoformat(),
            captured_at_utc=clock().astimezone(UTC).isoformat(),
        )
    finally:
        if temporary.exists():
            temporary.unlink()


def _schema_payload(frame: pd.DataFrame) -> list[dict[str, str]]:
    return [
        {"name": str(column), "dtype": str(dtype)}
        for column, dtype in frame.dtypes.items()
    ]


def _required_source_columns(logical_id: str) -> tuple[str, ...]:
    if logical_id == "outcome_events":
        return ("open_time_utc", "close_time_utc")
    if logical_id == "market_features_60d":
        return ("symbol", "tf", "ts", "open", "high", "low", "close", "volume")
    raise FreezeV3Error(f"unknown_source_logical_id:{logical_id}")


def _source_descriptor(snapshot: SourceSnapshot, *, epoch_dir: Path) -> dict[str, Any]:
    frame = pd.read_parquet(snapshot.frozen_path)
    required = _required_source_columns(snapshot.logical_id)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise FreezeV3Error(
            f"source_required_columns_missing:{snapshot.logical_id}:{','.join(missing)}"
        )

    if snapshot.logical_id == "outcome_events":
        minimum = pd.to_datetime(frame["open_time_utc"], utc=True, errors="coerce").min()
        maximum = pd.to_datetime(frame["close_time_utc"], utc=True, errors="coerce").max()
        timestamp_contract = "open_time_utc..close_time_utc"
    else:
        minimum = pd.to_datetime(frame["ts"], utc=True, errors="coerce").min()
        maximum = pd.to_datetime(frame["ts"], utc=True, errors="coerce").max()
        timestamp_contract = "candle_open_ts"
    if pd.isna(minimum) or pd.isna(maximum):
        raise FreezeV3Error(f"source_timestamp_range_missing:{snapshot.logical_id}")

    schema = _schema_payload(frame)
    content_contract = {
        "logical_id": snapshot.logical_id,
        "sha256": snapshot.sha256,
        "bytes": snapshot.bytes,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "schema_sha256": _sha256_json(schema),
        "min_timestamp_utc": pd.Timestamp(minimum).isoformat(),
        "max_timestamp_utc": pd.Timestamp(maximum).isoformat(),
        "timestamp_contract": timestamp_contract,
    }
    return {
        **content_contract,
        "content_contract_sha256": _sha256_json(content_contract),
        "original_path": str(snapshot.source_path),
        "frozen_relative_path": snapshot.frozen_path.relative_to(epoch_dir).as_posix(),
        "schema": schema,
        "source_modified_at_utc": snapshot.source_modified_at_utc,
        "captured_at_utc": snapshot.captured_at_utc,
        "immutable": True,
        "content_addressed": True,
    }


def _run_git(project_root: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FreezeV3Error(f"git_lineage_command_failed:{args[0] if args else 'unknown'}") from exc
    return completed.stdout


def _lineage_lockfiles(project_root: Path) -> list[dict[str, Any]]:
    lockfiles: list[dict[str, Any]] = []
    for relative in LOCKFILE_PATHS:
        path = (project_root / relative).resolve()
        _require_within(path, project_root, reason="lockfile_path_outside_project")
        if not path.is_file() or path.is_symlink():
            raise FreezeV3Error(f"required_lineage_file_missing:{relative}")
        lockfiles.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return lockfiles


def _git_lineage_contract(project_root: Path) -> dict[str, Any]:
    status = str(_run_git(project_root, "status", "--porcelain=v1", "--untracked-files=all"))
    if status.strip():
        raise FreezeV3Error("git_worktree_must_be_clean_before_freeze")
    commit_sha = str(_run_git(project_root, "rev-parse", "HEAD")).strip()
    tree_sha = str(_run_git(project_root, "rev-parse", "HEAD^{tree}")).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise FreezeV3Error("git_commit_sha_invalid")
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree_sha):
        raise FreezeV3Error("git_tree_sha_invalid")
    lockfiles = _lineage_lockfiles(project_root)
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "git_commit_sha": commit_sha,
        "git_tree_sha": tree_sha,
        "lockfiles": lockfiles,
        "lockfiles_sha256": _sha256_json(lockfiles),
        "lineage_prepared_externally": False,
    }


def _archive_entry_bytes(archive: Path, relative: str) -> bytes:
    key = relative.replace("\\", "/")
    try:
        with zipfile.ZipFile(archive) as handle:
            return handle.read(key)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise FreezeV3Error(f"source_archive_required_path_missing:{relative}") from exc


def _prepared_lineage_contract(
    project_root: Path,
    *,
    source_archive: Path,
    git_commit_sha: str,
    git_tree_sha: str,
) -> dict[str, Any]:
    if not source_archive.is_absolute():
        raise FreezeV3Error("prepared_source_archive_must_be_absolute")
    if not source_archive.is_file() or source_archive.is_symlink():
        raise FreezeV3Error("prepared_source_archive_missing_or_unsafe")
    if not re.fullmatch(r"[0-9a-f]{40,64}", git_commit_sha):
        raise FreezeV3Error("git_commit_sha_invalid")
    if not re.fullmatch(r"[0-9a-f]{40,64}", git_tree_sha):
        raise FreezeV3Error("git_tree_sha_invalid")
    for relative in CRITICAL_LINEAGE_PATHS:
        current = (project_root / relative).resolve()
        _require_within(current, project_root, reason="critical_lineage_path_outside_project")
        if not current.is_file() or current.is_symlink():
            raise FreezeV3Error(f"critical_lineage_file_missing:{relative}")
        if current.read_bytes() != _archive_entry_bytes(source_archive, relative):
            raise FreezeV3Error(f"prepared_archive_worktree_mismatch:{relative}")
    lockfiles = _lineage_lockfiles(project_root)
    for item in lockfiles:
        relative = str(item["path"])
        if _sha256_bytes(_archive_entry_bytes(source_archive, relative)) != item["sha256"]:
            raise FreezeV3Error(f"prepared_archive_lockfile_mismatch:{relative}")
    if not _zip_contains_safe_path(source_archive, REBUILD_WORKER_PATH):
        raise FreezeV3Error("frozen_rebuild_worker_missing_from_source_archive")
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "git_commit_sha": git_commit_sha,
        "git_tree_sha": git_tree_sha,
        "lockfiles": lockfiles,
        "lockfiles_sha256": _sha256_json(lockfiles),
        "lineage_prepared_externally": True,
        "prepared_source_archive_path": str(source_archive),
        "prepared_source_archive_sha256": _sha256_file(source_archive),
    }

def _write_source_archive(
    project_root: Path,
    epoch_dir: Path,
    lineage: Mapping[str, Any],
    *,
    prepared_source_archive: Path | None,
) -> dict[str, Any]:
    archive = epoch_dir / SOURCE_ARCHIVE_NAME
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise FreezeV3Error("implementation_source_archive_already_exists")
    if prepared_source_archive is not None:
        content = prepared_source_archive.read_bytes()
        _write_new_bytes(archive, content)
        expected = str(lineage.get("prepared_source_archive_sha256") or "")
        if expected and _sha256_file(archive) != expected:
            raise FreezeV3Error("prepared_source_archive_copy_hash_mismatch")
    else:
        commit_sha = str(lineage["git_commit_sha"])
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_root),
                    "archive",
                    "--format=zip",
                    f"--output={archive}",
                    commit_sha,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise FreezeV3Error("git_source_archive_creation_failed") from exc
    if not archive.is_file() or archive.stat().st_size <= 0:
        raise FreezeV3Error("git_source_archive_missing_or_empty")
    if not _zip_contains_safe_path(archive, REBUILD_WORKER_PATH):
        raise FreezeV3Error("frozen_rebuild_worker_missing_from_source_archive")
    payload = {
        **{
            key: value
            for key, value in dict(lineage).items()
            if key not in {"prepared_source_archive_path", "prepared_source_archive_sha256"}
        },
        "source_archive_relative_path": SOURCE_ARCHIVE_NAME,
        "source_archive_bytes": archive.stat().st_size,
        "source_archive_sha256": _sha256_file(archive),
    }
    payload["lineage_sha256"] = _sha256_json(payload)
    manifest_sha = _write_new_json(epoch_dir / LINEAGE_NAME, payload)
    return {**payload, "lineage_manifest_sha256": manifest_sha}

def _zip_contains_safe_path(archive: Path, expected: str) -> bool:
    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    return expected.replace("\\", "/") in names


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as handle:
        for info in handle.infolist():
            member = (destination / info.filename).resolve()
            try:
                member.relative_to(root)
            except ValueError as exc:
                raise FreezeV3Error("implementation_archive_path_traversal") from exc
        handle.extractall(destination)


def _verify_lineage_artifacts(epoch_dir: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    lineage = _read_json_object(epoch_dir / LINEAGE_NAME)
    expected_archive_sha = str(lineage.get("source_archive_sha256") or "")
    archive = (epoch_dir / str(lineage.get("source_archive_relative_path") or "")).resolve()
    _require_within(archive, epoch_dir, reason="implementation_archive_path_escape")
    if not archive.is_file() or archive.is_symlink():
        raise FreezeV3Error("implementation_source_archive_missing_or_unsafe")
    if SHA256_RE.fullmatch(expected_archive_sha) is None:
        raise FreezeV3Error("implementation_source_archive_hash_invalid")
    if _sha256_file(archive) != expected_archive_sha:
        raise FreezeV3Error("implementation_source_archive_hash_mismatch")
    if archive.stat().st_size != int(lineage.get("source_archive_bytes", -1)):
        raise FreezeV3Error("implementation_source_archive_size_mismatch")
    normalized = {key: value for key, value in lineage.items() if key != "lineage_sha256"}
    if lineage.get("lineage_sha256") != _sha256_json(normalized):
        raise FreezeV3Error("implementation_lineage_hash_mismatch")
    if contract.get("implementation_lineage_sha256") != lineage.get("lineage_sha256"):
        raise FreezeV3Error("freeze_implementation_lineage_hash_mismatch")
    return lineage


def _deterministic_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    materialized.sort(
        key=lambda row: (
            row["__close_time"],
            row["__open_time"],
            _required_trade_id(row),
        )
    )
    identities = [_required_trade_id(row) for row in materialized]
    if len(identities) != len(set(identities)):
        raise FreezeV3Error("duplicate_trade_id_detected")
    return materialized


def _derive_initial_cohorts(
    *,
    outcome_snapshot: Path,
    market_snapshot: Path,
    training_cutoff_utc: datetime,
    stress_bps: float,
) -> tuple[FrozenPrepared, dict[str, Any]]:
    rows = base._read_rows(outcome_snapshot)
    market = base._market_frame(None, market_snapshot)
    normalized, invalid_time_count = base._normalize_outcomes(rows)
    if invalid_time_count:
        raise FreezeV3Error("invalid_trade_time_detected")
    normalized = _deterministic_rows(normalized)
    rematerialized = base._rematerialize_market_features(market)
    aligned, alignment = base._align_point_in_time_market_features(normalized, rematerialized)
    aligned = _deterministic_rows(aligned)
    if alignment["coverage"] < base.MIN_PIT_ALIGNMENT_COVERAGE:
        raise FreezeV3Error("point_in_time_market_feature_coverage_not_met")
    prior = [row for row in aligned if row["__open_time"] <= training_cutoff_utc]
    if not prior:
        raise FreezeV3Error("pre_freeze_rows_missing")
    sentinel = dict(prior[-1])
    sentinel["__open_time"] = training_cutoff_utc
    sentinel["__close_time"] = training_cutoff_utc + timedelta(seconds=1)
    selected, blockers = v2._prepare_fold(
        prior_rows=prior,
        test_rows=[sentinel],
        stress_bps=stress_bps,
    )
    if selected is None:
        reason = blockers[0] if blockers else "cohort_selection_failed"
        raise FreezeV3Error(f"cohort_selection_failed:{reason}")
    medians = prospective_feature_medians(selected)
    prepared = _build_explicit_prepared(
        fit_rows=selected.fit_rows,
        calibration_rows=selected.calibration_rows,
        feature_columns=selected.feature_columns,
        feature_medians=medians,
        training_cutoff_utc=training_cutoff_utc,
        stress_bps=stress_bps,
    )
    if not np.array_equal(prepared.train_x.to_numpy(), selected.train_x.to_numpy()):
        raise FreezeV3Error("explicit_fit_matrix_differs_from_v2_builder")
    if not np.array_equal(
        prepared.calibration_x.to_numpy(), selected.calibration_x.to_numpy()
    ):
        raise FreezeV3Error("explicit_calibration_matrix_differs_from_v2_builder")
    if not np.array_equal(prepared.train_y.to_numpy(), selected.train_y.to_numpy()):
        raise FreezeV3Error("explicit_target_differs_from_v2_builder")
    return prepared, dict(alignment)


def prospective_feature_medians(prepared: base.PreparedFold) -> dict[str, float]:
    """Reuse the V2 fit-only median definition without runtime observer coupling."""

    feature_rows = [base._model_feature_row(row) for row in prepared.fit_rows]
    result: dict[str, float] = {}
    for column in prepared.feature_columns:
        values = [base._numeric(row.get(column)) for row in feature_rows]
        finite = [float(value) for value in values if value is not None]
        if not finite or not all(math.isfinite(value) for value in finite):
            raise FreezeV3Error(f"frozen_feature_median_missing:{column}")
        result[column] = float(np.median(np.asarray(finite, dtype=float)))
    return result


def _build_explicit_prepared(
    *,
    fit_rows: Sequence[Mapping[str, Any]],
    calibration_rows: Sequence[Mapping[str, Any]],
    feature_columns: Sequence[str],
    feature_medians: Mapping[str, float],
    training_cutoff_utc: datetime,
    stress_bps: float,
) -> FrozenPrepared:
    fit = tuple(dict(row) for row in fit_rows)
    calibration = tuple(dict(row) for row in calibration_rows)
    fit_ids = [_required_trade_id(row) for row in fit]
    calibration_ids = [_required_trade_id(row) for row in calibration]
    if len(fit_ids) != len(set(fit_ids)) or len(calibration_ids) != len(set(calibration_ids)):
        raise FreezeV3Error("duplicate_explicit_cohort_identity")
    if set(fit_ids).intersection(calibration_ids):
        raise FreezeV3Error("fit_calibration_cohort_overlap")
    if not fit or not calibration:
        raise FreezeV3Error("explicit_cohort_empty")

    embargo = timedelta(seconds=base.OUTCOME_AVAILABILITY_EMBARGO_SECONDS)
    calibration_open_min = min(row["__open_time"] for row in calibration)
    if max(row["__close_time"] for row in fit) > calibration_open_min - embargo:
        raise FreezeV3Error("fit_calibration_temporal_leakage")
    if max(row["__close_time"] for row in calibration) > training_cutoff_utc - embargo:
        raise FreezeV3Error("calibration_freeze_temporal_leakage")

    columns = tuple(str(column) for column in feature_columns)
    if len(columns) != len(set(columns)) or len(columns) < base.MIN_FEATURE_COUNT:
        raise FreezeV3Error("invalid_frozen_feature_columns")
    medians = {column: float(feature_medians[column]) for column in columns}
    if not all(math.isfinite(value) for value in medians.values()):
        raise FreezeV3Error("non_finite_frozen_median")

    def matrix(rows_: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        feature_rows = [base._model_feature_row(row) for row in rows_]
        data: dict[str, list[float]] = {}
        for column in columns:
            values: list[float] = []
            for row in feature_rows:
                raw = base._numeric(row.get(column))
                values.append(
                    medians[column]
                    if raw is None or not math.isfinite(raw)
                    else float(raw)
                )
            data[column] = values
        frame = pd.DataFrame(data, columns=columns, dtype="float64")
        if not np.isfinite(frame.to_numpy()).all():
            raise FreezeV3Error("non_finite_frozen_matrix")
        return frame

    train_x = matrix(fit)
    calibration_x = matrix(calibration)
    target = np.asarray([base._stressed_pnl(row, stress_bps) for row in fit], dtype="float64")
    if len(target) >= 20:
        lower = float(np.quantile(target, base.TARGET_CLIP_LOWER_QUANTILE))
        upper = float(np.quantile(target, base.TARGET_CLIP_UPPER_QUANTILE))
        target = np.clip(target, lower, upper)
    if not np.isfinite(target).all():
        raise FreezeV3Error("non_finite_frozen_target")
    return FrozenPrepared(
        feature_columns=columns,
        train_x=train_x,
        train_y=pd.Series(target, name="label", dtype="float64"),
        calibration_x=calibration_x,
        fit_rows=fit,
        calibration_rows=calibration,
        feature_medians=medians,
        training_cutoff_utc=training_cutoff_utc,
    )


def _ordered_ids_payload(ids: Sequence[str], *, partition: str) -> tuple[dict[str, Any], str]:
    ordered = [str(value) for value in ids]
    ordered_hash = _sha256_json(ordered)
    return (
        {
            "schema_version": "qlib_v3_explicit_cohort_ids_v1",
            "partition": partition,
            "deterministic_order": "close_time_utc,open_time_utc,trade_id",
            "count": len(ordered),
            "ordered_trade_ids_sha256": ordered_hash,
            "trade_ids": ordered,
        },
        ordered_hash,
    )


def _semantic_row(
    row: Mapping[str, Any],
    *,
    partition: str,
    position: int,
    feature_columns: Sequence[str],
    stress_bps: float,
    training_target: float | None,
) -> dict[str, Any]:
    features = base._model_feature_row(row)
    return {
        "partition": partition,
        "partition_position": position,
        "trade_id": _required_trade_id(row),
        "symbol": str(row.get("__symbol") or ""),
        "side": str(row.get("side") or "").strip().lower(),
        "open_time_utc": _time_iso(row["__open_time"], field="open_time_utc"),
        "close_time_utc": _time_iso(row["__close_time"], field="close_time_utc"),
        "net_pnl_float64": _float_hex(row["net_pnl"], field="net_pnl"),
        "notional_float64": _optional_float_hex(base._notional(row), field="notional"),
        "raw_target_float64": _float_hex(base._stressed_pnl(row, stress_bps), field="raw_target"),
        "training_target_float64": (
            None
            if training_target is None
            else _float_hex(training_target, field="training_target")
        ),
        "features": {
            column: _optional_float_hex(features.get(column), field=f"feature:{column}")
            for column in feature_columns
        },
    }


def _dataset_payload(prepared: FrozenPrepared, *, stress_bps: float) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(prepared.fit_rows):
        rows.append(
            _semantic_row(
                row,
                partition="fit",
                position=index,
                feature_columns=prepared.feature_columns,
                stress_bps=stress_bps,
                training_target=float(prepared.train_y.iloc[index]),
            )
        )
    for index, row in enumerate(prepared.calibration_rows):
        rows.append(
            _semantic_row(
                row,
                partition="calibration",
                position=index,
                feature_columns=prepared.feature_columns,
                stress_bps=stress_bps,
                training_target=None,
            )
        )
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "deterministic_order": "fit_then_calibration_with_explicit_cohort_order",
        "numeric_representation": "python_float_hex_ieee754_binary64",
        "null_representation": None,
        "target_contract": v2.TARGET_NAME,
        "split_membership_embedded": True,
        "row_count": len(rows),
        "feature_columns": list(prepared.feature_columns),
        "rows": rows,
    }


def _matrix_payload(prepared: FrozenPrepared) -> dict[str, Any]:
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "numeric_representation": "python_float_hex_ieee754_binary64",
        "feature_columns": list(prepared.feature_columns),
        "feature_dtypes": ["float64" for _ in prepared.feature_columns],
        "fit_trade_ids": [_required_trade_id(row) for row in prepared.fit_rows],
        "calibration_trade_ids": [_required_trade_id(row) for row in prepared.calibration_rows],
        "fit_matrix": [
            [_float_hex(value, field="fit_matrix") for value in row]
            for row in prepared.train_x.to_numpy(dtype="float64")
        ],
        "fit_target": [
            _float_hex(value, field="fit_target")
            for value in prepared.train_y.to_numpy(dtype="float64")
        ],
        "calibration_matrix": [
            [_float_hex(value, field="calibration_matrix") for value in row]
            for row in prepared.calibration_x.to_numpy(dtype="float64")
        ],
    }


def _imputation_payload(prepared: FrozenPrepared) -> dict[str, Any]:
    ordered = [
        {
            "feature": column,
            "dtype": "float64",
            "median_float64": _float_hex(
                prepared.feature_medians[column], field=f"median:{column}"
            ),
        }
        for column in prepared.feature_columns
    ]
    return {
        "schema_version": "qlib_v3_fit_only_median_imputation_v1",
        "fit_only": True,
        "feature_order": list(prepared.feature_columns),
        "ordered_medians": ordered,
        "median_vector_sha256": _sha256_json(ordered),
    }


def _environment_contract(lineage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    package_names = (
        "pyqlib",
        "lightgbm",
        "pandas",
        "numpy",
        "pyarrow",
        "mlflow",
        "joblib",
    )
    versions: dict[str, str] = {}
    for name in package_names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise FreezeV3Error(f"required_training_package_missing:{name}") from exc

    deterministic = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "byteorder": sys.byteorder,
        "packages": versions,
        "training_contract": base.native_qlib_lgb_training_contract(),
        "target": v2.TARGET_NAME,
        "eligibility_policy": v2.ELIGIBILITY_POLICY,
        "score_quantiles": list(v2.SCORE_QUANTILES),
        "pit_contract": {
            "timeframe": base.TIMEFRAME,
            "timeframe_seconds": base.TIMEFRAME_SECONDS,
            "feature_builder": "smartcrypto.data.feature_builder.build_market_feature_frame",
            "available_at": "candle_ts_plus_5_minutes_lte_trade_open",
            "same_candle_lookahead_allowed": False,
            "forward_fill_across_gaps": False,
            "outcome_availability_embargo_seconds": base.OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
        },
        "lockfiles_sha256": None if lineage is None else lineage.get("lockfiles_sha256"),
        "git_tree_sha": None if lineage is None else lineage.get("git_tree_sha"),
    }
    informational = {
        "platform": platform.platform(),
        "container_image_digest": os.environ.get("SMART_FUTUROS_QLIB_IMAGE_DIGEST"),
    }
    return {
        "schema_version": "qlib_v3_training_environment_v2",
        "deterministic": deterministic,
        "deterministic_sha256": _sha256_json(deterministic),
        "informational": informational,
    }


def _canonicalize_model_dump(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_model_dump(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in {"version"}
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_model_dump(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return {"__float64__": float(value).hex()}
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _model_semantic_fingerprint(model_text: str) -> str:
    try:
        lightgbm = importlib.import_module("lightgbm")
        booster_class = getattr(lightgbm, "Booster")
        booster = booster_class(model_str=model_text)
        dumped = booster.dump_model()
    except Exception as exc:
        raise FreezeV3Error(
            f"model_semantic_dump_failed:{type(exc).__name__}"
        ) from exc
    return _sha256_json(_canonicalize_model_dump(dumped))


def _treatment_semantic_fingerprint(
    *,
    model_semantic_fingerprint: str,
    threshold: float,
    feature_columns: Sequence[str],
    selected_quantile: Any,
) -> str:
    return _sha256_json(
        {
            "model_semantic_fingerprint": model_semantic_fingerprint,
            "feature_columns_sha256": _sha256_json(list(feature_columns)),
            "threshold_float64": threshold.hex(),
            "selected_quantile": selected_quantile,
            "target": v2.TARGET_NAME,
            "eligibility_policy": v2.ELIGIBILITY_POLICY,
        }
    )


def _train_epoch(
    prepared: FrozenPrepared,
    *,
    trainer: FrozenTrainer,
    trainer_metadata: Mapping[str, Any],
    stress_bps: float,
) -> TrainedEpoch:
    try:
        result = trainer(
            prepared.train_x,
            prepared.train_y,
            prepared.calibration_x,
            prepared.calibration_x,
            fold_id="qlib_v3_frozen_train",
        )
    except Exception as exc:
        raise FreezeV3Error(f"native_qlib_training_failed:{type(exc).__name__}") from exc

    scores = base._finite_scores(
        result.calibration_scores,
        expected=len(prepared.calibration_rows),
    )
    repeated = base._finite_scores(
        result.test_scores,
        expected=len(prepared.calibration_rows),
    )
    if not np.array_equal(scores, repeated):
        raise FreezeV3Error("same_matrix_score_reproduction_failed")
    calibration = v2._select_calibration_threshold(
        rows=prepared.calibration_rows,
        scores=scores,
        stress_bps=stress_bps,
    )
    if calibration.get("status") != "ok":
        reason = str(calibration.get("reason") or "unknown")
        raise FreezeV3Error(f"v3_calibration_blocked:{reason}")

    model_bytes = result.model_text.rstrip("\n").encode("utf-8") + b"\n"
    model_sha = _sha256_bytes(model_bytes)
    model_semantic = _model_semantic_fingerprint(model_bytes.decode("utf-8"))
    feature_columns_hash = _sha256_json(list(prepared.feature_columns))
    threshold = float(calibration["threshold"])
    treatment_semantic = _treatment_semantic_fingerprint(
        model_semantic_fingerprint=model_semantic,
        threshold=threshold,
        feature_columns=prepared.feature_columns,
        selected_quantile=calibration["selected_quantile"],
    )
    model_metadata = {
        **dict(trainer_metadata),
        "best_iteration": int(result.best_iteration),
        "feature_columns_sha256": feature_columns_hash,
        "serialized_model_sha256": model_sha,
        "serialized_model_format": "lightgbm_booster_text",
        "serialized_model_loader": "lightgbm.Booster(model_file=path)",
        "model_semantic_fingerprint": model_semantic,
        "treatment_semantic_fingerprint": treatment_semantic,
    }
    score_values = [_float_hex(value, field="calibration_score") for value in scores]
    calibration_payload = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "cohort": "explicit_calibration_trade_ids",
        "score_count": len(score_values),
        "scores_float64": score_values,
        "score_vector_sha256": _sha256_json(score_values),
        "selected_quantile": calibration["selected_quantile"],
        "threshold_float64": threshold.hex(),
        "threshold_decimal": repr(threshold),
        "selection_reason": calibration["reason"],
        "calibration_economics": calibration,
    }
    calibration_sha = _sha256_json(calibration_payload)
    policy_payload = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "source_policy_schema": v2.SCHEMA_VERSION,
        "target": v2.TARGET_NAME,
        "eligibility_policy": v2.ELIGIBILITY_POLICY,
        "selection_side": "long",
        "short_rows_used_for_training": True,
        "short_rows_eligible_for_selection": False,
        "score_quantiles": list(v2.SCORE_QUANTILES),
        "selected_quantile": calibration["selected_quantile"],
        "threshold_float64": threshold.hex(),
        "threshold_decimal": repr(threshold),
        "threshold_derivation_source": "frozen_calibration_scores_past_only",
        "additional_execution_stress_bps": stress_bps,
        "calibration_sha256": calibration_sha,
        "model_semantic_fingerprint": model_semantic,
        "treatment_semantic_fingerprint": treatment_semantic,
        "promotion_allowed": False,
        "operational_authority": False,
    }
    return TrainedEpoch(
        model_text=model_bytes.decode("utf-8"),
        model_sha256=model_sha,
        model_semantic_fingerprint=model_semantic,
        treatment_semantic_fingerprint=treatment_semantic,
        model_metadata=model_metadata,
        calibration_scores=scores,
        calibration_payload=calibration_payload,
        calibration_sha256=calibration_sha,
        threshold=threshold,
        policy_payload=policy_payload,
        policy_sha256=_sha256_json(policy_payload),
    )


def _artifact_entry(epoch_dir: Path, relative: str) -> dict[str, Any]:
    path = epoch_dir / relative
    return {
        "relative_path": relative,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _write_training_artifacts(
    epoch_dir: Path,
    *,
    prepared: FrozenPrepared,
    trained: TrainedEpoch,
    stress_bps: float,
) -> dict[str, Any]:
    fit_ids = [_required_trade_id(row) for row in prepared.fit_rows]
    calibration_ids = [_required_trade_id(row) for row in prepared.calibration_rows]
    fit_payload, fit_ids_sha = _ordered_ids_payload(fit_ids, partition="fit")
    calibration_ids_payload, calibration_ids_sha = _ordered_ids_payload(
        calibration_ids, partition="calibration"
    )
    dataset_payload = _dataset_payload(prepared, stress_bps=stress_bps)
    matrix_payload = _matrix_payload(prepared)
    imputation_payload = _imputation_payload(prepared)

    fit_file_sha = _write_new_json(epoch_dir / FIT_IDS_NAME, fit_payload)
    calibration_file_sha = _write_new_json(
        epoch_dir / CALIBRATION_IDS_NAME, calibration_ids_payload
    )
    dataset_sha = _write_new_json(epoch_dir / DATASET_NAME, dataset_payload)
    matrix_sha = _write_new_json(epoch_dir / MATRIX_NAME, matrix_payload)
    imputation_sha = _write_new_json(epoch_dir / IMPUTATION_NAME, imputation_payload)
    _write_new_bytes(epoch_dir / MODEL_ARTIFACT_NAME, trained.model_text.encode("utf-8"))
    model_metadata_sha = _write_new_json(epoch_dir / MODEL_METADATA_NAME, trained.model_metadata)
    calibration_sha = _write_new_json(epoch_dir / CALIBRATION_NAME, trained.calibration_payload)
    policy_sha = _write_new_json(epoch_dir / POLICY_NAME, trained.policy_payload)
    if trained.model_sha256 != _sha256_file(epoch_dir / MODEL_ARTIFACT_NAME):
        raise FreezeV3Error("model_artifact_write_hash_mismatch")
    if trained.calibration_sha256 != calibration_sha:
        raise FreezeV3Error("calibration_artifact_write_hash_mismatch")
    if trained.policy_sha256 != policy_sha:
        raise FreezeV3Error("policy_artifact_write_hash_mismatch")

    return {
        "fit_trade_count": len(fit_ids),
        "fit_trade_ids_sha256": fit_ids_sha,
        "fit_trade_ids_file_sha256": fit_file_sha,
        "calibration_trade_count": len(calibration_ids),
        "calibration_trade_ids_sha256": calibration_ids_sha,
        "calibration_trade_ids_file_sha256": calibration_file_sha,
        "dataset_row_count": len(fit_ids) + len(calibration_ids),
        "dataset_fingerprint": dataset_sha,
        "frozen_matrix_sha256": matrix_sha,
        "imputation_artifact_sha256": imputation_sha,
        "median_vector_sha256": imputation_payload["median_vector_sha256"],
        "feature_count": len(prepared.feature_columns),
        "feature_columns": list(prepared.feature_columns),
        "feature_columns_sha256": _sha256_json(list(prepared.feature_columns)),
        "model_artifact_sha256": trained.model_sha256,
        "model_metadata_sha256": model_metadata_sha,
        "model_semantic_fingerprint": trained.model_semantic_fingerprint,
        "treatment_semantic_fingerprint": trained.treatment_semantic_fingerprint,
        "calibration_sha256": calibration_sha,
        "calibration_score_vector_sha256": trained.calibration_payload["score_vector_sha256"],
        "threshold_float64": trained.threshold.hex(),
        "threshold_decimal": repr(trained.threshold),
        "selected_quantile": trained.calibration_payload["selected_quantile"],
        "policy_sha256": policy_sha,
        "artifacts": {
            name: _artifact_entry(epoch_dir, name)
            for name in (
                FIT_IDS_NAME,
                CALIBRATION_IDS_NAME,
                DATASET_NAME,
                MATRIX_NAME,
                IMPUTATION_NAME,
                MODEL_ARTIFACT_NAME,
                MODEL_METADATA_NAME,
                CALIBRATION_NAME,
                POLICY_NAME,
            )
        },
    }


def _read_cohort_ids(path: Path, *, expected_partition: str) -> list[str]:
    payload = _read_json_object(path)
    if payload.get("schema_version") != "qlib_v3_explicit_cohort_ids_v1":
        raise FreezeV3Error(f"cohort_schema_mismatch:{expected_partition}")
    if payload.get("partition") != expected_partition:
        raise FreezeV3Error(f"cohort_partition_mismatch:{expected_partition}")
    raw_ids = payload.get("trade_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(value, str) and value for value in raw_ids
    ):
        raise FreezeV3Error(f"cohort_trade_ids_invalid:{expected_partition}")
    ids = [str(value) for value in raw_ids]
    if len(ids) != len(set(ids)):
        raise FreezeV3Error(f"cohort_trade_ids_duplicate:{expected_partition}")
    if payload.get("count") != len(ids):
        raise FreezeV3Error(f"cohort_count_mismatch:{expected_partition}")
    if payload.get("ordered_trade_ids_sha256") != _sha256_json(ids):
        raise FreezeV3Error(f"cohort_ordered_hash_mismatch:{expected_partition}")
    return ids


def _read_frozen_medians(path: Path, feature_columns: Sequence[str]) -> dict[str, float]:
    payload = _read_json_object(path)
    if payload.get("schema_version") != "qlib_v3_fit_only_median_imputation_v1":
        raise FreezeV3Error("imputation_schema_mismatch")
    if payload.get("feature_order") != list(feature_columns):
        raise FreezeV3Error("imputation_feature_order_mismatch")
    raw = payload.get("ordered_medians")
    if not isinstance(raw, list):
        raise FreezeV3Error("imputation_medians_missing")
    expected_vector_hash = _sha256_json(raw)
    if payload.get("median_vector_sha256") != expected_vector_hash:
        raise FreezeV3Error("imputation_median_vector_hash_mismatch")
    medians: dict[str, float] = {}
    for index, column in enumerate(feature_columns):
        if index >= len(raw) or not isinstance(raw[index], Mapping):
            raise FreezeV3Error("imputation_median_entry_missing")
        item = raw[index]
        if item.get("feature") != column or item.get("dtype") != "float64":
            raise FreezeV3Error("imputation_median_entry_mismatch")
        try:
            value = float.fromhex(str(item.get("median_float64")))
        except ValueError as exc:
            raise FreezeV3Error("imputation_median_value_invalid") from exc
        if not math.isfinite(value):
            raise FreezeV3Error("imputation_median_value_non_finite")
        medians[str(column)] = value
    if len(raw) != len(feature_columns):
        raise FreezeV3Error("imputation_median_count_mismatch")
    return medians


def _source_by_logical_id(
    epoch_dir: Path, source_manifest: Mapping[str, Any]
) -> dict[str, tuple[Path, Mapping[str, Any]]]:
    raw_sources = source_manifest.get("sources")
    if not isinstance(raw_sources, list):
        raise FreezeV3Error("source_manifest_sources_missing")
    resolved: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise FreezeV3Error("source_manifest_entry_invalid")
        logical_id = str(raw.get("logical_id") or "")
        relative = str(raw.get("frozen_relative_path") or "")
        if not logical_id or not relative:
            raise FreezeV3Error("source_manifest_identity_missing")
        path = (epoch_dir / relative).resolve()
        _require_within(path, epoch_dir, reason="frozen_source_path_escape")
        if not path.exists() or not path.is_file() or path.is_symlink():
            raise FreezeV3Error(f"frozen_source_missing_or_unsafe:{logical_id}")
        expected_sha = str(raw.get("sha256") or "").lower()
        if SHA256_RE.fullmatch(expected_sha) is None:
            raise FreezeV3Error(f"frozen_source_hash_invalid:{logical_id}")
        if _sha256_file(path) != expected_sha:
            raise FreezeV3Error(f"frozen_source_hash_mismatch:{logical_id}")
        if path.stat().st_size != int(raw.get("bytes", -1)):
            raise FreezeV3Error(f"frozen_source_size_mismatch:{logical_id}")
        resolved[logical_id] = (path, raw)
    required = {"outcome_events", "market_features_60d"}
    if set(resolved) != required:
        raise FreezeV3Error("frozen_source_set_mismatch")
    return resolved


def _source_content_contract(raw: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "logical_id",
        "sha256",
        "bytes",
        "row_count",
        "column_count",
        "schema_sha256",
        "min_timestamp_utc",
        "max_timestamp_utc",
        "timestamp_contract",
    )
    return {key: raw.get(key) for key in keys}


def _reconstruct_explicit_prepared(
    epoch_dir: Path,
    contract: Mapping[str, Any],
) -> FrozenPrepared:
    source_manifest = _read_json_object(epoch_dir / SOURCE_MANIFEST_NAME)
    sources = _source_by_logical_id(epoch_dir, source_manifest)
    raw_sources = source_manifest.get("sources")
    if not isinstance(raw_sources, list):
        raise FreezeV3Error("source_manifest_sources_missing")
    content_payload = {
        "schema_version": source_manifest.get("schema_version"),
        "sources": [
            _source_content_contract(raw)
            for raw in raw_sources
            if isinstance(raw, Mapping)
        ],
    }
    if source_manifest.get("source_snapshot_sha256") != _sha256_json(content_payload):
        raise FreezeV3Error("source_snapshot_composite_hash_mismatch")
    if contract.get("source_snapshot_sha256") != source_manifest.get("source_snapshot_sha256"):
        raise FreezeV3Error("freeze_source_snapshot_hash_mismatch")

    outcome_path = sources["outcome_events"][0]
    market_path = sources["market_features_60d"][0]
    outcome_rows = base._read_rows(outcome_path)
    normalized, invalid_time_count = base._normalize_outcomes(outcome_rows)
    if invalid_time_count:
        raise FreezeV3Error("frozen_outcome_invalid_trade_time")
    normalized = _deterministic_rows(normalized)
    market = base._market_frame(None, market_path)
    rematerialized = base._rematerialize_market_features(market)
    aligned, alignment = base._align_point_in_time_market_features(normalized, rematerialized)
    aligned = _deterministic_rows(aligned)
    if alignment["coverage"] < base.MIN_PIT_ALIGNMENT_COVERAGE:
        raise FreezeV3Error("frozen_pit_alignment_coverage_not_met")

    row_by_id = {_required_trade_id(row): row for row in aligned}
    if len(row_by_id) != len(aligned):
        raise FreezeV3Error("frozen_aligned_identity_duplicate")
    fit_ids = _read_cohort_ids(epoch_dir / FIT_IDS_NAME, expected_partition="fit")
    calibration_ids = _read_cohort_ids(
        epoch_dir / CALIBRATION_IDS_NAME,
        expected_partition="calibration",
    )
    if any(value not in row_by_id for value in fit_ids):
        raise FreezeV3Error("frozen_fit_trade_id_missing_from_snapshot")
    if any(value not in row_by_id for value in calibration_ids):
        raise FreezeV3Error("frozen_calibration_trade_id_missing_from_snapshot")
    feature_columns = contract.get("feature_columns")
    if not isinstance(feature_columns, list) or not all(
        isinstance(value, str) for value in feature_columns
    ):
        raise FreezeV3Error("freeze_feature_columns_invalid")
    medians = _read_frozen_medians(epoch_dir / IMPUTATION_NAME, feature_columns)
    cutoff = _parse_utc(contract.get("training_cutoff_utc"), field="training_cutoff_utc")
    prepared = _build_explicit_prepared(
        fit_rows=[row_by_id[value] for value in fit_ids],
        calibration_rows=[row_by_id[value] for value in calibration_ids],
        feature_columns=feature_columns,
        feature_medians=medians,
        training_cutoff_utc=cutoff,
        stress_bps=float(contract["additional_execution_stress_bps"]),
    )
    fit_features = [base._model_feature_row(row) for row in prepared.fit_rows]
    recomputed: dict[str, float] = {}
    for column in prepared.feature_columns:
        values = [base._numeric(row.get(column)) for row in fit_features]
        finite = [float(value) for value in values if value is not None]
        if not finite:
            raise FreezeV3Error(f"reconstructed_median_missing:{column}")
        recomputed[column] = float(np.median(np.asarray(finite)))
    if any(
        recomputed[column].hex() != medians[column].hex()
        for column in prepared.feature_columns
    ):
        raise FreezeV3Error("reconstructed_median_vector_mismatch")
    return prepared


def _verify_declared_artifacts(epoch_dir: Path, contract: Mapping[str, Any]) -> None:
    artifacts = contract.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise FreezeV3Error("freeze_artifact_inventory_missing")
    for relative, raw in artifacts.items():
        if not isinstance(relative, str) or not isinstance(raw, Mapping):
            raise FreezeV3Error("freeze_artifact_inventory_invalid")
        path = (epoch_dir / relative).resolve()
        _require_within(path, epoch_dir, reason="freeze_artifact_path_escape")
        if not path.is_file() or path.is_symlink():
            raise FreezeV3Error(f"freeze_artifact_missing_or_unsafe:{relative}")
        if _sha256_file(path) != raw.get("sha256"):
            raise FreezeV3Error(f"freeze_artifact_hash_mismatch:{relative}")
        if path.stat().st_size != int(raw.get("bytes", -1)):
            raise FreezeV3Error(f"freeze_artifact_size_mismatch:{relative}")


def _freeze_identity(manifest: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "freeze_v3_sha256"}
    return _sha256_json(payload)


def _load_freeze_manifest(epoch_dir: Path, manifest_name: str) -> dict[str, Any]:
    manifest = _read_json_object(epoch_dir / manifest_name)
    if manifest_name == FREEZE_MANIFEST_NAME:
        if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
            raise FreezeV3Error("freeze_schema_version_mismatch")
        expected = str(manifest.get("freeze_v3_sha256") or "")
        if SHA256_RE.fullmatch(expected) is None or _freeze_identity(manifest) != expected:
            raise FreezeV3Error("freeze_identity_mismatch")
    return manifest


def rebuild_qlib_v3_freeze_once_v1(
    *,
    project_root: str | Path,
    epoch_dir: str | Path,
    manifest_name: str = FREEZE_MANIFEST_NAME,
    trainer: FrozenTrainer | None = None,
    trainer_metadata: Mapping[str, Any] | None = None,
    environment_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one no-write rebuild against only the frozen epoch artifacts."""

    epoch = Path(epoch_dir).resolve()
    if not epoch.is_dir() or epoch.is_symlink():
        raise FreezeV3Error("frozen_epoch_missing_or_unsafe")
    manifest = _load_freeze_manifest(epoch, manifest_name)
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping):
        raise FreezeV3Error("freeze_contract_missing")
    _verify_declared_artifacts(epoch, contract)
    lineage = _verify_lineage_artifacts(epoch, contract)
    environment = dict(environment_contract or _environment_contract(lineage))
    if environment.get("deterministic_sha256") != contract.get("environment_deterministic_sha256"):
        raise FreezeV3Error("environment_deterministic_hash_mismatch")

    prepared = _reconstruct_explicit_prepared(epoch, contract)
    fit_ids = [_required_trade_id(row) for row in prepared.fit_rows]
    calibration_ids = [_required_trade_id(row) for row in prepared.calibration_rows]
    expected_values = {
        "fit_trade_count": len(fit_ids),
        "fit_trade_ids_sha256": _sha256_json(fit_ids),
        "calibration_trade_count": len(calibration_ids),
        "calibration_trade_ids_sha256": _sha256_json(calibration_ids),
        "dataset_row_count": len(fit_ids) + len(calibration_ids),
        "dataset_fingerprint": _sha256_json(
            _dataset_payload(
                prepared,
                stress_bps=float(contract["additional_execution_stress_bps"]),
            )
        ),
        "frozen_matrix_sha256": _sha256_json(_matrix_payload(prepared)),
        "median_vector_sha256": _imputation_payload(prepared)["median_vector_sha256"],
        "feature_count": len(prepared.feature_columns),
        "feature_columns_sha256": _sha256_json(list(prepared.feature_columns)),
    }
    mismatch_fields = [
        field
        for field, value in expected_values.items()
        if contract.get(field) != value
    ]

    def evaluate(active_trainer: FrozenTrainer, metadata: Mapping[str, Any]) -> dict[str, Any]:
        trained = _train_epoch(
            prepared,
            trainer=active_trainer,
            trainer_metadata=metadata,
            stress_bps=float(contract["additional_execution_stress_bps"]),
        )
        semantic_values = {
            "model_semantic_fingerprint": trained.model_semantic_fingerprint,
            "treatment_semantic_fingerprint": trained.treatment_semantic_fingerprint,
            "calibration_sha256": trained.calibration_sha256,
            "calibration_score_vector_sha256": trained.calibration_payload["score_vector_sha256"],
            "threshold_float64": trained.threshold.hex(),
            "policy_sha256": trained.policy_sha256,
        }
        semantic_mismatches = [
            field for field, value in semantic_values.items() if contract.get(field) != value
        ]
        byte_reproducible = contract.get("model_artifact_sha256") == trained.model_sha256
        all_mismatches = sorted(set(mismatch_fields + semantic_mismatches))
        return {
            "status": "ok" if not all_mismatches else "blocked",
            "reason": (
                "exact_frozen_treatment_rebuild_match"
                if not all_mismatches
                else all_mismatches[0]
            ),
            "match": not all_mismatches,
            "mismatch_fields": all_mismatches,
            **expected_values,
            **semantic_values,
            "model_artifact_sha256_rebuilt": trained.model_sha256,
            "model_artifact_byte_reproducible": byte_reproducible,
            "freeze_v3_sha256": manifest.get("freeze_v3_sha256"),
            "write_performed": False,
            **SAFETY_FLAGS,
        }

    if trainer is not None:
        return evaluate(trainer, dict(trainer_metadata or {"framework": "injected_test_double"}))
    try:
        with base._native_qlib_lgb_trainer_context() as (native_trainer, metadata):
            return evaluate(native_trainer, metadata)
    except FreezeV3Error:
        raise
    except Exception as exc:
        raise FreezeV3Error(f"native_qlib_rebuild_failed:{type(exc).__name__}") from exc


def _cross_rebuild_match(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    fields = (
        "fit_trade_count",
        "fit_trade_ids_sha256",
        "calibration_trade_count",
        "calibration_trade_ids_sha256",
        "dataset_row_count",
        "dataset_fingerprint",
        "frozen_matrix_sha256",
        "median_vector_sha256",
        "feature_count",
        "feature_columns_sha256",
        "model_semantic_fingerprint",
        "treatment_semantic_fingerprint",
        "calibration_sha256",
        "calibration_score_vector_sha256",
        "threshold_float64",
        "policy_sha256",
    )
    return bool(
        first.get("match") is True
        and second.get("match") is True
        and all(first.get(field) == second.get(field) for field in fields)
    )


def _read_worker_result(path: Path) -> dict[str, Any]:
    return _read_json_object(path)


def _run_frozen_rebuild_subprocess(
    *,
    source_archive: Path,
    epoch_dir: Path,
    manifest_name: str,
    run_label: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"futuros-v3-{run_label}-") as temp:
        temp_root = Path(temp).resolve()
        source_root = temp_root / "source"
        _safe_extract_zip(source_archive, source_root)
        worker = source_root / REBUILD_WORKER_PATH
        if not worker.is_file():
            raise FreezeV3Error("frozen_rebuild_worker_missing_after_extract")
        output = temp_root / "rebuild_result.json"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["PYTHONNOUSERSITE"] = "1"
        command = [
            sys.executable,
            str(worker),
            "--project-root",
            str(source_root),
            "--epoch-dir",
            str(epoch_dir),
            "--manifest-name",
            manifest_name,
            "--output-json",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=source_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FreezeV3Error(f"isolated_rebuild_process_failed:{run_label}") from exc
        if not output.is_file():
            raise FreezeV3Error(f"isolated_rebuild_result_missing:{run_label}")
        report = _read_worker_result(output)
        report["process_exit_code"] = completed.returncode
        report["process_isolated"] = True
        report["source_archive_execution"] = True
        return report


def _independent_rebuild_pair(
    *,
    epoch_dir: Path,
    manifest_name: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    lineage = _read_json_object(epoch_dir / LINEAGE_NAME)
    archive = (epoch_dir / str(lineage.get("source_archive_relative_path") or "")).resolve()
    _require_within(archive, epoch_dir, reason="implementation_archive_path_escape")
    first = _run_frozen_rebuild_subprocess(
        source_archive=archive,
        epoch_dir=epoch_dir,
        manifest_name=manifest_name,
        run_label="rebuild-01",
    )
    second = _run_frozen_rebuild_subprocess(
        source_archive=archive,
        epoch_dir=epoch_dir,
        manifest_name=manifest_name,
        run_label="rebuild-02",
    )
    return first, second, _cross_rebuild_match(first, second)


def _injected_rebuild_pair(
    *,
    project_root: Path,
    epoch_dir: Path,
    manifest_name: str,
    trainer: FrozenTrainer,
    trainer_metadata: Mapping[str, Any],
    environment_contract: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    first = rebuild_qlib_v3_freeze_once_v1(
        project_root=project_root,
        epoch_dir=epoch_dir,
        manifest_name=manifest_name,
        trainer=trainer,
        trainer_metadata=trainer_metadata,
        environment_contract=environment_contract,
    )
    second = rebuild_qlib_v3_freeze_once_v1(
        project_root=project_root,
        epoch_dir=epoch_dir,
        manifest_name=manifest_name,
        trainer=trainer,
        trainer_metadata=trainer_metadata,
        environment_contract=environment_contract,
    )
    return first, second, _cross_rebuild_match(first, second)


def _checkpoint_tree_valid(checkpoint: Path, expected_freeze_sha: str) -> bool:
    try:
        manifest = _load_freeze_manifest(checkpoint, FREEZE_MANIFEST_NAME)
        if manifest.get("freeze_v3_sha256") != expected_freeze_sha:
            return False
        contract = manifest.get("contract")
        if not isinstance(contract, Mapping):
            return False
        _verify_declared_artifacts(checkpoint, contract)
        _verify_lineage_artifacts(checkpoint, contract)
        _source_by_logical_id(checkpoint, _read_json_object(checkpoint / SOURCE_MANIFEST_NAME))
        return True
    except (FreezeV3Error, OSError, ValueError, TypeError, KeyError):
        return False


def _copy_checkpoint_verified(epoch_dir: Path, checkpoint_root: Path, freeze_sha: str) -> Path:
    if not checkpoint_root.is_absolute():
        raise FreezeV3Error("checkpoint_root_must_be_absolute")
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    target = checkpoint_root / f"QLIB_V3_FREEZE_{freeze_sha}"
    if target.exists():
        if not _checkpoint_tree_valid(target, freeze_sha):
            raise FreezeV3Error("existing_checkpoint_integrity_mismatch")
        return target
    temporary = checkpoint_root / f".QLIB_V3_FREEZE_{freeze_sha}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copytree(epoch_dir, temporary, copy_function=shutil.copy2)
        if not _checkpoint_tree_valid(temporary, freeze_sha):
            raise FreezeV3Error("checkpoint_copy_integrity_mismatch")
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _write_report(project_root: Path, report_path: Path, payload: Mapping[str, Any]) -> None:
    allowed_report_root = (project_root / "data/reports/qlib_v3").resolve()
    _require_within(report_path, allowed_report_root, reason="report_path_outside_qlib_v3_reports")
    policy = AtomicWritePolicy.restricted([allowed_report_root], working_directory=project_root)
    atomic_write_json(report_path, dict(payload), policy=policy, sort_keys=True, allow_nan=False)


def _write_certified_report(
    *,
    project_root: Path,
    report_path: Path,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Write derived observability without invalidating an already certified freeze."""

    success_payload = {
        **dict(report),
        "report_write_performed": True,
        "report_write_status": "ok",
    }
    try:
        _write_report(project_root, report_path, success_payload)
    except (AtomicWriteError, FreezeV3Error, OSError, TypeError, ValueError) as exc:
        return {
            **dict(report),
            "status": "warning",
            "reason": "v3_freeze_certified_report_write_failed",
            "decision": "V3_REPRODUCIBLE_EPOCH_READY_FOR_ACTIVATION",
            "report_write_performed": False,
            "report_write_status": "warning",
            "report_write_error_class": type(exc).__name__,
        }
    return success_payload


def _materialize_with_trainer(
    *,
    project_root: Path,
    outcome_source: Path,
    market_source: Path,
    artifact_root: Path,
    report_path: Path,
    checkpoint_root: Path,
    stress_bps: float,
    trainer: FrozenTrainer,
    trainer_metadata: Mapping[str, Any],
    environment_contract: Mapping[str, Any],
    lineage: Mapping[str, Any],
    prepared_source_archive: Path | None,
    clock: Clock,
    process_isolated_rebuilds: bool,
) -> dict[str, Any]:
    allowed_artifact_root = (project_root / DEFAULT_ARTIFACT_ROOT).resolve()
    _require_within(
        artifact_root,
        allowed_artifact_root,
        reason="artifact_root_outside_qlib_v3_research",
    )
    if artifact_root.is_symlink():
        raise FreezeV3Error("artifact_root_symlink_not_allowed")

    artifact_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qlib-v3-freeze-", dir=str(artifact_root))).resolve()
    final_epoch_dir: Path | None = None
    try:
        outcome_snapshot = _stable_copy_source(
            outcome_source,
            staging / "sources",
            logical_id="outcome_events",
            clock=clock,
        )
        market_snapshot = _stable_copy_source(
            market_source,
            staging / "sources",
            logical_id="market_features_60d",
            clock=clock,
        )
        descriptors = [
            _source_descriptor(outcome_snapshot, epoch_dir=staging),
            _source_descriptor(market_snapshot, epoch_dir=staging),
        ]
        source_content_payload = {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "sources": [_source_content_contract(item) for item in descriptors],
        }
        source_snapshot_sha = _sha256_json(source_content_payload)
        source_manifest = {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "sources": descriptors,
            "source_snapshot_sha256": source_snapshot_sha,
            "mutable_sources_consulted_after_snapshot": False,
        }
        source_manifest_file_sha = _write_new_json(staging / SOURCE_MANIFEST_NAME, source_manifest)

        lineage_payload = _write_source_archive(
            project_root,
            staging,
            lineage,
            prepared_source_archive=prepared_source_archive,
        )
        environment_sha = _write_new_json(staging / ENVIRONMENT_NAME, environment_contract)
        training_cutoff = clock().astimezone(UTC)
        prepared, pit_alignment = _derive_initial_cohorts(
            outcome_snapshot=outcome_snapshot.frozen_path,
            market_snapshot=market_snapshot.frozen_path,
            training_cutoff_utc=training_cutoff,
            stress_bps=stress_bps,
        )
        epoch_seed = {
            "epoch_version": EPOCH_VERSION,
            "source_snapshot_sha256": source_snapshot_sha,
            "git_tree_sha": lineage_payload["git_tree_sha"],
            "environment_deterministic_sha256": environment_contract["deterministic_sha256"],
            "training_cutoff_utc": training_cutoff.isoformat(),
            "training_contract": base.native_qlib_lgb_training_contract(),
            "target": v2.TARGET_NAME,
            "eligibility_policy": v2.ELIGIBILITY_POLICY,
        }
        epoch_id = "qlib-v3-" + _sha256_json(epoch_seed)[:24]
        final_epoch_dir = artifact_root / "epochs" / epoch_id
        if final_epoch_dir.exists():
            raise FreezeV3Error(f"immutable_epoch_already_exists:{epoch_id}")

        trained = _train_epoch(
            prepared,
            trainer=trainer,
            trainer_metadata=trainer_metadata,
            stress_bps=stress_bps,
        )
        training_contract = _write_training_artifacts(
            staging,
            prepared=prepared,
            trained=trained,
            stress_bps=stress_bps,
        )
        infrastructure_artifacts = {
            SOURCE_MANIFEST_NAME: _artifact_entry(staging, SOURCE_MANIFEST_NAME),
            LINEAGE_NAME: _artifact_entry(staging, LINEAGE_NAME),
            SOURCE_ARCHIVE_NAME: _artifact_entry(staging, SOURCE_ARCHIVE_NAME),
            ENVIRONMENT_NAME: _artifact_entry(staging, ENVIRONMENT_NAME),
        }
        contract: dict[str, Any] = {
            **training_contract,
            "training_cutoff_utc": training_cutoff.isoformat(),
            "additional_execution_stress_bps": stress_bps,
            "target": v2.TARGET_NAME,
            "eligibility_policy": v2.ELIGIBILITY_POLICY,
            "source_policy_schema": v2.SCHEMA_VERSION,
            "source_snapshot_sha256": source_snapshot_sha,
            "source_manifest_file_sha256": source_manifest_file_sha,
            "implementation_lineage_sha256": lineage_payload["lineage_sha256"],
            "git_commit_sha": lineage_payload["git_commit_sha"],
            "git_tree_sha": lineage_payload["git_tree_sha"],
            "source_archive_sha256": lineage_payload["source_archive_sha256"],
            "environment_artifact_sha256": environment_sha,
            "environment_deterministic_sha256": environment_contract["deterministic_sha256"],
            "environment": dict(environment_contract),
            "pit_alignment": pit_alignment,
            "cohort_selector": "v2_prepare_fold_once_then_explicit_ids_only_for_all_rebuilds",
            "cohorts_disjoint": True,
            "deterministic_row_order": "close_time_utc,open_time_utc,trade_id",
            "artifacts": {**infrastructure_artifacts, **training_contract["artifacts"]},
        }
        candidate_manifest: dict[str, Any] = {
            "schema_version": FREEZE_SCHEMA_VERSION,
            "manifest_kind": "rebuild_candidate",
            "epoch_id": epoch_id,
            "epoch_version": EPOCH_VERSION,
            "v2_status": V2_TERMINAL_STATUS,
            "v2_terminal_reason": V2_TERMINAL_REASON,
            "contract": contract,
            "v3_freeze_reproducible": False,
            "epoch_activation_allowed": False,
            "prospective_start_utc": None,
            **SAFETY_FLAGS,
        }
        _write_new_json(staging / FREEZE_CANDIDATE_NAME, candidate_manifest)

        if process_isolated_rebuilds:
            rebuild_1, rebuild_2, cross_match = _independent_rebuild_pair(
                epoch_dir=staging,
                manifest_name=FREEZE_CANDIDATE_NAME,
            )
        else:
            rebuild_1, rebuild_2, cross_match = _injected_rebuild_pair(
                project_root=project_root,
                epoch_dir=staging,
                manifest_name=FREEZE_CANDIDATE_NAME,
                trainer=trainer,
                trainer_metadata=trainer_metadata,
                environment_contract=environment_contract,
            )
        if not (rebuild_1.get("match") and rebuild_2.get("match") and cross_match):
            raise FreezeV3Error("pre_activation_cross_rebuild_mismatch")
        if process_isolated_rebuilds and not (
            rebuild_1.get("process_isolated") is True and rebuild_2.get("process_isolated") is True
        ):
            raise FreezeV3Error("independent_rebuild_process_isolation_not_proven")

        byte_reproducible = bool(
            rebuild_1.get("model_artifact_byte_reproducible") is True
            and rebuild_2.get("model_artifact_byte_reproducible") is True
        )
        reproduction_payload = {
            "schema_version": REPRODUCTION_SCHEMA_VERSION,
            "rebuild_1": rebuild_1,
            "rebuild_2": rebuild_2,
            "rebuild_1_match": True,
            "rebuild_2_match": True,
            "cross_rebuild_match": True,
            "process_isolated_rebuilds": process_isolated_rebuilds,
            "model_artifact_byte_reproducible": byte_reproducible,
            "model_semantic_reproducible": True,
            "treatment_semantic_reproducible": True,
        }
        reproduction_sha = _write_new_json(
            staging / REPRODUCTION_CERTIFICATE_NAME, reproduction_payload
        )
        contract["reproduction_certificate_sha256"] = reproduction_sha
        contract["model_artifact_byte_reproducible"] = byte_reproducible
        contract["model_semantic_reproducible"] = True
        contract["treatment_semantic_reproducible"] = True
        contract["artifacts"] = {
            **dict(contract["artifacts"]),
            REPRODUCTION_CERTIFICATE_NAME: _artifact_entry(staging, REPRODUCTION_CERTIFICATE_NAME),
        }

        (staging / FREEZE_CANDIDATE_NAME).unlink()
        freeze_materialized_at = clock().astimezone(UTC)
        final_manifest: dict[str, Any] = {
            "schema_version": FREEZE_SCHEMA_VERSION,
            "manifest_kind": "certified_freeze",
            "epoch_id": epoch_id,
            "epoch_version": EPOCH_VERSION,
            "created_at_utc": freeze_materialized_at.isoformat(),
            "freeze_materialized_at_utc": freeze_materialized_at.isoformat(),
            "prospective_start_utc": None,
            "activation_required": True,
            "epoch_activation_allowed": False,
            "v2_status": V2_TERMINAL_STATUS,
            "v2_terminal_reason": V2_TERMINAL_REASON,
            "contract": contract,
            "v3_freeze_reproducible": True,
            "rebuild_1_match": True,
            "rebuild_2_match": True,
            "cross_rebuild_match": True,
            "old_v2_signal_admission_allowed": False,
            "pre_boundary_event_admission_allowed": False,
            **SAFETY_FLAGS,
        }
        final_manifest["freeze_v3_sha256"] = _freeze_identity(final_manifest)
        _write_new_json(staging / FREEZE_MANIFEST_NAME, final_manifest)
        expected_freeze_sha = str(final_manifest["freeze_v3_sha256"])
        if not _checkpoint_tree_valid(staging, expected_freeze_sha):
            raise FreezeV3Error("staging_freeze_integrity_failed")

        checkpoint_path = _copy_checkpoint_verified(staging, checkpoint_root, expected_freeze_sha)
        final_epoch_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final_epoch_dir)
        if not _checkpoint_tree_valid(final_epoch_dir, expected_freeze_sha):
            raise FreezeV3Error("published_epoch_integrity_failed")

        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "reason": "v3_freeze_certified_ready_for_explicit_activation",
            "decision": "V3_REPRODUCIBLE_EPOCH_READY_FOR_ACTIVATION",
            "root_blocker_v2": V2_TERMINAL_REASON,
            "v2_status": V2_TERMINAL_STATUS,
            "v3_freeze_reproducible": True,
            "v3_epoch_id": epoch_id,
            "v3_prospective_start_utc": None,
            "activation_required": True,
            "epoch_dir": str(final_epoch_dir),
            "checkpoint_path": str(checkpoint_path),
            "source_snapshot_sha256": source_snapshot_sha,
            "freeze_v3_sha256": expected_freeze_sha,
            "rebuild_1": rebuild_1,
            "rebuild_2": rebuild_2,
            "rebuild_1_match": True,
            "rebuild_2_match": True,
            "cross_rebuild_match": True,
            "model_artifact_byte_reproducible": byte_reproducible,
            "model_semantic_reproducible": True,
            "treatment_semantic_reproducible": True,
            "v3_freeze_dod": "READY_FOR_ACTIVATION" if process_isolated_rebuilds else "TEST_ONLY",
            "write_performed": True,
            "writes_research_artifacts": True,
            "writes_external_checkpoint": True,
            **contract,
            **SAFETY_FLAGS,
        }
        return _write_certified_report(
            project_root=project_root,
            report_path=report_path,
            report=report,
        )
    except Exception as exc:
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_exc:
                exc.add_note(f"staging_cleanup_failed:{type(cleanup_exc).__name__}")
        raise


def materialize_qlib_v3_freeze_epoch_v1(
    *,
    project_root: str | Path,
    outcome_path: str | Path = DEFAULT_OUTCOME_PATH,
    market_features_path: str | Path = DEFAULT_MARKET_FEATURES_PATH,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    checkpoint_root: str | Path,
    additional_execution_stress_bps: float = base.DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    trainer: FrozenTrainer | None = None,
    trainer_metadata: Mapping[str, Any] | None = None,
    environment_contract: Mapping[str, Any] | None = None,
    prepared_source_archive: str | Path | None = None,
    prepared_git_commit_sha: str | None = None,
    prepared_git_tree_sha: str | None = None,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Materialize and certify a V3 freeze; activation remains a separate step."""

    root = Path(project_root).resolve()
    prepared_archive = (
        Path(prepared_source_archive).resolve()
        if prepared_source_archive is not None
        else None
    )
    prepared_values = (
        prepared_archive is not None,
        prepared_git_commit_sha is not None,
        prepared_git_tree_sha is not None,
    )
    if any(prepared_values) and not all(prepared_values):
        raise FreezeV3Error("prepared_lineage_requires_archive_commit_and_tree")
    if prepared_archive is not None:
        lineage = _prepared_lineage_contract(
            root,
            source_archive=prepared_archive,
            git_commit_sha=str(prepared_git_commit_sha),
            git_tree_sha=str(prepared_git_tree_sha),
        )
    else:
        lineage = _git_lineage_contract(root)
    outcome_source = _resolve(root, outcome_path)
    market_source = _resolve(root, market_features_path)
    artifacts = _resolve(root, artifact_root)
    report = _resolve(root, report_path)
    checkpoint = Path(checkpoint_root).resolve()
    stress_bps = base._validate_stress_bps(additional_execution_stress_bps)
    environment = dict(environment_contract or _environment_contract(lineage))

    if trainer is not None:
        return _materialize_with_trainer(
            project_root=root,
            outcome_source=outcome_source,
            market_source=market_source,
            artifact_root=artifacts,
            report_path=report,
            checkpoint_root=checkpoint,
            stress_bps=stress_bps,
            trainer=trainer,
            trainer_metadata=dict(trainer_metadata or {"framework": "injected_test_double"}),
            environment_contract=environment,
            lineage=lineage,
            prepared_source_archive=prepared_archive,
            clock=clock,
            process_isolated_rebuilds=False,
        )
    try:
        with base._native_qlib_lgb_trainer_context() as (native_trainer, metadata):
            return _materialize_with_trainer(
                project_root=root,
                outcome_source=outcome_source,
                market_source=market_source,
                artifact_root=artifacts,
                report_path=report,
                checkpoint_root=checkpoint,
                stress_bps=stress_bps,
                trainer=native_trainer,
                trainer_metadata=metadata,
                environment_contract=environment,
                lineage=lineage,
                prepared_source_archive=prepared_archive,
                clock=clock,
                process_isolated_rebuilds=True,
            )
    except FreezeV3Error:
        raise
    except Exception as exc:
        raise FreezeV3Error(f"native_qlib_materialization_failed:{type(exc).__name__}") from exc


def verify_qlib_v3_freeze_epoch_v1(
    *,
    project_root: str | Path,
    epoch_dir: str | Path,
    trainer: FrozenTrainer | None = None,
    trainer_metadata: Mapping[str, Any] | None = None,
    environment_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild an existing V3 freeze twice, read-only and process-isolated in production."""

    root = Path(project_root).resolve()
    epoch = Path(epoch_dir).resolve()
    manifest = _load_freeze_manifest(epoch, FREEZE_MANIFEST_NAME)
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping):
        raise FreezeV3Error("freeze_contract_missing")
    lineage = _verify_lineage_artifacts(epoch, contract)
    environment = dict(environment_contract or _environment_contract(lineage))
    if trainer is not None:
        first, second, cross = _injected_rebuild_pair(
            project_root=root,
            epoch_dir=epoch,
            manifest_name=FREEZE_MANIFEST_NAME,
            trainer=trainer,
            trainer_metadata=dict(trainer_metadata or {"framework": "injected_test_double"}),
            environment_contract=environment,
        )
        process_isolated = False
    else:
        first, second, cross = _independent_rebuild_pair(
            epoch_dir=epoch,
            manifest_name=FREEZE_MANIFEST_NAME,
        )
        process_isolated = True
    passed = bool(first.get("match") and second.get("match") and cross)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if passed else "blocked",
        "reason": (
            "v3_freeze_reproduced_twice_read_only"
            if passed
            else "v3_freeze_rebuild_mismatch"
        ),
        "v3_epoch_id": manifest.get("epoch_id"),
        "v3_prospective_start_utc": None,
        "freeze_v3_sha256": manifest.get("freeze_v3_sha256"),
        "v3_freeze_reproducible": passed,
        "rebuild_1": first,
        "rebuild_2": second,
        "rebuild_1_match": first.get("match") is True,
        "rebuild_2_match": second.get("match") is True,
        "cross_rebuild_match": cross,
        "process_isolated_rebuilds": process_isolated,
        "model_artifact_byte_reproducible": bool(
            first.get("model_artifact_byte_reproducible") is True
            and second.get("model_artifact_byte_reproducible") is True
        ),
        "model_semantic_reproducible": passed,
        "treatment_semantic_reproducible": passed,
        "v3_freeze_dod": "READY_FOR_ACTIVATION" if passed and process_isolated else "TEST_ONLY",
        "write_performed": False,
        **SAFETY_FLAGS,
    }


def _activation_identity(payload: Mapping[str, Any]) -> str:
    base_payload = {key: value for key, value in payload.items() if key != "activation_sha256"}
    return _sha256_json(base_payload)


def activate_qlib_v3_freeze_epoch_v1(
    *,
    project_root: str | Path,
    epoch_dir: str | Path,
    checkpoint_root: str | Path,
    activation_root: str | Path = DEFAULT_ACTIVATION_ROOT,
    report_path: str | Path = DEFAULT_ACTIVATION_REPORT_PATH,
    activation_delay_seconds: int = DEFAULT_ACTIVATION_DELAY_SECONDS,
    clock: Clock = _utc_now,
    trainer: FrozenTrainer | None = None,
    trainer_metadata: Mapping[str, Any] | None = None,
    environment_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a future prospective boundary only after a complete freeze verification."""

    if activation_delay_seconds < 30:
        raise FreezeV3Error("activation_delay_seconds_below_minimum_30")
    root = Path(project_root).resolve()
    epoch = Path(epoch_dir).resolve()
    freeze = _load_freeze_manifest(epoch, FREEZE_MANIFEST_NAME)
    verification = verify_qlib_v3_freeze_epoch_v1(
        project_root=root,
        epoch_dir=epoch,
        trainer=trainer,
        trainer_metadata=trainer_metadata,
        environment_contract=environment_contract,
    )
    production_mode = trainer is None
    if verification.get("status") != "ok":
        raise FreezeV3Error("activation_requires_successful_freeze_verification")
    if production_mode and verification.get("process_isolated_rebuilds") is not True:
        raise FreezeV3Error("activation_requires_process_isolated_rebuilds")

    now = clock().astimezone(UTC)
    prospective_start = now + timedelta(seconds=int(activation_delay_seconds))
    activation: dict[str, Any] = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "epoch_id": freeze.get("epoch_id"),
        "epoch_version": EPOCH_VERSION,
        "freeze_v3_sha256": freeze.get("freeze_v3_sha256"),
        "activated_at_utc": now.isoformat(),
        "prospective_start_utc": prospective_start.isoformat(),
        "activation_delay_seconds": int(activation_delay_seconds),
        "verification_rebuild_1_match": verification.get("rebuild_1_match"),
        "verification_rebuild_2_match": verification.get("rebuild_2_match"),
        "verification_cross_rebuild_match": verification.get("cross_rebuild_match"),
        "process_isolated_rebuilds": verification.get("process_isolated_rebuilds"),
        "old_v2_signal_admission_allowed": False,
        "pre_boundary_event_admission_allowed": False,
        "backfill_allowed": False,
        **SAFETY_FLAGS,
    }
    activation["activation_sha256"] = _activation_identity(activation)

    allowed_activation_root = (root / DEFAULT_ACTIVATION_ROOT).resolve()
    activation_dir = _resolve(root, activation_root)
    _require_within(
        activation_dir,
        allowed_activation_root,
        reason="activation_root_outside_qlib_v3_activations",
    )
    activation_dir.mkdir(parents=True, exist_ok=True)
    activation_path = activation_dir / f"{freeze['freeze_v3_sha256']}.json"
    if activation_path.exists():
        existing = _read_json_object(activation_path)
        if existing.get("activation_sha256") != _activation_identity(existing):
            raise FreezeV3Error("existing_activation_integrity_mismatch")
        if existing.get("freeze_v3_sha256") != freeze.get("freeze_v3_sha256"):
            raise FreezeV3Error("existing_activation_freeze_mismatch")
        if existing.get("epoch_id") != freeze.get("epoch_id"):
            raise FreezeV3Error("existing_activation_epoch_mismatch")
        activation = existing
        prospective_start = _parse_utc(
            activation.get("prospective_start_utc"), field="prospective_start_utc"
        )
    else:
        _write_new_json(activation_path, activation)

    checkpoint = Path(checkpoint_root).resolve()
    if not checkpoint.is_absolute():
        raise FreezeV3Error("checkpoint_root_must_be_absolute")
    activation_sha = str(activation["activation_sha256"])
    activation_checkpoint = checkpoint / f"QLIB_V3_ACTIVATION_{activation_sha}"
    if activation_checkpoint.exists():
        existing = _read_json_object(activation_checkpoint / "activation.json")
        if existing.get("activation_sha256") != activation_sha:
            raise FreezeV3Error("activation_checkpoint_integrity_mismatch")
    else:
        temporary = checkpoint / f".QLIB_V3_ACTIVATION_{activation_sha}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            _write_new_json(temporary / "activation.json", activation)
            os.replace(temporary, activation_checkpoint)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "v3_freeze_verified_and_future_prospective_boundary_activated",
        "decision": "V3_REPRODUCIBLE_EPOCH_READY_FOR_FORWARD_EVIDENCE",
        "root_blocker_v2": V2_TERMINAL_REASON,
        "v2_status": V2_TERMINAL_STATUS,
        "v3_freeze_reproducible": True,
        "v3_epoch_id": freeze.get("epoch_id"),
        "freeze_v3_sha256": freeze.get("freeze_v3_sha256"),
        "activation_sha256": activation_sha,
        "v3_prospective_start_utc": prospective_start.isoformat(),
        "rebuild_1_match": verification.get("rebuild_1_match"),
        "rebuild_2_match": verification.get("rebuild_2_match"),
        "cross_rebuild_match": verification.get("cross_rebuild_match"),
        "model_artifact_byte_reproducible": verification.get(
            "model_artifact_byte_reproducible"
        ),
        "model_semantic_reproducible": True,
        "treatment_semantic_reproducible": True,
        "activation_path": str(activation_path),
        "activation_checkpoint_path": str(activation_checkpoint),
        "v3_freeze_dod": "PASS" if production_mode else "TEST_ONLY",
        "backfill_performed": False,
        "write_performed": True,
        **SAFETY_FLAGS,
    }
    _write_report(root, _resolve(root, report_path), report)
    return report


def validate_v3_prospective_event(
    *,
    freeze_manifest: Mapping[str, Any],
    activation_manifest: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate epoch and temporal admission without scoring or writing."""

    blockers: list[str] = []
    epoch_id = str(freeze_manifest.get("epoch_id") or "")
    freeze_sha = str(freeze_manifest.get("freeze_v3_sha256") or "")
    if freeze_manifest.get("v3_freeze_reproducible") is not True:
        blockers.append("v3_freeze_not_reproducible")
    if activation_manifest.get("freeze_v3_sha256") != freeze_sha:
        blockers.append("activation_freeze_identity_mismatch")
    if activation_manifest.get("activation_sha256") != _activation_identity(activation_manifest):
        blockers.append("activation_identity_mismatch")
    if str(event.get("epoch_id") or "") != epoch_id:
        blockers.append("event_epoch_identity_mismatch")
    if str(event.get("epoch_version") or "").casefold() != EPOCH_VERSION:
        blockers.append("event_epoch_version_mismatch")
    try:
        event_time = _parse_utc(event.get("observed_at_utc"), field="event_observed_at_utc")
        boundary = _parse_utc(
            activation_manifest.get("prospective_start_utc"),
            field="prospective_start_utc",
        )
        if event_time < boundary:
            blockers.append("event_precedes_v3_prospective_start")
    except FreezeV3Error as exc:
        blockers.append(exc.reason)
    admitted = not blockers
    return {
        "status": "ok" if admitted else "blocked",
        "reason": "v3_event_admitted" if admitted else blockers[0],
        "event_admitted": admitted,
        "blockers": list(dict.fromkeys(blockers)),
        "backfill_performed": False,
        "old_v2_signal_reused": False,
        "write_performed": False,
        **SAFETY_FLAGS,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    """Return a stable fail-closed CLI result."""

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "root_blocker_v2": V2_TERMINAL_REASON,
        "v2_status": V2_TERMINAL_STATUS,
        "v3_freeze_reproducible": False,
        "v3_freeze_dod": "BLOCKED_WITH_REASON",
        "write_performed": False,
        **SAFETY_FLAGS,
    }
