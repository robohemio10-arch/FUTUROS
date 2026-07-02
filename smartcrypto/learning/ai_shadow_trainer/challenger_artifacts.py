"""Artifact writers for research-only AI Shadow quality veto challengers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def write_report_artifacts(
    *,
    report: dict[str, Any],
    metrics_payload: dict[str, Any],
    report_json: Path,
    report_md: Path,
    metrics_json: Path,
    metrics_md: Path,
) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(stable_json(report), encoding="utf-8")
    report_md.write_text(render_report_markdown(report), encoding="utf-8")
    metrics_json.write_text(stable_json(metrics_payload), encoding="utf-8")
    metrics_md.write_text(render_metrics_markdown(metrics_payload), encoding="utf-8")


def write_challenger_artifact(
    *,
    root: Path,
    generated_at_utc: str,
    metadata: dict[str, Any],
    model_payload: dict[str, Any],
    metrics: dict[str, Any],
    thresholds: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    safe_timestamp = generated_at_utc.replace(":", "").replace("+", "Z")
    artifact_dir = root / "data" / "models" / "challengers" / "ai_shadow_quality_veto_v1" / safe_timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "metadata": artifact_dir / "metadata.json",
        "model": artifact_dir / "model.joblib",
        "metrics": artifact_dir / "metrics.json",
        "thresholds": artifact_dir / "thresholds.json",
    }
    paths["metadata"].write_text(stable_json(metadata), encoding="utf-8")
    paths["model"].write_text(stable_json(model_payload), encoding="utf-8")
    paths["metrics"].write_text(stable_json(metrics), encoding="utf-8")
    paths["thresholds"].write_text(stable_json({"threshold_by_symbol_side_regime": thresholds}), encoding="utf-8")
    path_strings = {key: str(path) for key, path in paths.items()}
    hashes = {key: file_sha256(path) for key, path in paths.items()}
    return path_strings, hashes


def render_report_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# AI Shadow Quality Veto Trainer V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Trainer status: `{report.get('trainer_status')}`",
            f"- Feature columns: `{report.get('feature_column_count')}`",
            f"- Split count: `{report.get('split_count')}`",
            f"- Candidate decision: `{report.get('candidate_decision')}`",
            f"- Promotion eligible: `{report.get('promotion_eligible')}`",
            f"- Veto runtime active: `{report.get('veto_runtime_active')}`",
            "",
            "Research-only evidence. This report does not activate vetoes, write an active registry, promote models, update runtime, change risk, access exchange, or send orders.",
            "",
        ]
    )


def render_metrics_markdown(metrics: dict[str, Any]) -> str:
    aggregate = metrics.get("aggregate_metrics", {})
    return "\n".join(
        [
            "# AI Shadow Quality Veto Metrics V1",
            "",
            f"- Evaluated splits: `{metrics.get('evaluated_split_count')}`",
            f"- Accepted expected value total: `{aggregate.get('accepted_expected_value_total')}`",
            f"- Net EV delta research-only: `{aggregate.get('net_ev_delta_if_applied_research_only_total')}`",
            "",
        ]
    )


def stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
