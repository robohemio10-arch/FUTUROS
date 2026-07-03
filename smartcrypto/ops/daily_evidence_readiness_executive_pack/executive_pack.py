"""Executive evidence/readiness pack for SMART FUTUROS.

This pack consolidates existing research-only evidence into JSON, Markdown and
HTML. It never creates schedulers, trains models, promotes artifacts, updates
runtime, writes registry data, accesses private exchange APIs, or submits
orders.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.learning.ai_qlib_drift_regime_monitor import build_ai_qlib_drift_regime_monitor_v1
from smartcrypto.learning.paper_autotrain_feedback_loop import build_paper_autotrain_feedback_loop_v1
from smartcrypto.learning.qlib_backend_environment_lock import build_qlib_environment_lock_report
from smartcrypto.learning.qlib_backend_gate import build_qlib_research_backend_gate_report
from smartcrypto.research.daily_learning_evidence_readiness_integration import (
    build_daily_learning_evidence_readiness_integration_snapshot,
)

SCHEMA_VERSION = "daily_evidence_readiness_executive_pack_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_REPORT_JSON = Path("data/reports/daily_evidence_readiness_executive_pack_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/daily_evidence_readiness_executive_pack_v1.md")
DEFAULT_REPORT_HTML = Path("data/reports/daily_evidence_readiness_executive_pack_v1.html")

INPUT_SOURCE_PATHS: tuple[tuple[str, str, bool], ...] = (
    ("qlib_environment_lock_report", "data/reports/qlib_research_backend_environment_lock_v1.json", False),
    ("qlib_backend_gate_report", "data/reports/qlib_research_backend_gate_v1.json", False),
    ("paper_autotrain_feedback_loop_report", "data/reports/paper_autotrain_feedback_loop_v1.json", False),
    (
        "daily_learning_evidence_readiness_report",
        "data/reports/daily_learning_evidence_readiness_integration_v1.json",
        False,
    ),
    ("ai_qlib_drift_regime_monitor_report", "data/reports/ai_qlib_drift_regime_monitor_v1.json", False),
)

CRITICAL_SECTION_IDS = (
    "qlib_backend_section",
    "paper_autotrain_section",
    "daily_learning_readiness_section",
    "ai_qlib_drift_regime_section",
)


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "source": self.source,
        }


def build_daily_evidence_readiness_executive_pack_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    report_html_path: str | Path | None = None,
    generated_at_utc: str | None = None,
    component_payloads: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the executive pack from internal no-write builders or injected payloads."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    injected = component_payloads or {}
    qlib_lock = dict(injected.get("qlib_environment_lock") or build_qlib_environment_lock_report(project_root=root, write=False))
    qlib_gate = dict(injected.get("qlib_backend_gate") or build_qlib_research_backend_gate_report(project_root=root, write=False))
    paper_autotrain = dict(
        injected.get("paper_autotrain")
        or build_paper_autotrain_feedback_loop_v1(
            project_root=root,
            write_report=False,
            allow_runtime_read=False,
            run_qlib_train=False,
            run_ai_shadow_train=False,
        )
    )
    daily_readiness = dict(
        injected.get("daily_learning_readiness")
        or build_daily_learning_evidence_readiness_integration_snapshot(
            project_root=root,
            paper_autotrain_feedback_loop_payload=paper_autotrain,
        )
    )
    drift_regime = dict(
        injected.get("ai_qlib_drift_regime")
        or build_ai_qlib_drift_regime_monitor_v1(project_root=root, write_report=False)
    )

    input_sources = build_input_sources(root)
    qlib_backend_section = build_qlib_backend_section(qlib_lock, qlib_gate)
    paper_autotrain_section = build_paper_autotrain_section(paper_autotrain)
    daily_learning_section = build_daily_readiness_section(daily_readiness)
    drift_section = build_drift_regime_section(drift_regime)
    sections = {
        "qlib_backend_section": qlib_backend_section,
        "paper_autotrain_section": paper_autotrain_section,
        "daily_learning_readiness_section": daily_learning_section,
        "ai_qlib_drift_regime_section": drift_section,
    }
    blockers = collect_blockers(sections, input_sources)
    warnings = collect_warnings(sections)
    status = "blocked" if blockers else "warning" if warnings else "ok"
    reason = "executive_pack_blockers_present" if blockers else "executive_pack_warnings_present" if warnings else "executive_pack_loaded_research_only"
    lineage_hashes = collect_lineage_hashes(qlib_lock, qlib_gate, paper_autotrain, daily_readiness, drift_regime)
    safety = safety_flags()
    executive_summary = build_executive_summary(
        status=status,
        blockers=blockers,
        warnings=warnings,
        qlib_backend_section=qlib_backend_section,
        paper_autotrain_section=paper_autotrain_section,
        daily_learning_section=daily_learning_section,
        drift_section=drift_section,
    )
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)
    report_html = resolve(root, report_html_path, DEFAULT_REPORT_HTML)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "executive_summary": executive_summary,
        "readiness_summary": {
            "status": daily_learning_section["status"],
            "readiness_status": daily_learning_section["readiness_status"],
            "decision": daily_learning_section["decision"],
            "release_allowed": False,
            "readiness_release_authority": False,
            "operational_authority": False,
        },
        "qlib_backend_section": qlib_backend_section,
        "paper_autotrain_section": paper_autotrain_section,
        "daily_learning_readiness_section": daily_learning_section,
        "ai_qlib_drift_regime_section": drift_section,
        "blockers": blockers,
        "warnings": warnings,
        "lineage_hashes": lineage_hashes,
        "input_sources": [source.as_dict() for source in input_sources],
        "output_paths": {
            "json": str(report_json),
            "markdown": str(report_md),
            "html": str(report_html),
        },
        "write_requested": bool(write_report),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }
    if write_report:
        write_reports(report, report_json, report_md, report_html)
        report["write_performed"] = True
        write_json(report_json, report)
    return report


def build_input_sources(project_root: Path) -> list[SourceInfo]:
    sources: list[SourceInfo] = []
    for source_id, relative_path, required in INPUT_SOURCE_PATHS:
        path = project_root / relative_path
        exists = path.is_file()
        sources.append(
            SourceInfo(
                source_id=source_id,
                relative_path=relative_path,
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                source="existing_report_read_only" if exists else "internal_no_write_builder",
            )
        )
    return sources


def build_qlib_backend_section(lock: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    available = bool(gate.get("qlib_importable") or lock.get("qlib_importable"))
    status = "ok" if available and gate.get("status") == "ok" and lock.get("status") == "ok" else "blocked"
    blockers: list[str] = []
    if not available:
        blockers.append("qlib_backend_unavailable")
    if lock.get("environment_lock_status") not in {None, "locked"}:
        blockers.append("qlib_environment_lock_not_locked")
    return {
        "status": status,
        "reason": gate.get("reason") or lock.get("reason"),
        "qlib_backend_available": available,
        "qlib_backend_status": gate.get("qlib_backend_status") or lock.get("qlib_backend_status"),
        "qlib_importable": available,
        "qlib_version": gate.get("qlib_version") or lock.get("qlib_version"),
        "environment_lock_status": lock.get("environment_lock_status"),
        "dependency_contract_hash": gate.get("dependency_contract_hash"),
        "blockers": blockers,
        "warnings": [],
        "source": "internal_no_write_builder",
    }


def build_paper_autotrain_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    blockers = list_of_strings(payload.get("blockers"))
    warnings = list_of_strings(payload.get("warnings"))
    if bool(payload.get("sends_orders")):
        blockers.append("paper_autotrain_sends_orders_true")
    return {
        "status": str(payload.get("status", "blocked")),
        "reason": payload.get("reason"),
        "decision": payload.get("decision", DECISION_RESEARCH),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "lineage_hashes": mapping_or_empty(payload.get("lineage_hashes")),
        "write_performed": bool(payload.get("write_performed")),
        "run_qlib_train_requested": bool(payload.get("run_qlib_train_requested")),
        "run_ai_shadow_train_requested": bool(payload.get("run_ai_shadow_train_requested")),
        "source": "internal_no_write_builder",
    }


def build_daily_readiness_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    validation_errors = list_of_strings(payload.get("validation_errors"))
    readiness_summary = mapping_or_empty(payload.get("readiness_summary"))
    gate_summary = mapping_or_empty(payload.get("gate_summary"))
    blockers = []
    if payload.get("status") == "blocked" or payload.get("readiness_status") == "blocked":
        blockers.append("daily_learning_readiness_blocked")
    blockers.extend(validation_errors)
    return {
        "status": str(payload.get("status", "blocked")),
        "reason": payload.get("reason"),
        "decision": payload.get("decision", DECISION_RESEARCH),
        "readiness_status": payload.get("readiness_status", "blocked"),
        "readiness_summary": readiness_summary,
        "gate_summary": gate_summary,
        "blockers": sorted(set(blockers)),
        "warnings": [],
        "source": "internal_no_write_builder",
    }


def build_drift_regime_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    blockers = list_of_strings(payload.get("blockers"))
    warnings = list_of_strings(payload.get("warnings"))
    regime_summary = mapping_or_empty(payload.get("regime_summary"))
    overall_regime = regime_summary.get("overall_regime")
    if overall_regime in {"unstable", "critical"} and "ai_qlib_drift_regime_unstable" not in blockers:
        blockers.append("ai_qlib_drift_regime_unstable")
    return {
        "status": str(payload.get("status", "blocked")),
        "reason": payload.get("reason"),
        "decision": payload.get("decision", DECISION_RESEARCH),
        "overall_regime": overall_regime,
        "drift_summary": mapping_or_empty(payload.get("drift_summary")),
        "regime_summary": regime_summary,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "lineage_hashes": mapping_or_empty(payload.get("lineage_hashes")),
        "write_performed": bool(payload.get("write_performed")),
        "source": "internal_no_write_builder",
    }


def collect_blockers(sections: Mapping[str, Mapping[str, Any]], input_sources: Sequence[SourceInfo]) -> list[str]:
    blockers: list[str] = []
    for section_id in CRITICAL_SECTION_IDS:
        section = sections[section_id]
        if section.get("status") == "blocked":
            blockers.append(f"{section_id}:blocked")
        blockers.extend(f"{section_id}:{item}" for item in list_of_strings(section.get("blockers")))
    blockers.extend(f"missing_required_source:{source.relative_path}" for source in input_sources if source.required and not source.exists)
    return sorted(set(blockers))


def collect_warnings(sections: Mapping[str, Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for section_id, section in sections.items():
        if section.get("status") == "warning":
            warnings.append(f"{section_id}:warning")
        warnings.extend(f"{section_id}:{item}" for item in list_of_strings(section.get("warnings")))
    return sorted(set(warnings))


def collect_lineage_hashes(*payloads: Mapping[str, Any]) -> dict[str, Any]:
    hashes: dict[str, Any] = {}
    for payload in payloads:
        for key in (
            "contract_hash",
            "dependency_contract_hash",
            "feature_contract_hash",
            "dataset_hash",
            "target_store_hash",
            "split_engine_hash",
        ):
            if payload.get(key) is not None:
                hashes[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                if value is not None:
                    hashes[str(key)] = value
    return dict(sorted(hashes.items()))


def build_executive_summary(
    *,
    status: str,
    blockers: Sequence[str],
    warnings: Sequence[str],
    qlib_backend_section: Mapping[str, Any],
    paper_autotrain_section: Mapping[str, Any],
    daily_learning_section: Mapping[str, Any],
    drift_section: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "overall_status": status,
        "overall_decision": DECISION_RESEARCH,
        "release_allowed": False,
        "operational_authority": False,
        "primary_blockers": list(blockers[:10]),
        "primary_warnings": list(warnings[:10]),
        "qlib_backend_available": bool(qlib_backend_section.get("qlib_backend_available")),
        "paper_autotrain_decision": paper_autotrain_section.get("decision", DECISION_RESEARCH),
        "daily_readiness_status": daily_learning_section.get("readiness_status", "blocked"),
        "drift_regime_status": drift_section.get("status"),
        "overall_regime": drift_section.get("overall_regime"),
        "human_review_required": True,
        "manual_go_no_go_required": True,
    }


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "informational_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "creates_scheduler": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def write_reports(report: dict[str, Any], report_json: Path, report_md: Path, report_html: Path) -> None:
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    report_html.parent.mkdir(parents=True, exist_ok=True)
    report_html.write_text(render_html(report), encoding="utf-8")


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = mapping_or_empty(report.get("executive_summary"))
    return "\n".join(
        [
            "# SMART FUTUROS Daily Evidence Readiness Executive Pack",
            "",
            "## Executive Summary",
            f"- Status: `{summary.get('overall_status')}`",
            f"- Decision: `{summary.get('overall_decision')}`",
            f"- Release allowed: `{summary.get('release_allowed')}`",
            f"- Operational authority: `{summary.get('operational_authority')}`",
            f"- Human review required: `{summary.get('human_review_required')}`",
            "",
            "## Release Decision",
            "Informational only. No operational authority.",
            "",
            "## Current Blockers",
            *markdown_list(report.get("blockers")),
            "",
            "## Qlib Backend",
            markdown_mapping(report.get("qlib_backend_section")),
            "",
            "## Paper Autotrain Feedback Loop",
            markdown_mapping(report.get("paper_autotrain_section")),
            "",
            "## Daily Learning Readiness",
            markdown_mapping(report.get("daily_learning_readiness_section")),
            "",
            "## AI/Qlib Drift & Regime",
            markdown_mapping(report.get("ai_qlib_drift_regime_section")),
            "",
            "## Safety Invariants",
            markdown_mapping(report.get("safety_flags")),
            "",
            "## Allowed Next Steps",
            "- Human review of blocked/warning evidence.",
            "- Separate branch for any future operational change.",
            "",
            "## Forbidden Actions",
            "- Scheduler/cron/systemd/Windows Task creation.",
            "- Model promotion, registry write, runtime update, orders, or private exchange access.",
            "",
        ]
    )


def render_html(report: Mapping[str, Any]) -> str:
    summary = mapping_or_empty(report.get("executive_summary"))
    blockers = list_of_strings(report.get("blockers"))
    flags = mapping_or_empty(report.get("safety_flags"))
    cards = {
        "Status": summary.get("overall_status"),
        "Decision": summary.get("overall_decision"),
        "Qlib Backend": summary.get("qlib_backend_available"),
        "Daily Readiness": summary.get("daily_readiness_status"),
        "Drift Regime": summary.get("overall_regime"),
    }
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>SMART FUTUROS Daily Evidence Readiness Executive Pack</title>",
            "<style>",
            "body{font-family:Arial,sans-serif;margin:24px;color:#17202a;background:#f7f9fb}",
            ".card{display:inline-block;background:#fff;border:1px solid #d5dde5;border-radius:6px;padding:12px;margin:6px;min-width:150px}",
            "table{border-collapse:collapse;background:#fff;width:100%;margin:12px 0}",
            "th,td{border:1px solid #d5dde5;padding:8px;text-align:left}",
            ".notice{font-weight:bold;color:#7b241c}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>SMART FUTUROS Daily Evidence Readiness Executive Pack</h1>",
            f"<p>Generated at UTC: {escape(report.get('generated_at_utc'))}</p>",
            '<p class="notice">Informational only — no operational authority</p>',
            "<section><h2>Executive Summary</h2>",
            "".join(f'<div class="card"><strong>{escape(k)}</strong><br>{escape(v)}</div>' for k, v in cards.items()),
            "</section>",
            "<section><h2>Release Decision</h2><p>Release allowed: false. Decision: MANTER_EM_RESEARCH.</p></section>",
            "<section><h2>Current Blockers</h2>",
            render_table(["blocker"], [{"blocker": blocker} for blocker in blockers]),
            "</section>",
            "<section><h2>Qlib Backend</h2>",
            render_table(["field", "value"], mapping_rows(report.get("qlib_backend_section"))),
            "</section>",
            "<section><h2>Paper Autotrain Feedback Loop</h2>",
            render_table(["field", "value"], mapping_rows(report.get("paper_autotrain_section"))),
            "</section>",
            "<section><h2>Daily Learning Readiness</h2>",
            render_table(["field", "value"], mapping_rows(report.get("daily_learning_readiness_section"))),
            "</section>",
            "<section><h2>AI/Qlib Drift &amp; Regime</h2>",
            render_table(["field", "value"], mapping_rows(report.get("ai_qlib_drift_regime_section"))),
            "</section>",
            "<section><h2>Safety Invariants</h2>",
            render_table(["flag", "value"], [{"flag": key, "value": value} for key, value in sorted(flags.items())]),
            "</section>",
            "<section><h2>Allowed Next Steps</h2><p>Human review and separate branch for operational changes.</p></section>",
            "<section><h2>Forbidden Actions</h2><p>No scheduler, model promotion, registry write, runtime update, orders, or private exchange access.</p></section>",
            "</body></html>",
        ]
    )


def render_table(headers: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(row.get(header))}</td>" for header in headers)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def mapping_rows(value: Any) -> list[dict[str, Any]]:
    mapping = mapping_or_empty(value)
    return [
        {"field": key, "value": json.dumps(field_value, ensure_ascii=False, sort_keys=True, default=str)}
        for key, field_value in sorted(mapping.items())
        if key not in {"blockers", "warnings"}
    ]


def markdown_mapping(value: Any) -> str:
    mapping = mapping_or_empty(value)
    if not mapping:
        return "- none"
    return "\n".join(f"- `{key}`: `{json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)}`" for key, item in sorted(mapping.items()))


def markdown_list(value: Any) -> list[str]:
    items = list_of_strings(value)
    if not items:
        return ["- none"]
    return [f"- `{item}`" for item in items]


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def list_of_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if str(item)]
    return []


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
