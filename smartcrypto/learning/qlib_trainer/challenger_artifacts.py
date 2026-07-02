"""Report and challenger artifact writers for ranking trainer evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def write_report_artifacts(
    *,
    report: Mapping[str, Any],
    metrics: Mapping[str, Any],
    report_json: Path,
    report_md: Path,
    metrics_json: Path,
    metrics_md: Path,
) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    metrics_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(stable_pretty_json(report), encoding="utf-8")
    metrics_json.write_text(stable_pretty_json(metrics), encoding="utf-8")
    report_md.write_text(render_report_markdown(report), encoding="utf-8")
    metrics_md.write_text(render_metrics_markdown(metrics), encoding="utf-8")


def write_challenger_artifact(
    *,
    root: Path,
    generated_at_utc: str,
    metadata: Mapping[str, Any],
    metrics: Mapping[str, Any],
    model_payload: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    safe_timestamp = generated_at_utc.replace(":", "").replace("+", "Z")
    artifact_dir = root / "data" / "models" / "challengers" / "qlib_institutional_ranking_v1" / safe_timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "metadata": str(artifact_dir / "metadata.json"),
        "metrics": str(artifact_dir / "metrics.json"),
        "model": str(artifact_dir / "model.json"),
    }
    Path(paths["metadata"]).write_text(stable_pretty_json(metadata), encoding="utf-8")
    Path(paths["metrics"]).write_text(stable_pretty_json(metrics), encoding="utf-8")
    Path(paths["model"]).write_text(stable_pretty_json(model_payload), encoding="utf-8")
    hashes = {name: file_sha256(Path(path)) for name, path in paths.items()}
    return paths, hashes


def render_report_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Qlib Institutional Ranking Trainer V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Backend: `{report.get('backend_name')}`",
            f"- Split count: `{report.get('split_count')}`",
            f"- Evaluated splits: `{report.get('evaluated_split_count')}`",
            f"- Candidate decision: `{report.get('candidate_decision')}`",
            f"- Promotion eligible: `{report.get('promotion_eligible')}`",
            "",
            "This report is challenger research evidence only. It does not promote models or update runtime.",
            "",
        ]
    )


def render_metrics_markdown(metrics: Mapping[str, Any]) -> str:
    aggregate = metrics.get("aggregate_metrics", {}) if isinstance(metrics.get("aggregate_metrics"), Mapping) else {}
    return "\n".join(
        [
            "# Qlib Institutional Ranking Metrics V1",
            "",
            f"- Evaluated splits: `{metrics.get('evaluated_split_count')}`",
            f"- Mean RankIC: `{aggregate.get('mean_rank_ic')}`",
            f"- Mean precision@10: `{aggregate.get('mean_precision_at_10')}`",
            f"- Selected top-k EV total: `{aggregate.get('selected_top_k_expected_value_total')}`",
            "",
            "Metrics are calculated per walk-forward split with train-only preprocessing.",
            "",
        ]
    )


def stable_pretty_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n"


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
