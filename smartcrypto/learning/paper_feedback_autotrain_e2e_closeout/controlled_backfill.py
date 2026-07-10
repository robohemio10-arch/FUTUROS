"""Transactional writer for an explicitly authorized paper-feedback backfill."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autotrain_feedback_gap_backfill_dryrun.dryrun import (
    validate_simulated_event,
)


@dataclass(frozen=True)
class BackfillRequest:
    feedback_path: Path
    backup_dir: Path
    lock_path: Path
    operation_id: str
    authorization_reference: str
    candidate_events: tuple[Mapping[str, Any], ...]
    external_source_paths: tuple[Path, ...]
    source_fingerprint_hash: str


ReplaceFunction = Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None]
PostWriteValidator = Callable[[Sequence[Mapping[str, Any]]], bool]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def source_fingerprint(paths: Sequence[Path]) -> str:
    records: list[dict[str, Any]] = []
    for path in sorted((item.resolve() for item in paths), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError("symlink_source_forbidden")
        if not path.is_file():
            records.append({"path": path.as_posix(), "exists": False})
            continue
        stat = path.stat()
        records.append(
            {
                "path": path.as_posix(),
                "exists": True,
                "size": stat.st_size,
                "sha256": file_sha256(path),
            }
        )
    return canonical_sha256(records)


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], ["missing_feedback_events_source"]
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return [], ["unreadable_feedback_events_source"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"invalid_feedback_jsonl_line:{line_number}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"invalid_feedback_jsonl_object:{line_number}")
            continue
        rows.append(payload)
    return rows, errors


def event_identity(event: Mapping[str, Any]) -> str:
    for field in ("event_hash", "idempotency_key"):
        value = str(event.get(field) or "").strip().casefold()
        if value:
            return f"{field}:{value}"
    return "payload:" + canonical_sha256(event)


def execute_controlled_backfill(
    request: BackfillRequest,
    *,
    replace_function: ReplaceFunction = os.replace,
    rollback_replace_function: ReplaceFunction | None = None,
    post_write_validator: PostWriteValidator | None = None,
    source_change_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Apply one authorized batch with lock, backup, atomic replace and rollback."""

    feedback = request.feedback_path
    existing, load_errors = load_jsonl(feedback)
    candidates = [dict(event) for event in request.candidate_events]
    base = {
        "operation_id": request.operation_id,
        "authorization_reference": request.authorization_reference,
        "pre_write_feedback_count": len(existing),
        "planned_event_count": len(candidates),
        "post_write_feedback_count": len(existing),
        "applied_event_count": 0,
        "already_existing_count": 0,
        "missing_after_count": len(candidates),
        "duplicate_count": 0,
        "conflict_count": 0,
        "schema_error_count": 0,
        "backup_created": False,
        "rollback_performed": False,
        "already_applied": False,
        "write_performed": False,
        "backfill_performed": False,
        "manual_intervention_required": False,
    }
    if load_errors:
        return {**base, "status": "blocked", "reason": "invalid_feedback_jsonl", "blockers": load_errors}

    existing_identity_values = [event_identity(event) for event in existing]
    existing_duplicates = len(existing_identity_values) - len(set(existing_identity_values))
    if existing_duplicates:
        return {
            **base,
            "status": "blocked",
            "reason": "existing_feedback_duplicates_detected",
            "duplicate_count": existing_duplicates,
            "blockers": ["existing_feedback_duplicates_detected"],
        }

    candidate_errors = [error for event in candidates for error in validate_simulated_event(event)]
    identities = [event_identity(event) for event in candidates]
    duplicate_batch_count = len(identities) - len(set(identities))
    if candidate_errors or duplicate_batch_count:
        return {
            **base,
            "status": "blocked",
            "reason": "candidate_batch_validation_failed",
            "schema_error_count": len(candidate_errors),
            "duplicate_count": duplicate_batch_count,
            "blockers": sorted(set(candidate_errors + (["duplicate_candidate_event"] if duplicate_batch_count else []))),
        }

    existing_identities = {event_identity(event) for event in existing}
    already_existing = [event for event in candidates if event_identity(event) in existing_identities]
    pending = [event for event in candidates if event_identity(event) not in existing_identities]
    if candidates and not pending:
        return {
            **base,
            "status": "ok",
            "reason": "authorized_backfill_already_applied",
            "decision": "BACKFILL_ALREADY_APPLIED",
            "already_existing_count": len(already_existing),
            "missing_after_count": 0,
            "already_applied": True,
        }

    if source_fingerprint(request.external_source_paths) != request.source_fingerprint_hash:
        return {
            **base,
            "status": "blocked",
            "reason": "source_fingerprint_mismatch",
            "blockers": ["source_fingerprint_mismatch"],
        }

    request.feedback_path.parent.mkdir(parents=True, exist_ok=True)
    request.backup_dir.mkdir(parents=True, exist_ok=True)
    lock_owned = False
    preserve_lock = False
    temporary: Path | None = None
    backup = request.backup_dir / f"{feedback.name}.{request.operation_id}.bak"
    try:
        try:
            descriptor = os.open(request.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return {**base, "status": "blocked", "reason": "backfill_lock_already_exists", "blockers": ["lock_exists"]}
        lock_owned = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as lock_handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "authorization_reference": request.authorization_reference,
                    "operation_id": request.operation_id,
                },
                lock_handle,
                sort_keys=True,
            )
            lock_handle.flush()
            os.fsync(lock_handle.fileno())

        if backup.exists():
            return {**base, "status": "blocked", "reason": "backup_already_exists", "blockers": ["backup_exists"]}

        pre_bytes = feedback.read_bytes()
        pre_hash = hashlib.sha256(pre_bytes).hexdigest()
        with backup.open("xb") as backup_handle:
            backup_handle.write(pre_bytes)
            backup_handle.flush()
            os.fsync(backup_handle.fileno())
        if file_sha256(backup) != pre_hash:
            return {**base, "status": "blocked", "reason": "backup_hash_mismatch", "blockers": ["backup_hash_mismatch"]}

        final_events = [*existing, *pending]
        final_text = "".join(
            json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in final_events
        )
        temp_handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=feedback.parent,
            prefix=f".{feedback.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(temp_handle.name)
        with temp_handle:
            temp_handle.write(final_text)
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
        temp_rows, temp_errors = load_jsonl(temporary)
        if temp_errors or len(temp_rows) != len(final_events):
            return {**base, "status": "blocked", "reason": "temporary_jsonl_validation_failed", "blockers": temp_errors}

        if source_change_hook is not None:
            source_change_hook()
        if source_fingerprint(request.external_source_paths) != request.source_fingerprint_hash:
            return {**base, "status": "blocked", "reason": "source_fingerprint_changed_before_write", "blockers": ["source_changed"]}

        replace_function(temporary, feedback)
        fsync_directory(feedback.parent)
        temporary = None
        post_rows, post_errors = load_jsonl(feedback)
        post_identities = [event_identity(event) for event in post_rows]
        counts = Counter(post_identities)
        missing_after = sum(1 for identity in identities if counts[identity] == 0)
        duplicate_count = sum(max(0, counts[identity] - 1) for identity in identities)
        validator_ok = post_write_validator(post_rows) if post_write_validator is not None else True
        valid = (
            not post_errors
            and len(post_rows) == len(existing) + len(pending)
            and missing_after == 0
            and duplicate_count == 0
            and validator_ok
            and source_fingerprint(request.external_source_paths) == request.source_fingerprint_hash
        )
        if valid:
            return {
                **base,
                "status": "ok",
                "reason": "controlled_feedback_backfill_completed",
                "decision": "BACKFILL_APPLIED",
                "post_write_feedback_count": len(post_rows),
                "applied_event_count": len(pending),
                "already_existing_count": len(already_existing),
                "missing_after_count": 0,
                "backup_created": True,
                "write_performed": True,
                "backfill_performed": True,
            }

        rollback_replace = rollback_replace_function or os.replace
        try:
            rollback_temp = feedback.parent / f".{feedback.name}.{request.operation_id}.rollback.tmp"
            with backup.open("rb") as backup_handle, rollback_temp.open("xb") as rollback_handle:
                shutil.copyfileobj(backup_handle, rollback_handle)
                rollback_handle.flush()
                os.fsync(rollback_handle.fileno())
            rollback_replace(rollback_temp, feedback)
            fsync_directory(feedback.parent)
            if file_sha256(feedback) != pre_hash:
                raise OSError("restored_hash_mismatch")
        except OSError:
            preserve_lock = True
            return {
                **base,
                "status": "blocked",
                "reason": "post_write_validation_failed_rollback_failed",
                "decision": "MANUAL_INTERVENTION_REQUIRED",
                "backup_created": True,
                "manual_intervention_required": True,
            }
        return {
            **base,
            "status": "blocked",
            "reason": "post_write_validation_failed_rollback_completed",
            "decision": "BACKFILL_ROLLED_BACK",
            "backup_created": True,
            "rollback_performed": True,
        }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if lock_owned and not preserve_lock:
            request.lock_path.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    """Sync directory metadata where the platform permits opening directories."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
