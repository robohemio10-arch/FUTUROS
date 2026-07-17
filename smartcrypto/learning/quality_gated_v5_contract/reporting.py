"""Deterministic research report rendering and guarded report-only persistence."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def stable_json(value: Any) -> str:
    return json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def evidence_hash(report: dict[str, Any]) -> str:
    excluded = {
        "generated_at_utc",
        "write_requested",
        "write_performed",
        "output_paths",
        "evidence_hash",
        "row_records",
    }
    payload = {
        key: value for key, value in report.items() if key not in excluded
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def ensure_report_path(project_root: Path, path: Path) -> Path:
    resolved = (
        path.resolve()
        if path.is_absolute()
        else (project_root / path).resolve()
    )
    allowed = (project_root / "data" / "reports").resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"report_path_outside_data_reports:{resolved}") from exc
    return resolved


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(
            json_safe(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    lines = [stable_json(record) for record in records]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def render_markdown(report: dict[str, Any]) -> str:
    eligibility = report.get("eligibility", {})
    provenance = report.get("provenance", {})
    freshness = report.get("freshness", {})
    nonregression = report.get("nonregression", {})
    safety = report.get("safety_flags", {})
    unavailability_reasons = nonregression.get(
        "canonical_identity_unavailability_reasons",
        [],
    )
    lines = [
        "# Quality-Gated V5 Provenance, Freshness and Non-Regression Contract V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Universe rows: `{report.get('universe_rows')}`",
        f"- Eligible rows: `{eligibility.get('eligible_rows')}`",
        f"- Blocked rows: `{eligibility.get('blocked_rows')}`",
        f"- V5 recognized rows: `{provenance.get('v5_recognized_rows')}`",
        f"- Stale 1m rows: `{freshness.get('stale_1m_rows')}`",
        f"- Stale 5m rows: `{freshness.get('stale_5m_rows')}`",
        f"- Future snapshots: `{freshness.get('future_snapshot_rows')}`",
        f"- In-progress snapshots: `{freshness.get('in_progress_snapshot_rows')}`",
        f"- Official rows: `{nonregression.get('official_rows')}`",
        f"- Projected rows: `{nonregression.get('projected_rows')}`",
        f"- Non-regression status: `{nonregression.get('status')}`",
        f"- Non-regression reason: `{nonregression.get('reason')}`",
        "- Canonical non-regression evaluable: "
        f"`{nonregression.get('canonical_nonregression_evaluable')}`",
        "- Artifact trade_id namespace compatible: "
        f"`{nonregression.get('artifact_trade_id_namespace_compatible')}`",
        "- Artifact trade_id overlap: "
        f"`{nonregression.get('artifact_trade_id_overlap_unique_keys')}`",
        "- Diagnostic order_id overlap: "
        f"`{nonregression.get('order_id_diagnostic_overlap_unique_keys')}`",
        "- Diagnostic order_id universe duplicate rows: "
        f"`{nonregression.get('order_id_diagnostic_universe_duplicate_rows')}`",
        "- Official identity loss proven: "
        f"`{nonregression.get('official_identity_loss_proven')}`",
        "- Official identity retention proven: "
        f"`{nonregression.get('official_identity_retention_proven')}`",
        "- Unexplained removed official IDs: "
        f"`{nonregression.get('unexplained_removed_official_ids')}`",
        f"- Evidence hash: `{report.get('evidence_hash')}`",
        "",
        "## Canonical identity availability",
        "",
    ]
    if unavailability_reasons:
        for reason in unavailability_reasons:
            lines.append(f"- `{reason}`")
    else:
        lines.append("- `canonical_identity_available`")

    lines.extend(
        [
            "",
            "`null` for unexplained removed official IDs means the gate was not "
            "evaluable; it does not mean zero unexplained removals.",
            "",
            "## Safety",
            "",
        ]
    )
    for key in sorted(safety):
        lines.append(f"- `{key}`: `{safety[key]}`")
    lines.extend(
        [
            "",
            "This report is research-only and projection-only. It does not train, "
            "promote, register, rebuild, start services, submit orders, or alter "
            "the official quality-gated dataset.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    *,
    project_root: Path,
    report: dict[str, Any],
    row_records: list[dict[str, Any]],
    report_json: Path,
    report_rows_jsonl: Path,
    report_markdown: Path,
) -> None:
    resolved_json = ensure_report_path(project_root, report_json)
    resolved_jsonl = ensure_report_path(project_root, report_rows_jsonl)
    resolved_markdown = ensure_report_path(project_root, report_markdown)

    main_payload = {
        key: value for key, value in report.items() if key != "row_records"
    }
    write_json(resolved_json, main_payload)
    write_jsonl(resolved_jsonl, row_records)
    atomic_write_text(resolved_markdown, render_markdown(report))
