"""Restricted, atomic persistence for Financial AI research artifacts."""

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


DEFAULT_REPORT = Path("data/reports/financial_ai_research_engine_v1.json")
DEFAULT_ESTIMATES = Path(
    "data/reports/financial_ai_candidate_estimates_v1.jsonl"
)


def resolve_report_path(
    root: Path,
    value: str | Path | None,
) -> Path:
    return _resolve(
        root,
        value or DEFAULT_REPORT,
        ".json",
    )


def resolve_estimates_path(
    root: Path,
    value: str | Path | None,
) -> Path:
    return _resolve(
        root,
        value or DEFAULT_ESTIMATES,
        ".jsonl",
    )


def write_report(
    root: Path,
    path: Path,
    report: Mapping[str, Any],
) -> None:
    policy = _policy(root)
    atomic_write_json(
        path,
        report,
        policy=policy,
        allow_nan=False,
    )


def write_estimates_idempotent(
    root: Path,
    path: Path,
    estimates: Iterable[Mapping[str, Any]],
) -> int:
    target = resolve_estimates_path(root, path)
    rows = [dict(row) for row in estimates]
    if not rows:
        return 0

    policy = _policy(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _InterProcessFileLock(
        target.parent
        / f".{target.name}.idempotency.lock",
        timeout_seconds=policy.lock_timeout_seconds,
    )
    lock.acquire()
    try:
        existing = _read_existing(target)
        by_id = {
            str(row["estimate_id"]): row
            for row in existing
        }
        appended = 0

        for row in rows:
            estimate_id = str(
                row.get("estimate_id") or ""
            ).strip()
            if not estimate_id:
                raise ValueError("estimate_id_required")

            prior = by_id.get(estimate_id)
            if prior is not None:
                if _semantic_estimate(
                    prior
                ) != _semantic_estimate(row):
                    raise ValueError(
                        "estimate_id_conflict"
                    )
                continue

            by_id[estimate_id] = row
            appended += 1

        if appended:
            rendered = "".join(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
                for row in by_id.values()
            )
            atomic_write_text(
                target,
                rendered,
                policy=policy,
            )

        return appended
    finally:
        lock.release()


def _read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if (
            not isinstance(payload, dict)
            or not payload.get("estimate_id")
        ):
            raise ValueError(
                "existing_estimate_row_invalid"
            )
        rows.append(payload)
    return rows


def _semantic_estimate(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the complete semantic estimate.

    Point-in-time generated/available timestamps are intentionally included:
    changing them changes the meaning and must not be silently deduplicated.
    """

    return dict(row)


def _policy(root: Path) -> AtomicWritePolicy:
    return AtomicWritePolicy.restricted(
        [(root / "data" / "reports").resolve()],
        working_directory=root,
    )


def _resolve(
    root: Path,
    value: str | Path,
    suffix: str,
) -> Path:
    candidate = Path(value)
    candidate = (
        candidate
        if candidate.is_absolute()
        else root / candidate
    )
    candidate = candidate.resolve()
    allowed = (
        root / "data" / "reports"
    ).resolve()

    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(
            "output_must_be_under_data_reports"
        ) from exc

    if candidate.suffix.lower() != suffix:
        raise ValueError(
            f"output_must_use_{suffix}_suffix"
        )

    return candidate
