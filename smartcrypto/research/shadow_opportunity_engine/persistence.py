"""Restricted persistence for research reports and idempotent ledger events."""

from __future__ import annotations

import json
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


def resolve_report_path(root: Path, value: str | Path) -> Path:
    return _resolve_under_reports(root, value, suffix=".json")


def resolve_ledger_path(root: Path, value: str | Path) -> Path:
    return _resolve_under_reports(root, value, suffix=".jsonl")


def write_report(root: Path, path: Path, report: Mapping[str, Any]) -> None:
    policy = AtomicWritePolicy.restricted(
        [(root / "data" / "reports").resolve()],
        working_directory=root,
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
    target = resolve_ledger_path(root, path)
    policy = AtomicWritePolicy.restricted(
        [(root / "data" / "reports").resolve()],
        working_directory=root,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
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
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else root / candidate
    candidate = candidate.resolve()
    allowed = (root / "data" / "reports").resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("output_must_be_under_data_reports") from exc
    if candidate.suffix.lower() != suffix:
        raise ValueError(f"output_must_use_{suffix}_suffix")
    return candidate
