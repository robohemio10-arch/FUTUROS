"""Restricted persistence for research reports and idempotent ledger events."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_append_jsonl,
    atomic_write_json,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    _InterProcessFileLock,
)

_LEDGER_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()


def resolve_report_path(root: Path, value: str | Path) -> Path:
    return _resolve_under_reports(root, value, suffix=".json")


def resolve_ledger_path(root: Path, value: str | Path) -> Path:
    return _resolve_under_reports(root, value, suffix=".jsonl")


def write_report(root: Path, path: Path, report: Mapping[str, Any]) -> None:
    canonical_root = _canonical_root(root)
    policy = AtomicWritePolicy.restricted(
        [_reports_root(canonical_root)],
        working_directory=canonical_root,
    )
    atomic_write_json(path, report, policy=policy, allow_nan=False)


def append_ledger_idempotent(
    root: Path,
    path: Path,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return 0
    canonical_root = _canonical_root(root)
    target = resolve_ledger_path(canonical_root, path)
    policy = AtomicWritePolicy.restricted(
        [_reports_root(canonical_root)],
        working_directory=canonical_root,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _ledger_thread_lock_for(target)
    with thread_lock:
        lock = _InterProcessFileLock(
            target.parent / f".{target.name}.idempotency.lock",
            timeout_seconds=policy.lock_timeout_seconds,
        )
        lock.acquire()
        try:
            existing_ids = _existing_ledger_ids(target)
            batch_ids: set[str] = set()
            new_rows: list[dict[str, Any]] = []
            for row in materialized:
                ledger_id = str(row.get("ledger_id") or "").strip()
                if not ledger_id:
                    raise ValueError("ledger_id_required")
                if ledger_id in existing_ids or ledger_id in batch_ids:
                    continue
                batch_ids.add(ledger_id)
                new_rows.append(row)
            if not new_rows:
                return 0
            atomic_append_jsonl(target, new_rows, policy=policy)
            return len(new_rows)
        finally:
            lock.release()


def _existing_ledger_ids(path: Path) -> set[str]:
    existing_ids: set[str] = set()
    if not path.exists():
        return existing_ids
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or not payload.get("ledger_id"):
            raise ValueError("existing_ledger_row_invalid")
        existing_ids.add(str(payload["ledger_id"]))
    return existing_ids


def _resolve_under_reports(root: Path, value: str | Path, *, suffix: str) -> Path:
    canonical_root = _canonical_root(root)
    allowed = _reports_root(canonical_root)
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else canonical_root / candidate
    candidate = candidate.resolve(strict=False)
    if not _path_is_within(candidate, allowed):
        raise ValueError("output_must_be_under_data_reports")
    if candidate.suffix.lower() != suffix:
        raise ValueError(f"output_must_use_{suffix}_suffix")
    return candidate


def _canonical_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _reports_root(root: Path) -> Path:
    return (root / "data" / "reports").resolve(strict=False)


def _path_is_within(candidate: Path, allowed: Path) -> bool:
    candidate_key = os.path.normcase(os.path.abspath(str(candidate)))
    allowed_key = os.path.normcase(os.path.abspath(str(allowed)))
    try:
        return os.path.commonpath([candidate_key, allowed_key]) == allowed_key
    except ValueError:
        return False


def _ledger_thread_lock_for(target: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(target.resolve(strict=False))))
    with _LEDGER_THREAD_LOCKS_GUARD:
        return _LEDGER_THREAD_LOCKS.setdefault(key, threading.RLock())
