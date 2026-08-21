"""Restricted atomic persistence for Paper A/B Edge Selector research artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_json,
    atomic_write_text,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    _InterProcessFileLock,
)


DEFAULT_REPORT = Path("data/reports/paper_ab_edge_selector_v1.json")
DEFAULT_ASSIGNMENTS = Path("data/reports/paper_ab_edge_selector_assignments_v1.jsonl")


def _policy(root: Path) -> AtomicWritePolicy:
    return AtomicWritePolicy.restricted(
        [(root / "data" / "reports").resolve()],
        working_directory=root,
    )


def _resolve(root: Path, value: str | Path, suffix: str) -> Path:
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


def resolve_report_path(root: Path, value: str | Path | None = None) -> Path:
    return _resolve(root, value or DEFAULT_REPORT, ".json")


def resolve_assignments_path(root: Path, value: str | Path | None = None) -> Path:
    return _resolve(root, value or DEFAULT_ASSIGNMENTS, ".jsonl")


def write_report(root: Path, path: Path, report: Mapping[str, Any]) -> None:
    atomic_write_json(
        resolve_report_path(root, path),
        dict(report),
        policy=_policy(root),
        allow_nan=False,
    )


def _read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or not str(payload.get("assignment_id") or "").strip():
            raise ValueError("existing_assignment_row_invalid")
        rows.append(payload)
    return rows


def write_assignments_idempotent(
    root: Path,
    path: Path,
    assignments: Iterable[Mapping[str, Any]],
) -> int:
    target = resolve_assignments_path(root, path)
    incoming = [dict(row) for row in assignments]
    if not incoming:
        return 0

    policy = _policy(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _InterProcessFileLock(
        target.parent / f".{target.name}.idempotency.lock",
        timeout_seconds=policy.lock_timeout_seconds,
    )
    lock.acquire()
    try:
        existing = _read_existing(target)
        by_id: dict[str, dict[str, Any]] = {}
        for row in existing:
            assignment_id = str(row["assignment_id"])
            prior = by_id.get(assignment_id)
            if prior is not None and prior != row:
                raise ValueError("existing_assignment_id_conflict")
            by_id[assignment_id] = row
        appended = 0

        for row in incoming:
            assignment_id = str(row.get("assignment_id") or "").strip()
            if not assignment_id:
                raise ValueError("assignment_id_required")
            prior = by_id.get(assignment_id)
            if prior is not None:
                if prior != row:
                    raise ValueError("assignment_id_conflict")
                continue
            by_id[assignment_id] = row
            appended += 1

        if appended:
            rendered = "".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                    default=str,
                )
                + "\n"
                for row in by_id.values()
            )
            atomic_write_text(target, rendered, policy=policy)
        return appended
    finally:
        lock.release()
