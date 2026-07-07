"""Research-only registry gate for paper model candidates.

The gate consolidates candidate evidence from Qlib, IA Shadow, ensemble
threshold calibration, and paper autotrain reports. It does not publish,
promote, apply thresholds, write registries, update runtime, or touch trading
components. Domain functions are pure/in-memory; only the CLI may write reports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_model_candidate_registry_gate_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_REPORT_JSON = Path("data/reports/paper_model_candidate_registry_gate_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_model_candidate_registry_gate_v1.md")

SOURCE_SPECS: tuple[tuple[str, Path, bool], ...] = (
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json"), False),
    ("ai_shadow_quality_veto", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"), False),
    (
        "ensemble_threshold_calibration",
        Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json"),
        False,
    ),
    ("feature_source_contract", Path("data/reports/ai_feature_source_fields_enrichment_contract_v1.json"), False),
    ("target_store", Path("data/reports/financial_label_target_store_v1.json"), False),
    ("drift_monitor", Path("data/reports/ai_qlib_drift_regime_monitor_v1.json"), False),
    ("execution_cost_gate", Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json"), False),
    ("paper_autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json"), False),
)

CANDIDATE_SOURCE_IDS = (
    "qlib_trainer",
    "ai_shadow_quality_veto",
    "ensemble_threshold_calibration",
    "paper_autotrain_feedback_loop",
)

EXPECTED_CANDIDATE_TYPES = {
    "qlib_trainer": "qlib_model_candidate",
    "ai_shadow_quality_veto": "ai_shadow_quality_veto_candidate",
    "ensemble_threshold_calibration": "ensemble_threshold_candidate",
    "paper_autotrain_feedback_loop": "paper_autotrain_candidate",
}

PASSING_SOURCE_STATUSES = {"ok", "warning"}
BLOCKING_SOURCE_STATUSES = {"blocked", "missing", "invalid"}
RUNTIME_UNSAFE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "sends_orders",
    "exchange_private_access",
    "qlib_runtime_updated",
    "ai_shadow_runtime_updated",
    "updates_ai_shadow_thresholds",
    "updates_qlib_runtime",
    "updates_freqtrade",
    "updates_risk_manager",
    "changes_risk",
    "changes_model",
    "writes_runtime",
    "writes_registry",
    "writes_sqlite",
    "writes_parquet",
)
RELEASE_UNSAFE_FLAGS = (
    "release_allowed",
    "live_release_allowed",
    "canary_release_allowed",
    "promotion_eligible",
    "model_promotion_performed",
    "registry_write_performed",
    "model_registry_write_performed",
    "runtime_registry_write_performed",
    "candidate_registry_write_performed",
)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


def build_paper_model_candidate_registry_gate_v1(
    *,
    project_root: str | Path,
    evidence_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the registry gate report in memory."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    if evidence_payloads is None:
        sources = load_sources(root)
        payloads = {source.source_id: source.payload for source in sources if source.payload}
    else:
        payloads = {str(key): dict(value) for key, value in evidence_payloads.items()}
        sources = sources_from_payloads(root, payloads)

    candidates = build_candidates(sources, payloads)
    eligible_count = sum(1 for candidate in candidates if bool(candidate["eligible_for_research_review"]))
    blocked_count = len(candidates) - eligible_count
    source_blockers = input_source_blockers(sources)
    candidate_blockers = sorted(
        {
            reason
            for candidate in candidates
            for reason in list_of_strings(candidate.get("blocked_reasons"))
        }
    )
    blockers = sorted(set(source_blockers + candidate_blockers))
    warnings = build_warnings(sources, candidates)
    registry_gate_status = decide_registry_gate_status(candidates, source_blockers, eligible_count, warnings)
    status, reason = decide_status(registry_gate_status, warnings)
    safety = safety_flags()
    output_json = resolve(root, output_json_path, DEFAULT_REPORT_JSON)
    output_md = resolve(root, output_markdown_path, DEFAULT_REPORT_MD)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "registry_gate_status": registry_gate_status,
        "input_sources": [source.public_record() for source in sources],
        "lineage_hashes": build_lineage_hashes(payloads),
        "candidate_count": len(candidates),
        "eligible_candidate_count": eligible_count,
        "blocked_candidate_count": blocked_count,
        "candidates": candidates,
        "blockers": blockers,
        "warnings": warnings,
        "non_goals": build_non_goals(),
        "output_paths": {"json": str(output_json), "markdown": str(output_md)},
        "write_requested": bool(write),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }
    return report


def load_sources(project_root: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in SOURCE_SPECS:
        path = project_root / relative_path
        exists = path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None
        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                load_error = f"invalid_json:{exc.__class__.__name__}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )
    return records


def sources_from_payloads(project_root: Path, payloads: Mapping[str, Mapping[str, Any]]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in SOURCE_SPECS:
        payload = dict(payloads.get(source_id, {}))
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=(project_root / relative_path).resolve(),
                required=required,
                exists=bool(payload),
                sha256=stable_payload_hash(payload) if payload else None,
                load_error=None,
                payload=payload,
            )
        )
    return records


def build_candidates(
    sources: Sequence[SourceRecord],
    payloads: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_map = {source.source_id: source for source in sources}
    gate_context = build_gate_context(payloads)
    candidates: list[dict[str, Any]] = []
    for source_id in CANDIDATE_SOURCE_IDS:
        source = source_map[source_id]
        payload = payloads.get(source_id, {})
        candidate = candidate_from_source(source, payload, gate_context)
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: str(item["candidate_id"]))


def build_gate_context(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    drift_payload = payloads.get("drift_monitor", {})
    cost_payload = payloads.get("execution_cost_gate", {})
    source_contract_payload = payloads.get("feature_source_contract", {})
    target_store_payload = payloads.get("target_store", {})
    return {
        "drift_gate_passed": gate_source_passed(drift_payload),
        "drift_gate_missing": not bool(drift_payload),
        "execution_cost_gate_passed": gate_source_passed(cost_payload),
        "execution_cost_gate_missing": not bool(cost_payload),
        "source_contract_ready": source_contract_ready(source_contract_payload),
        "source_contract_missing": not bool(source_contract_payload),
        "target_store_available": bool(target_store_payload),
    }


def candidate_from_source(
    source: SourceRecord,
    payload: Mapping[str, Any],
    gate_context: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_type = EXPECTED_CANDIDATE_TYPES[source.source_id]
    blocked_reasons: list[str] = []
    evidence_status = evidence_status_for_source(source, payload)
    if evidence_status != "available":
        blocked_reasons.append("blocked_missing_evidence")
    if bool(payload):
        blocked_reasons.extend(source_level_blockers(payload))
    blocked_reasons.extend(context_blockers(gate_context, candidate_type))
    gate_status = first_candidate_gate_status(blocked_reasons)
    eligible_for_research_review = gate_status == "eligible_for_research_review"
    metric_summary = metric_summary_from_payload(payload)
    threshold = extract_threshold(payload)
    identity_parts = [
        candidate_type,
        source.source_id,
        str(source.sha256 or stable_payload_hash(payload)),
        str(threshold),
    ]
    return {
        "candidate_id": deterministic_id(identity_parts),
        "candidate_type": candidate_type,
        "source_id": source.source_id,
        "source_path": source.relative_path,
        "source_sha256": source.sha256,
        "model_family": extract_model_family(payload, candidate_type),
        "strategy_family": extract_strategy_family(payload, candidate_type),
        "symbol_scope": extract_scope(payload, ("symbol_scope", "symbols", "target_symbols")),
        "side_scope": extract_scope(payload, ("side_scope", "sides", "target_sides")),
        "regime_scope": extract_scope(payload, ("regime_scope", "regimes", "target_regimes")),
        "threshold": threshold,
        "score_metric_summary": metric_summary,
        "evidence_status": evidence_status,
        "gate_status": gate_status,
        "blocked_reasons": sorted_unique(blocked_reasons),
        "eligible_for_research_review": eligible_for_research_review,
        "eligible_for_runtime": False,
        "promotes_model": False,
        "applies_thresholds": False,
        "writes_registry": False,
        "updates_runtime": False,
    }


def evidence_status_for_source(source: SourceRecord, payload: Mapping[str, Any]) -> str:
    if not source.exists:
        return "missing"
    if source.load_error is not None:
        return "invalid"
    if not payload:
        return "missing"
    status = str(payload.get("status") or payload.get("trainer_status") or "ok")
    if status in BLOCKING_SOURCE_STATUSES:
        return status
    return "available"


def source_level_blockers(payload: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for flag in RUNTIME_UNSAFE_FLAGS:
        if payload.get(flag) is True:
            blockers.append("blocked_runtime_authority")
            break
    for flag in RELEASE_UNSAFE_FLAGS:
        if payload.get(flag) is True:
            blockers.append("blocked_release_authority")
            break
    if payload.get("model_promotion_requested") is True or payload.get("registry_write_requested") is True:
        blockers.append("blocked_release_authority")
    return blockers


def context_blockers(gate_context: Mapping[str, Any], candidate_type: str) -> list[str]:
    blockers: list[str] = []
    if gate_context.get("drift_gate_missing"):
        blockers.append("blocked_missing_evidence")
    elif not gate_context.get("drift_gate_passed"):
        blockers.append("blocked_drift_gate")
    if gate_context.get("execution_cost_gate_missing"):
        blockers.append("blocked_missing_evidence")
    elif not gate_context.get("execution_cost_gate_passed"):
        blockers.append("blocked_execution_cost_gate")
    if gate_context.get("source_contract_missing"):
        blockers.append("blocked_missing_evidence")
    elif not gate_context.get("source_contract_ready"):
        blockers.append("blocked_source_contract_gate")
    if candidate_type in {"qlib_model_candidate", "ai_shadow_quality_veto_candidate"} and not gate_context.get(
        "target_store_available"
    ):
        blockers.append("blocked_missing_evidence")
    return blockers


def gate_source_passed(payload: Mapping[str, Any]) -> bool:
    if not payload:
        return False
    status = str(payload.get("status") or "").lower()
    if status == "blocked":
        return False
    if payload.get("blockers"):
        return False
    return True


def source_contract_ready(payload: Mapping[str, Any]) -> bool:
    if not payload:
        return False
    if payload.get("status") == "blocked":
        return False
    if payload.get("forbidden_fields_used"):
        return False
    return bool(payload.get("can_derive_feature_notional")) and bool(payload.get("can_derive_feature_quantity"))


def first_candidate_gate_status(blocked_reasons: Sequence[str]) -> str:
    if not blocked_reasons:
        return "eligible_for_research_review"
    priority = (
        "blocked_missing_evidence",
        "blocked_runtime_authority",
        "blocked_release_authority",
        "blocked_drift_gate",
        "blocked_execution_cost_gate",
        "blocked_source_contract_gate",
        "blocked_quality_gate",
    )
    reason_set = set(blocked_reasons)
    for reason in priority:
        if reason in reason_set:
            return reason
    return "blocked_quality_gate"


def decide_registry_gate_status(
    candidates: Sequence[Mapping[str, Any]],
    source_blockers: Sequence[str],
    eligible_count: int,
    warnings: Sequence[str],
) -> str:
    if source_blockers:
        return "blocked_missing_evidence"
    if not candidates or eligible_count == 0:
        return "blocked_no_eligible_candidates"
    if warnings:
        return "warning_review_required"
    return "ok_research_review_only"


def decide_status(registry_gate_status: str, warnings: Sequence[str]) -> tuple[str, str]:
    if registry_gate_status.startswith("blocked"):
        return "blocked", registry_gate_status
    if registry_gate_status.startswith("warning") or warnings:
        return "warning", registry_gate_status
    return "ok", registry_gate_status


def input_source_blockers(sources: Sequence[SourceRecord]) -> list[str]:
    blockers: list[str] = []
    for source in sources:
        if source.required and (not source.exists or source.load_error):
            blockers.append(f"missing_required_source:{source.relative_path}")
    return blockers


def build_warnings(sources: Sequence[SourceRecord], candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    del candidates
    warnings = [
        f"missing_relevant_source:{source.relative_path}"
        for source in sources
        if not source.required and (not source.exists or source.load_error)
    ]
    return sorted_unique(warnings)


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for payload in payloads.values():
        for key in (
            "dataset_hash",
            "feature_contract_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
            "dependency_contract_hash",
        ):
            if payload.get(key):
                output[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, Mapping):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def metric_summary_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for source_key in ("aggregate_metrics", "baseline_comparison", "gate_summary", "recommended_candidate"):
        value = payload.get(source_key)
        if isinstance(value, Mapping):
            summary[source_key] = compact_metric_map(value)
    for key in (
        "trainer_status",
        "feature_column_count",
        "split_count",
        "evaluated_split_count",
        "calibration_row_count",
        "promotion_eligible",
        "lineage_drift_detected",
        "research_candidate_cost_gate_passed",
    ):
        if key in payload:
            summary[key] = payload[key]
    return summary


def compact_metric_map(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed_scalar_keys = (
        "accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "rank_ic_mean",
        "precision_at_k",
        "expected_value",
        "threshold",
        "selected_count",
        "average_expected_value",
        "pnl_selected",
        "net_pnl_delta",
        "profit_factor",
    )
    output: dict[str, Any] = {}
    for key in allowed_scalar_keys:
        if key in value and not isinstance(value[key], (dict, list)):
            output[key] = value[key]
    return output


def extract_threshold(payload: Mapping[str, Any]) -> Any:
    for key in ("threshold", "ai_threshold", "recommended_threshold"):
        if key in payload:
            return payload[key]
    candidate = payload.get("recommended_candidate")
    if isinstance(candidate, Mapping):
        return candidate.get("threshold")
    return None


def extract_model_family(payload: Mapping[str, Any], candidate_type: str) -> str:
    for key in ("model_family", "backend_name", "model_name", "ai_model_name"):
        value = payload.get(key)
        if value:
            return str(value)
    if candidate_type == "qlib_model_candidate":
        return "qlib"
    if candidate_type == "ai_shadow_quality_veto_candidate":
        return "ai_shadow"
    return "research"


def extract_strategy_family(payload: Mapping[str, Any], candidate_type: str) -> str:
    value = payload.get("strategy_family") or payload.get("strategy_id")
    return str(value) if value else candidate_type


def extract_scope(payload: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return sorted_unique(value)
        if isinstance(value, str) and value:
            return [value]
    return []


def build_non_goals() -> list[str]:
    return [
        "No active model promotion",
        "No active model registry write",
        "No candidate registry write",
        "No runtime threshold update",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No Freqtrade or RiskManager changes",
        "No signal producer changes",
        "No order submission",
        "No private exchange access",
        "No SQLite, parquet, model artifact, or runtime writes",
    ]


def safety_flags() -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "read_only": True,
        "active_model_changed": False,
        "model_promotion_performed": False,
        "promotes_model": False,
        "runs_training": False,
        "registry_write_performed": False,
        "model_registry_write_performed": False,
        "runtime_registry_write_performed": False,
        "candidate_registry_write_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
        "updates_qlib_runtime": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_feature_contract": False,
        "changes_dataset_manifest": False,
        "writes_runtime": False,
        "writes_registry": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_model_artifact": False,
        "operational_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Model Candidate Registry Gate V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Registry gate status: `{report.get('registry_gate_status')}`",
            f"- Candidate count: `{report.get('candidate_count')}`",
            f"- Eligible for research review: `{report.get('eligible_candidate_count')}`",
            f"- Blocked candidates: `{report.get('blocked_candidate_count')}`",
            "",
            "## Candidates",
            "",
            *markdown_candidates(report.get("candidates", [])),
            "",
            "## Blockers",
            "",
            *markdown_list(report.get("blockers", [])),
            "",
            "## Warnings",
            "",
            *markdown_list(report.get("warnings", [])),
            "",
            "## Safety Invariants",
            "",
            "- `decision=MANTER_EM_RESEARCH`",
            "- `release_allowed=false`",
            "- `eligible_for_runtime=false` for every candidate",
            "- `registry_write_performed=false`",
            "- `candidate_registry_write_performed=false`",
            "- `model_promotion_performed=false`",
            "- `updates_qlib_runtime=false`",
            "- `updates_ai_shadow_thresholds=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "",
            "## Non-Goals",
            "",
            *[f"- {item}" for item in list_of_strings(report.get("non_goals"))],
            "",
        ]
    )


def markdown_candidates(candidates: Any) -> list[str]:
    rows = list_of_mappings(candidates)
    if not rows:
        return ["- No candidates discovered."]
    return [
        (
            f"- `{row.get('candidate_id')}`: type=`{row.get('candidate_type')}`, "
            f"gate_status=`{row.get('gate_status')}`, "
            f"eligible_for_research_review=`{row.get('eligible_for_research_review')}`, "
            f"eligible_for_runtime=`{row.get('eligible_for_runtime')}`"
        )
        for row in rows
    ]


def markdown_list(value: Any) -> list[str]:
    rows = list_of_strings(value)
    if not rows:
        return ["- None"]
    return [f"- `{row}`" for row in rows]


def write_reports(report: Mapping[str, Any], output_json: Path, output_markdown: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_json, report)
    output_markdown.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def deterministic_id(parts: Sequence[Any]) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"candidate_{digest[:16]}"


def stable_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=json_safe).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
