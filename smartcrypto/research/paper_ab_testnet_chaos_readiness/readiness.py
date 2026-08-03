"""B06 orchestration: evidence evaluation only, no operational authority."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import (
    CONFIG_SCHEMA_VERSION,
    DECISION_BLOCKED,
    DECISION_READY,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_CHAOS_SCENARIOS,
    REQUIRED_TESTNET_STAGES,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    mapping,
)
from .gates import (
    evaluate_capacity,
    evaluate_chaos,
    evaluate_incidents,
    evaluate_prerequisites,
    evaluate_testnet,
)
from .io import (
    canonical_sha256,
    load_config,
    load_evidence,
    report_path_errors,
    resolve,
)
from .paper_ab import evaluate_paper_ab
from .writer import B01AtomicReportWriter, ReportWriter


def build_paper_ab_testnet_chaos_readiness_v2(
    *,
    project_root: str | Path,
    evidence_path: str | Path | None = None,
    evidence_payload: Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    config_payload: Mapping[str, Any] | None = None,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    writer_backend: ReportWriter | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Evaluate all B06 gates and optionally persist an advisory report."""

    root = Path(project_root).resolve()
    output_json = resolve(root, output_json_path, DEFAULT_REPORT_JSON)
    output_markdown = resolve(
        root,
        output_markdown_path,
        DEFAULT_REPORT_MARKDOWN,
    )
    path_errors = (
        report_path_errors(root, output_json, output_markdown)
        if write_report
        else []
    )
    config, config_source, config_errors = load_config(
        root,
        config_path,
        config_payload,
    )
    evidence, evidence_source, evidence_errors = load_evidence(
        root,
        evidence_path,
        evidence_payload,
    )

    gates: dict[str, dict[str, Any]] = {
        "prerequisites": evaluate_prerequisites(
            evidence.get("prerequisites")
        ),
        "paper_ab": evaluate_paper_ab(
            evidence.get("paper_ab"),
            config,
        ),
        "testnet_e2e": evaluate_testnet(
            evidence.get("testnet_e2e"),
            config,
        ),
        "chaos": evaluate_chaos(
            evidence.get("chaos"),
            config,
        ),
        "capacity": evaluate_capacity(
            evidence.get("capacity"),
            config,
        ),
        "incidents": evaluate_incidents(evidence.get("incidents")),
    }
    top_level_errors = sorted(
        set([*path_errors, *config_errors, *evidence_errors])
    )
    failed_gate_ids = [
        name
        for name, gate_report in gates.items()
        if gate_report.get("passed") is not True
    ]
    blockers = sorted(
        set(
            [
                *top_level_errors,
                *(
                    f"{name}:{blocker}"
                    for name, gate_report in gates.items()
                    for blocker in gate_report.get("blockers", [])
                ),
            ]
        )
    )
    ready_for_soak = not top_level_errors and not failed_gate_ids
    safety = dict(SAFETY_FLAGS)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": (
            generated_at_utc or datetime.now(UTC).isoformat()
        ),
        "project_root": str(root),
        "status": "ok" if ready_for_soak else "blocked",
        "reason": (
            "all_b06_gates_passed"
            if ready_for_soak
            else "one_or_more_b06_gates_blocked"
        ),
        "decision": (
            DECISION_READY if ready_for_soak else DECISION_BLOCKED
        ),
        "b06_implementation_scope": [
            "paper_ab",
            "testnet_e2e_evidence",
            "chaos_recovery_evidence",
            "capacity_market_impact",
            "soak_readiness",
        ],
        "evidence_source": evidence_source,
        "evidence_sha256": (
            canonical_sha256(evidence) if evidence else None
        ),
        "config_source": config_source,
        "config_sha256": canonical_sha256(config) if config else None,
        "gate_count": len(gates),
        "passed_gate_count": sum(
            gate_report.get("passed") is True
            for gate_report in gates.values()
        ),
        "failed_gate_count": len(failed_gate_ids),
        "failed_gate_ids": failed_gate_ids,
        "gates": gates,
        "ready_for_30_day_soak": ready_for_soak,
        "soak_start_authority": "advisory_only",
        "testnet_execution_performed": False,
        "chaos_execution_performed": False,
        "paper_ab_recommendation": gates["paper_ab"].get(
            "recommendation"
        ),
        "capacity_recommendations": gates["capacity"].get(
            "safe_notional_by_symbol",
            {},
        ),
        "blockers": blockers,
        "warnings": [],
        "write_report_requested": write_report,
        "write_report_performed": False,
        "output_paths": {
            "json": str(output_json),
            "markdown": str(output_markdown),
        },
        **safety,
        "safety_flags": safety,
    }

    if write_report and not path_errors:
        writer = writer_backend or B01AtomicReportWriter(root)
        persisted_report = dict(report)
        persisted_report["write_report_performed"] = True
        writer.write_json(output_json, persisted_report)
        writer.write_text(
            output_markdown,
            render_markdown(persisted_report),
        )
        report = persisted_report
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render the compact operator-facing B06 readiness summary."""

    lines = [
        "# Paper A/B, Testnet, Chaos and Readiness V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Ready for 30-day soak: `{report.get('ready_for_30_day_soak')}`",
        "",
        "## Gates",
        "",
    ]
    for name, gate_report in mapping(report.get("gates")).items():
        item = mapping(gate_report)
        lines.append(
            f"- `{name}`: `{item.get('status')}` — "
            f"`{item.get('reason')}`"
        )
    lines.extend(
        [
            "",
            "Research/paper/shadow only; no operational authority.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "B01AtomicReportWriter",
    "CONFIG_SCHEMA_VERSION",
    "DECISION_BLOCKED",
    "DECISION_READY",
    "EVIDENCE_SCHEMA_VERSION",
    "REQUIRED_CHAOS_SCENARIOS",
    "REQUIRED_TESTNET_STAGES",
    "SCHEMA_VERSION",
    "build_paper_ab_testnet_chaos_readiness_v2",
    "render_markdown",
]
