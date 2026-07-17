"""Stable research reporting with no operational artifact authority."""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contracts import PipelinePaths


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA:
        return None
    return value


def render_markdown(report: dict[str, Any]) -> str:
    reconciliation = report.get("master_row_reconciliation", {})
    environment = report.get("environment_gate", {})
    diagnostic = report.get("diagnostic_ranking", [])
    eligible = report.get("eligible_candidate_ranking", [])
    lines = [
        "# Market Features Rematerialization and First Training Runs V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Candidate decision: `{report.get('decision')}`",
        f"- Research decision: `{report.get('research_decision')}`",
        f"- Canonical environment: `{environment.get('status')}`",
        f"- Expected environment: `{environment.get('expected')}`",
        f"- Observed environment: `{environment.get('observed')}`",
        f"- Canonical Master rows: `{reconciliation.get('canonical_rows')}`",
        f"- Expected Master rows: `{reconciliation.get('expected_rows')}`",
        f"- Explicit row-count delta: `{reconciliation.get('row_count_delta')}`",
        f"- Master ready rows: `{report.get('master_ready_row_count')}`",
        f"- Paper V1 consumed rows: `{report.get('paper_evaluation_set_v1_consumed_count')}`",
        f"- Prospective holdout V2 rows: `{report.get('prospective_holdout_v2_count')}`",
        f"- Paper V1 watermark: `{report.get('paper_evaluation_set_v1_watermark_utc')}`",
        "",
        "## Diagnostic ranking",
        "",
        "Diagnostic order is not candidate eligibility or promotion authority.",
        "",
        "| Rank | Model | Kind | Score |",
        "|---:|---|---|---:|",
    ]
    for item in diagnostic:
        lines.append(
            f"| {item.get('rank')} | {item.get('model_name')} | "
            f"{item.get('model_kind')} | {item.get('ranking_score', 0.0):.6f} |"
        )
    lines.extend(
        [
            "",
            "## Eligible candidate ranking",
            "",
            f"Eligible candidates: `{len(eligible)}`",
            f"Selected candidate: `{report.get('selected_candidate')}`",
            "",
            "## Concept drift",
            "",
            f"- Status: `{report.get('concept_drift', {}).get('status')}`",
            f"- Cohorts: `{report.get('concept_drift', {}).get('cohort_counts')}`",
            "- Metrics: PSI, KS, Wasserstein, label drift, and net PnL drift.",
            "- Decomposition: symbol, side, ISO week, and provenance.",
            "- Provenance is diagnostic only and never a model feature.",
            "",
            "## Institutional boundaries",
            "",
            "A non-canonical Python/scikit-learn/joblib environment may run diagnostics only.",
            "Paper rows never enter fitting, calibration, or threshold selection.",
            "No model is serialized, registered, promoted, or connected to runtime.",
            "Rows with unavailable point-in-time features remain blocked; no imputation is used.",
            "",
        ]
    )
    return "\n".join(lines)


def write_research_outputs(
    *,
    paths: PipelinePaths,
    master: pd.DataFrame,
    paper: pd.DataFrame,
    predictions: pd.DataFrame,
    report: dict[str, Any],
) -> list[str]:
    _validate_output_paths(paths)
    written: list[str] = []
    for path, frame in (
        (paths.master_dataset_path, master),
        (paths.paper_dataset_path, paper),
        (paths.predictions_path, predictions),
    ):
        _atomic_parquet(path, frame)
        written.append(str(path))
    materialized = {**report, "write_performed": True}
    _atomic_text(
        paths.report_json_path,
        json.dumps(json_safe(materialized), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    _atomic_text(paths.report_markdown_path, render_markdown(materialized))
    written.extend((str(paths.report_json_path), str(paths.report_markdown_path)))
    return written


def _validate_output_paths(paths: PipelinePaths) -> None:
    data_root = (paths.project_root / "data").resolve()
    for path in (
        paths.master_dataset_path,
        paths.paper_dataset_path,
        paths.predictions_path,
        paths.report_json_path,
        paths.report_markdown_path,
    ):
        try:
            path.resolve().relative_to(data_root)
        except ValueError as exc:
            raise ValueError(f"research_output_outside_data:{path}") from exc


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        frame.to_parquet(temporary_path, index=False)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)
