"""Build the canonical auto-learning closeout evidence report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.outcome_schema import utc_now_iso

from .evidence_loader import LoadedEvidence, load_evidence_sources, stable_json
from .lineage_validator import validate_lineage
from .safety_validator import closeout_safety_flags, validate_safety

SCHEMA_VERSION = "autolearning_canonical_loop_closeout_evidence_v1"
DEFAULT_REPORT_JSON = Path("data/reports/autolearning_canonical_loop_closeout_evidence_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/autolearning_canonical_loop_closeout_evidence_v1.md")
DEFAULT_LINEAGE_JSON = Path("data/reports/autolearning_canonical_loop_lineage_matrix_v1.json")
DEFAULT_SAFETY_JSON = Path("data/reports/autolearning_canonical_loop_safety_matrix_v1.json")


def build_closeout_report(
    *,
    project_root: str | Path,
    write: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    lineage_matrix_path: str | Path | None = None,
    safety_matrix_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    loaded = load_evidence_sources(root)
    payloads = {item.source.stage_id: item.payload for item in loaded if item.exists and item.load_error is None}
    source_hashes = {item.source.stage_id: item.sha256 for item in loaded}
    missing = [item.source.relative_path for item in loaded if item.source.required and (not item.exists or item.load_error is not None)]
    lineage = validate_lineage(payloads, source_hashes) if not missing else blocked_lineage()
    safety = validate_safety(payloads) if not missing else {"safety_status": "blocked", "safety_matrix": [], "safety_violations": []}
    stage_matrix = build_stage_matrix(loaded, lineage["lineage_matrix"], safety["safety_matrix"])
    blocked_stage_count = sum(1 for row in stage_matrix if row["status"] == "blocked")
    warning_stage_count = sum(1 for row in stage_matrix if row["status"] == "warning")
    ready_stage_count = sum(1 for row in stage_matrix if row["status"] in ("ok", "warning"))
    validation_errors: list[str] = []
    if missing:
        validation_errors.append("missing_evidence_sources")
        decision = "BLOCKED_MISSING_EVIDENCE"
    elif lineage["lineage_drift_detected"]:
        validation_errors.append("lineage_drift_detected")
        decision = "BLOCKED_LINEAGE_DRIFT"
    elif safety["safety_status"] != "ok":
        validation_errors.append("safety_violation_detected")
        decision = "BLOCKED_SAFETY_VIOLATION"
    else:
        decision = "CANONICAL_RESEARCH_LOOP_CLOSED"
    canonical_status = "ok" if decision == "CANONICAL_RESEARCH_LOOP_CLOSED" else "blocked"
    reason = decision.lower()
    output_paths = {
        "report_json": str(resolve(root, report_json_path, DEFAULT_REPORT_JSON)),
        "report_markdown": str(resolve(root, report_markdown_path, DEFAULT_REPORT_MD)),
        "lineage_matrix_json": str(resolve(root, lineage_matrix_path, DEFAULT_LINEAGE_JSON)),
        "safety_matrix_json": str(resolve(root, safety_matrix_path, DEFAULT_SAFETY_JSON)),
    }
    ai_shadow_report = payloads.get("ai_shadow_trainer", {})
    ai_shadow_metrics = payloads.get("ai_shadow_metrics", {})
    qlib_backend = payloads.get("qlib_backend_gate", {})
    qlib_trainer = payloads.get("qlib_trainer", {})
    safety_flags = closeout_safety_flags()
    report: dict[str, Any] = {
        "status": canonical_status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "project_root": str(root),
        "evidence_sources": evidence_sources_payload(loaded),
        "missing_evidence_sources": missing,
        "stage_count": len(stage_matrix),
        "ready_stage_count": ready_stage_count,
        "blocked_stage_count": blocked_stage_count,
        "warning_stage_count": warning_stage_count,
        "stage_matrix": stage_matrix,
        "lineage_status": lineage["lineage_status"],
        "lineage_drift_detected": lineage["lineage_drift_detected"],
        "lineage_matrix": lineage["lineage_matrix"],
        "safety_status": safety["safety_status"],
        "safety_matrix": safety["safety_matrix"],
        "canonical_loop_status": canonical_status,
        "canonical_loop_decision": decision,
        "recommended_next_action": recommended_next_action(decision, warning_stage_count),
        "feature_contract_hash": lineage.get("feature_contract_hash"),
        "dataset_hash": lineage.get("dataset_hash"),
        "target_store_hash": lineage.get("target_store_hash"),
        "split_engine_hash": lineage.get("split_engine_hash"),
        "qlib_trainer_report_hash": lineage.get("qlib_trainer_report_hash"),
        "ai_shadow_trainer_report_hash": lineage.get("ai_shadow_trainer_report_hash"),
        "qlib_backend_status": qlib_backend.get("qlib_backend_status", qlib_backend.get("status")),
        "qlib_trainer_status": qlib_trainer.get("trainer_status", qlib_trainer.get("status")),
        "ai_shadow_trainer_status": ai_shadow_report.get("trainer_status", ai_shadow_report.get("status")),
        "ai_shadow_candidate_decision": ai_shadow_report.get("candidate_decision", ai_shadow_metrics.get("candidate_decision")),
        "ai_shadow_net_ev_delta_if_applied_research_only_total": read_nested_number(
            ai_shadow_report,
            ("aggregate_metrics", "net_ev_delta_if_applied_research_only_total"),
        ),
        "closeout_ready": canonical_status == "ok",
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": output_paths,
        "validation_errors": validation_errors,
        **safety_flags,
        "safety_flags": safety_flags,
    }
    if write:
        write_outputs(report, output_paths)
        report["write_performed"] = True
        write_json(Path(output_paths["report_json"]), report)
    return report


def blocked_lineage() -> dict[str, Any]:
    return {
        "lineage_status": "blocked",
        "lineage_drift_detected": True,
        "lineage_matrix": [],
        "feature_contract_hash": None,
        "dataset_hash": None,
        "target_store_hash": None,
        "split_engine_hash": None,
        "qlib_trainer_report_hash": None,
        "ai_shadow_trainer_report_hash": None,
    }


def build_stage_matrix(
    loaded: list[LoadedEvidence],
    lineage_matrix: list[dict[str, Any]],
    safety_matrix: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lineage_by_stage = group_status(lineage_matrix)
    safety_by_stage = group_status(safety_matrix)
    rows: list[dict[str, Any]] = []
    for item in loaded:
        payload = item.payload
        source_status, source_reason = source_stage_status(item)
        lineage_status = lineage_by_stage.get(item.source.stage_id, "ok")
        safety_status = safety_by_stage.get(item.source.stage_id, "ok")
        status = combine_status(source_status, lineage_status, safety_status)
        rows.append(
            {
                "stage_id": item.source.stage_id,
                "stage_name": item.source.stage_name,
                "source_path": item.source.relative_path,
                "source_exists": item.exists,
                "source_hash": item.sha256,
                "status": status,
                "reason": source_reason,
                "row_count": payload.get("row_count")
                or payload.get("selected_dataset_rows")
                or payload.get("microbatch_rows")
                or payload.get("accepted_rows"),
                "split_count": payload.get("split_count"),
                "lineage_status": lineage_status,
                "safety_status": safety_status,
                "operational_authority": False,
                "promotion_allowed": False,
            }
        )
    return rows


def source_stage_status(item: LoadedEvidence) -> tuple[str, str]:
    if not item.exists:
        return "blocked", "missing_evidence_source"
    if item.load_error is not None:
        return "blocked", item.load_error
    payload = item.payload
    raw_status = payload.get("trainer_status") or payload.get("validation_status") or payload.get("status") or "ok"
    reason = str(payload.get("reason") or raw_status)
    if item.source.stage_id in {"qlib_backend_gate", "qlib_trainer"} and "qlib_backend_unavailable" in reason:
        return "warning", reason
    if str(raw_status).lower() in {"ok", "valid", "validation_ok"}:
        return "ok", reason
    if str(raw_status).lower() in {"warning", "degraded"}:
        return "warning", reason
    return "blocked", reason


def group_status(rows: list[dict[str, Any]]) -> dict[str, str]:
    grouped: dict[str, str] = {}
    for row in rows:
        stage_id = str(row.get("stage_id"))
        current = grouped.get(stage_id, "ok")
        grouped[stage_id] = combine_status(current, str(row.get("status", "ok")))
    return grouped


def combine_status(*statuses: str) -> str:
    normalized = {status.lower() for status in statuses}
    if "blocked" in normalized:
        return "blocked"
    if "warning" in normalized:
        return "warning"
    return "ok"


def evidence_sources_payload(loaded: list[LoadedEvidence]) -> list[dict[str, Any]]:
    return [
        {
            "stage_id": item.source.stage_id,
            "stage_name": item.source.stage_name,
            "path": item.source.relative_path,
            "required": item.source.required,
            "exists": item.exists,
            "sha256": item.sha256,
            "load_error": item.load_error,
        }
        for item in loaded
    ]


def recommended_next_action(decision: str, warning_stage_count: int) -> str:
    if decision == "CANONICAL_RESEARCH_LOOP_CLOSED" and warning_stage_count:
        return "Keep research loop closed; resolve warning evidence before any operational design branch."
    if decision == "CANONICAL_RESEARCH_LOOP_CLOSED":
        return "Archive closeout evidence and keep all outputs research-only."
    return "Resolve blocked closeout evidence before any downstream research or operational discussion."


def read_nested_number(payload: dict[str, Any], path: tuple[str, str]) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        return round(float(current), 10)
    except (TypeError, ValueError):
        return None


def resolve(root: Path, path: str | Path | None, default: Path) -> Path:
    candidate = Path(path) if path is not None else default
    return candidate if candidate.is_absolute() else root / candidate


def write_outputs(report: dict[str, Any], output_paths: dict[str, str]) -> None:
    write_json(Path(output_paths["lineage_matrix_json"]), {"schema_version": "autolearning_canonical_loop_lineage_matrix_v1", "lineage_matrix": report["lineage_matrix"]})
    write_json(Path(output_paths["safety_matrix_json"]), {"schema_version": "autolearning_canonical_loop_safety_matrix_v1", "safety_matrix": report["safety_matrix"]})
    write_text(Path(output_paths["report_markdown"]), markdown_report(report))
    write_json(Path(output_paths["report_json"]), report)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Auto-learning canonical loop closeout evidence V1",
            "",
            f"- Status: `{report['canonical_loop_status']}`",
            f"- Decision: `{report['canonical_loop_decision']}`",
            f"- Lineage: `{report['lineage_status']}`",
            f"- Safety: `{report['safety_status']}`",
            f"- Stages: `{report['stage_count']}`",
            f"- Warnings: `{report['warning_stage_count']}`",
            f"- Blocked: `{report['blocked_stage_count']}`",
            f"- Recommended next action: {report['recommended_next_action']}",
            "",
            "This report is research evidence only. It does not authorize promotion, runtime vetoes, registry writes, live trading, canary release, order submission, or private exchange access.",
            "",
        ]
    )
