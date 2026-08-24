"""Restricted persistence for Paper Capacity Scaleout V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_json,
    atomic_write_text,
)


DEFAULT_REPORT = Path("data/reports/paper_capacity_scaleout_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_capacity_scaleout_v1.md")
DEFAULT_RESEARCH = Path(
    "data/research/paper_capacity_scaleout_v1_recovered_opportunities.jsonl"
)


def _resolve_under(
    root: Path,
    value: str | Path,
    allowed_relative: str,
    suffix: str,
) -> Path:
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else root / candidate
    candidate = candidate.resolve()
    allowed = (root / allowed_relative).resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(
            f"output_must_be_under_{allowed_relative.replace('/', '_')}"
        ) from exc
    if candidate.suffix.lower() != suffix:
        raise ValueError(f"output_must_use_{suffix}_suffix")
    return candidate


def resolve_report_path(
    root: Path,
    value: str | Path | None = None,
) -> Path:
    return _resolve_under(
        root,
        value or DEFAULT_REPORT,
        "data/reports",
        ".json",
    )


def resolve_report_markdown_path(
    root: Path,
    value: str | Path | None = None,
) -> Path:
    return _resolve_under(
        root,
        value or DEFAULT_REPORT_MD,
        "data/reports",
        ".md",
    )


def resolve_research_path(
    root: Path,
    value: str | Path | None = None,
) -> Path:
    return _resolve_under(
        root,
        value or DEFAULT_RESEARCH,
        "data/research",
        ".jsonl",
    )


def _policy(root: Path, relative: str) -> AtomicWritePolicy:
    return AtomicWritePolicy.restricted(
        [(root / relative).resolve()],
        working_directory=root,
    )


def write_report(
    root: Path,
    report_path: Path,
    markdown_path: Path,
    report: Mapping[str, Any],
) -> None:
    atomic_write_json(
        resolve_report_path(root, report_path),
        dict(report),
        policy=_policy(root, "data/reports"),
        allow_nan=False,
    )
    atomic_write_text(
        resolve_report_markdown_path(root, markdown_path),
        _render_markdown(report),
        policy=_policy(root, "data/reports"),
    )


def write_research_rows(
    root: Path,
    path: Path,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    normalized = [dict(row) for row in rows]
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
        for row in normalized
    )
    atomic_write_text(
        resolve_research_path(root, path),
        rendered,
        policy=_policy(root, "data/research"),
    )
    return len(normalized)


def _render_markdown(report: Mapping[str, Any]) -> str:
    evidence = report.get("capacity_evidence", {})
    lines = [
        "# Paper Capacity Scaleout V1",
        "",
        f"- status: `{report.get('status')}`",
        f"- reason: `{report.get('reason')}`",
        f"- decision: `{report.get('decision')}`",
        f"- evidence: `{evidence.get('status')}`",
        "- capacity_activation_allowed: `false`",
        "",
        "## Safety",
        "",
        "- Research/paper/shadow only.",
        "- No runtime capacity change.",
        "- No RiskManager/strategy/model/order change.",
        "",
    ]
    return "\n".join(lines)
