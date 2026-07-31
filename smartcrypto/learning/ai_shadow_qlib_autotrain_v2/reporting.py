"""B01-backed report publication for B05."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def write_reports(
    report: Mapping[str, Any],
    *,
    project_root: Path,
    json_path: Path,
    markdown_path: Path,
) -> None:
    from smartcrypto.runtime.integrity_traceability_v2 import (
        AtomicWritePolicy,
        atomic_write_json,
        atomic_write_text,
    )

    policy = AtomicWritePolicy.project_data(working_directory=project_root)
    atomic_write_json(json_path, dict(report), policy=policy, allow_nan=False)
    atomic_write_text(markdown_path, render_markdown(report), policy=policy)


def render_markdown(report: Mapping[str, Any]) -> str:
    safety = report.get("safety_flags", {})
    training = report.get("training_governance", {})
    harness = report.get("counterfactual_harness", {})
    drift = report.get("drift_overlay", {})
    lines = [
        "# AI Shadow, Qlib e Autotreinamento Governado V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Authoritative result: `{report.get('authoritative_result')}`",
        f"- Counterfactual decisions: `{harness.get('decision_count', 0)}`",
        f"- Research training eligible: `{training.get('research_training_eligible')}`",
        f"- Drift status: `{drift.get('status')}`",
        f"- Result hash: `{report.get('result_hash')}`",
        "",
        "## Safety",
        "",
    ]
    if isinstance(safety, Mapping):
        for key in sorted(safety):
            lines.append(f"- `{key}={safety[key]}`")
    lines.extend(
        [
            "",
            "## Training blockers",
            "",
        ]
    )
    blockers = training.get("blockers", []) if isinstance(training, Mapping) else []
    if isinstance(blockers, list) and blockers:
        lines.extend(f"- `{item}`" for item in blockers)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
