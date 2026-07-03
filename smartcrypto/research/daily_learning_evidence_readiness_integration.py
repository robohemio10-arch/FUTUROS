"""Daily Learning evidence/readiness integration for SMART FUTUROS.

This module converts Daily Learning research payloads into an informative
readiness-evidence snapshot. It deliberately does not execute schedulers,
orchestrators, stage builders, training, runtime updates, model promotion, rule
promotion, risk changes, Freqtrade changes, or order submission. The output is a
blocked readiness summary suitable for governance review only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_NAME = "SMART FUTUROS"
SCHEMA_VERSION = "daily_learning_evidence_readiness_integration_v1"
STATUS_BLOCKED = "blocked"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
FINAL_DECISION_BLOCKED = "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"

_EVIDENCE_SOURCE_ORDER: tuple[str, ...] = (
    "scheduler",
    "dashboard_command_center",
    "orchestrator",
    "qlib_research_dataset",
    "ai_shadow_feedback_bridge",
    "candidate_shadow_rule_registry",
    "shadow_rule_oos_validation",
    "paper_autotrain_feedback_loop_v1",
)

_SOURCE_LABELS: dict[str, str] = {
    "scheduler": "Daily Learning scheduler paper-only",
    "dashboard_command_center": "Dashboard Daily Learning Command Center",
    "orchestrator": "Daily Paper/Master Learning Loop orchestrator",
    "qlib_research_dataset": "Qlib research dataset",
    "ai_shadow_feedback_bridge": "AI Shadow feedback bridge",
    "candidate_shadow_rule_registry": "Candidate shadow rule registry",
    "shadow_rule_oos_validation": "Shadow rule OOS validation",
    "paper_autotrain_feedback_loop_v1": "Paper auto-train feedback loop V1",
}

_SAFE_SOURCE_DECISIONS: set[str | None] = {
    None,
    DECISION_RESEARCH,
    "BLOCKED",
    "BLOCKED_BACKEND_UNAVAILABLE",
}

_REQUIRED_FALSE_FLAGS: tuple[str, ...] = (
    "operational_authority",
    "readiness_release_authority",
    "daily_learning_can_release_canary",
    "daily_learning_can_release_live",
    "daily_learning_can_promote_model",
    "daily_learning_can_promote_rules",
    "daily_learning_can_apply_rules",
    "daily_learning_can_apply_feedback",
    "registers_scheduler",
    "creates_cron",
    "creates_systemd_timer",
    "creates_windows_task",
    "creates_service",
    "executes_scheduler",
    "executes_orchestrator",
    "executes_stage_builders",
    "runs_training",
    "updates_qlib_runtime",
    "updates_ai_shadow_runtime",
    "updates_ai_shadow_thresholds",
    "updates_ai_shadow_policy",
    "updates_freqtrade",
    "updates_risk_manager",
    "updates_models",
    "applies_shadow_rules",
    "applies_feedback_to_ai_shadow",
    "promotes_shadow_rules",
    "can_promote_model",
    "can_promote_rules",
    "changes_model",
    "changes_risk",
    "exchange_private_access",
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "sends_orders",
    "writes_data",
    "writes_runtime",
    "writes_reports",
    "writes_sqlite",
    "writes_parquet",
    "writes_ai_shadow_sqlite",
)

_REQUIRED_TRUE_FLAGS: tuple[str, ...] = (
    "research_only",
    "read_only",
    "paper_only",
    "shadow_only",
    "daily_learning_evidence_is_informational",
    "readiness_snapshot_blocked",
)

_FORBIDDEN_SOURCE_TRUE_FLAGS: tuple[str, ...] = (
    "operational_authority",
    "write_performed",
    "registers_scheduler",
    "creates_cron",
    "creates_systemd_timer",
    "creates_windows_task",
    "creates_service",
    "executes_orchestrator",
    "executes_stage_builders",
    "runs_training",
    "updates_qlib_runtime",
    "updates_ai_shadow_runtime",
    "updates_freqtrade",
    "updates_risk_manager",
    "applies_shadow_rules",
    "applies_feedback_to_ai_shadow",
    "can_promote_model",
    "can_promote_rules",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "exchange_private_access",
)


@dataclass(frozen=True)
class EvidenceSourceDigest:
    """Compact, deterministic summary of one Daily Learning evidence source."""

    source_id: str
    source_name: str
    status: str
    decision: str
    schema_version: str | None
    input_mode: str
    reason: str
    row_count: int
    write_performed: bool
    research_only: bool
    read_only: bool
    paper_only: bool
    shadow_only: bool
    operational_authority: bool
    informational_only: bool
    release_authority: bool
    safe_for_readiness: bool
    validation_errors: tuple[str, ...]
    payload_provided: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "status": self.status,
            "decision": self.decision,
            "schema_version": self.schema_version,
            "input_mode": self.input_mode,
            "reason": self.reason,
            "row_count": self.row_count,
            "write_performed": self.write_performed,
            "research_only": self.research_only,
            "read_only": self.read_only,
            "paper_only": self.paper_only,
            "shadow_only": self.shadow_only,
            "operational_authority": self.operational_authority,
            "informational_only": self.informational_only,
            "release_authority": self.release_authority,
            "safe_for_readiness": self.safe_for_readiness,
            "validation_errors": list(self.validation_errors),
            "payload_provided": self.payload_provided,
        }


def _as_mapping(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return payload


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "sim"}:
            return True
        if lowered in {"false", "0", "no", "n", "nao", "não"}:
            return False
    return bool(value)


def _to_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str], default: Any) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _extract_nested(mapping: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _collect_validation_errors(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    raw_errors = mapping.get("validation_errors", [])
    errors: list[str] = []
    if isinstance(raw_errors, str):
        if raw_errors.strip():
            errors.append(raw_errors.strip())
    elif isinstance(raw_errors, Sequence):
        for item in raw_errors:
            if isinstance(item, str) and item.strip():
                errors.append(item.strip())
            elif item:
                errors.append(str(item))
    return tuple(sorted(set(errors)))


def _collect_string_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))
    return ()


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _infer_row_count(source_id: str, payload: Mapping[str, Any]) -> int:
    if source_id == "paper_autotrain_feedback_loop_v1":
        input_sources = payload.get("input_sources")
        if isinstance(input_sources, Sequence) and not isinstance(input_sources, (str, bytes)):
            return len(input_sources)

    direct = _first_present(
        payload,
        (
            "row_count",
            "dataset_row_count",
            "feedback_event_count",
            "candidate_rule_count",
            "oos_validation_result_count",
            "source_count",
            "stage_count",
            "total_reported_rows",
        ),
        None,
    )
    if direct is not None:
        return max(0, _to_int(direct))

    nested_paths: tuple[tuple[str, ...], ...] = (
        ("stage_summary", "total_reported_rows"),
        ("source_summary", "total_reported_rows"),
        ("dataset_summary", "row_count"),
        ("qlib_research_dataset", "dataset_row_count"),
        ("feedback_summary", "feedback_event_count"),
        ("run_plan_summary", "step_count"),
        ("daily_learning_scheduler", "run_plan", "step_count"),
    )
    if source_id == "scheduler":
        nested_paths = (
            ("run_plan_summary", "step_count"),
            ("daily_learning_scheduler", "run_plan", "step_count"),
        ) + nested_paths
    if source_id == "dashboard_command_center":
        nested_paths = (("source_summary", "source_count"),) + nested_paths

    for path in nested_paths:
        value = _extract_nested(payload, path)
        if value is not None:
            return max(0, _to_int(value))
    return 0


def _source_has_release_authority(payload: Mapping[str, Any]) -> bool:
    for key in (
        "readiness_release_authority",
        "daily_learning_can_release_canary",
        "daily_learning_can_release_live",
        "live_release_allowed",
        "canary_release_allowed",
        "model_promotion_allowed",
        "shadow_rule_promotion_allowed",
        "scheduler_registration_allowed",
    ):
        if _to_bool(payload.get(key), default=False):
            return True
    operator_decision = payload.get("operator_decision")
    if isinstance(operator_decision, Mapping):
        for key in (
            "live_release_allowed",
            "canary_release_allowed",
            "model_promotion_allowed",
            "shadow_rule_promotion_allowed",
            "scheduler_registration_allowed",
            "daily_learning_loop_operational_application_allowed",
        ):
            if _to_bool(operator_decision.get(key), default=False):
                return True
    return False


def _source_is_safe(payload: Mapping[str, Any]) -> bool:
    if not payload:
        return True
    if payload.get("decision") not in _SAFE_SOURCE_DECISIONS:
        return False
    if payload.get("status") not in {None, "ok", STATUS_BLOCKED, "not_loaded", "not_executed", "warning"}:
        return False
    if not _to_bool(payload.get("research_only"), default=True):
        return False
    if not _to_bool(payload.get("read_only"), default=True):
        return False
    if _source_has_release_authority(payload):
        return False
    for key in _FORBIDDEN_SOURCE_TRUE_FLAGS:
        if _to_bool(payload.get(key), default=False):
            return False
    safety_flags = payload.get("safety_flags")
    if isinstance(safety_flags, Mapping):
        for key in _FORBIDDEN_SOURCE_TRUE_FLAGS:
            if _to_bool(safety_flags.get(key), default=False):
                return False
    return True


def _build_paper_autotrain_feedback_loop_section(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    mapping = _as_mapping(payload)
    safety_flags = {
        "research_only": _to_bool(mapping.get("research_only"), default=True),
        "read_only": _to_bool(mapping.get("read_only"), default=True),
        "paper_only": _to_bool(mapping.get("paper_only"), default=True),
        "shadow_only": _to_bool(mapping.get("shadow_only"), default=True),
        "operational_authority": _to_bool(mapping.get("operational_authority"), default=False),
        "live_release_allowed": _to_bool(mapping.get("live_release_allowed"), default=False),
        "canary_release_allowed": _to_bool(mapping.get("canary_release_allowed"), default=False),
        "order_submission_enabled": _to_bool(mapping.get("order_submission_enabled"), default=False),
        "real_order_submission_enabled": _to_bool(mapping.get("real_order_submission_enabled"), default=False),
        "sends_orders": _to_bool(mapping.get("sends_orders"), default=False),
        "exchange_private_access": _to_bool(mapping.get("exchange_private_access"), default=False),
        "changes_risk": _to_bool(mapping.get("changes_risk"), default=False),
        "changes_model": _to_bool(mapping.get("changes_model"), default=False),
        "promotion_eligible": _to_bool(mapping.get("promotion_eligible"), default=False),
        "registry_write_performed": _to_bool(mapping.get("registry_write_performed"), default=False),
        "model_promotion_performed": _to_bool(mapping.get("model_promotion_performed"), default=False),
        "qlib_runtime_updated": _to_bool(mapping.get("qlib_runtime_updated"), default=False),
        "ai_shadow_runtime_updated": _to_bool(mapping.get("ai_shadow_runtime_updated"), default=False),
        "writes_runtime": _to_bool(mapping.get("writes_runtime"), default=False),
        "writes_sqlite": _to_bool(mapping.get("writes_sqlite"), default=False),
        "writes_parquet": _to_bool(mapping.get("writes_parquet"), default=False),
    }
    nested_safety = mapping.get("safety_flags")
    if isinstance(nested_safety, Mapping):
        for key in safety_flags:
            safety_flags[key] = _to_bool(nested_safety.get(key), default=safety_flags[key])

    lineage_hashes = dict(_mapping_or_empty(mapping.get("lineage_hashes")))
    source_hashes = {
        key: value
        for key, value in {
            "report_sha256": mapping.get("report_sha256"),
            "source_sha256": mapping.get("source_sha256"),
            **lineage_hashes,
        }.items()
        if value is not None
    }
    return {
        "source_id": "paper_autotrain_feedback_loop_v1",
        "status": str(mapping.get("status", "not_loaded")) if mapping else "not_loaded",
        "decision": str(mapping.get("decision", DECISION_RESEARCH)) if mapping else DECISION_RESEARCH,
        "reason": str(mapping.get("reason", "source_payload_not_loaded")) if mapping else "source_payload_not_loaded",
        "schema_version": mapping.get("schema_version"),
        "payload_loaded": bool(mapping),
        "blockers": list(_collect_string_list(mapping.get("blockers"))),
        "warnings": list(_collect_string_list(mapping.get("warnings"))),
        "hashes": source_hashes,
        "lineage_hashes": lineage_hashes,
        "safety_flags": safety_flags,
        "write_performed": _to_bool(mapping.get("write_performed"), default=False),
        "source_report_write_performed": _to_bool(mapping.get("source_report_write_performed"), default=False),
        "run_qlib_train_requested": _to_bool(mapping.get("run_qlib_train_requested"), default=False),
        "run_ai_shadow_train_requested": _to_bool(mapping.get("run_ai_shadow_train_requested"), default=False),
        "source_report_run_qlib_train_requested": _to_bool(
            mapping.get("source_report_run_qlib_train_requested"),
            default=False,
        ),
        "source_report_run_ai_shadow_train_requested": _to_bool(
            mapping.get("source_report_run_ai_shadow_train_requested"),
            default=False,
        ),
        "safe_for_readiness": _source_is_safe(mapping),
    }


def _build_source_digest(source_id: str, payload: Mapping[str, Any] | None) -> EvidenceSourceDigest:
    mapping = _as_mapping(payload)
    payload_provided = bool(mapping)
    if not payload_provided:
        return EvidenceSourceDigest(
            source_id=source_id,
            source_name=_SOURCE_LABELS[source_id],
            status="not_loaded",
            decision=DECISION_RESEARCH,
            schema_version=None,
            input_mode="no_runtime_rows_loaded",
            reason="source_payload_not_loaded",
            row_count=0,
            write_performed=False,
            research_only=True,
            read_only=True,
            paper_only=True,
            shadow_only=True,
            operational_authority=False,
            informational_only=True,
            release_authority=False,
            safe_for_readiness=True,
            validation_errors=(),
            payload_provided=False,
        )

    safe = _source_is_safe(mapping)
    release_authority = _source_has_release_authority(mapping)
    return EvidenceSourceDigest(
        source_id=source_id,
        source_name=_SOURCE_LABELS[source_id],
        status=str(mapping.get("status", STATUS_BLOCKED)),
        decision=str(mapping.get("decision", DECISION_RESEARCH)),
        schema_version=str(mapping["schema_version"]) if mapping.get("schema_version") else None,
        input_mode=str(mapping.get("input_mode", "in_memory_payload_loaded")),
        reason=str(mapping.get("reason", "source_payload_loaded")),
        row_count=_infer_row_count(source_id, mapping),
        write_performed=_to_bool(mapping.get("write_performed"), default=False),
        research_only=_to_bool(mapping.get("research_only"), default=True),
        read_only=_to_bool(mapping.get("read_only"), default=True),
        paper_only=_to_bool(mapping.get("paper_only"), default=True),
        shadow_only=_to_bool(mapping.get("shadow_only"), default=True),
        operational_authority=_to_bool(mapping.get("operational_authority"), default=False),
        informational_only=not release_authority,
        release_authority=release_authority,
        safe_for_readiness=safe,
        validation_errors=_collect_validation_errors(mapping),
        payload_provided=True,
    )


def _summarize_sources(digests: Sequence[EvidenceSourceDigest]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    input_mode_counts: dict[str, int] = {}
    unsafe_sources: list[str] = []
    payload_loaded_count = 0
    total_rows = 0

    for digest in digests:
        status_counts[digest.status] = status_counts.get(digest.status, 0) + 1
        decision_counts[digest.decision] = decision_counts.get(digest.decision, 0) + 1
        input_mode_counts[digest.input_mode] = input_mode_counts.get(digest.input_mode, 0) + 1
        total_rows += digest.row_count
        if digest.payload_provided:
            payload_loaded_count += 1
        if not digest.safe_for_readiness:
            unsafe_sources.append(digest.source_id)

    return {
        "source_count": len(digests),
        "payload_loaded_count": payload_loaded_count,
        "safe_source_count": len(digests) - len(unsafe_sources),
        "unsafe_source_count": len(unsafe_sources),
        "unsafe_sources": sorted(unsafe_sources),
        "total_reported_rows": total_rows,
        "status_counts": dict(sorted(status_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "input_mode_counts": dict(sorted(input_mode_counts.items())),
    }


def _build_gate_matrix(source_summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    unsafe_count = _to_int(source_summary.get("unsafe_source_count"))
    payload_loaded_count = _to_int(source_summary.get("payload_loaded_count"))
    return [
        {
            "gate_id": "daily_learning_informational_only",
            "gate_name": "Daily Learning evidence is informational only",
            "severity": "critical",
            "passed": True,
            "evidence": "daily_learning_evidence_is_informational=true; readiness_release_authority=false",
        },
        {
            "gate_id": "readiness_stays_blocked",
            "gate_name": "Readiness remains blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "readiness_snapshot_blocked=true; final decision remains MANTER_EM_RESEARCH",
        },
        {
            "gate_id": "live_canary_blocked",
            "gate_name": "Live and canary release remain blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "live_release_allowed=false; canary_release_allowed=false",
        },
        {
            "gate_id": "no_runtime_execution",
            "gate_name": "No scheduler/orchestrator/stage execution",
            "severity": "critical",
            "passed": True,
            "evidence": "executes_scheduler=false; executes_orchestrator=false; executes_stage_builders=false",
        },
        {
            "gate_id": "no_promotion_or_application",
            "gate_name": "No model/rule promotion or feedback application",
            "severity": "critical",
            "passed": True,
            "evidence": "can_promote_model=false; can_promote_rules=false; applies_shadow_rules=false; applies_feedback_to_ai_shadow=false",
        },
        {
            "gate_id": "source_payload_safety",
            "gate_name": "Loaded Daily Learning payloads are safe for readiness",
            "severity": "high",
            "passed": unsafe_count == 0,
            "evidence": f"unsafe_source_count={unsafe_count}",
        },
        {
            "gate_id": "source_payload_presence",
            "gate_name": "Daily Learning source payload presence",
            "severity": "info",
            "passed": payload_loaded_count > 0,
            "evidence": "no-runtime mode is acceptable; loaded payloads only enrich evidence cards",
        },
    ]


def _summarize_gates(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed_gate_ids = [str(gate["gate_id"]) for gate in gates if not _to_bool(gate.get("passed"), default=False)]
    critical_gate_count = sum(1 for gate in gates if gate.get("severity") == "critical")
    critical_failed_ids = [
        str(gate["gate_id"])
        for gate in gates
        if gate.get("severity") == "critical" and not _to_bool(gate.get("passed"), default=False)
    ]
    high_failed_ids = [
        str(gate["gate_id"])
        for gate in gates
        if gate.get("severity") == "high" and not _to_bool(gate.get("passed"), default=False)
    ]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed_gate_ids),
        "failed_gate_count": len(failed_gate_ids),
        "failed_gate_ids": failed_gate_ids,
        "critical_gate_count": critical_gate_count,
        "critical_failed_gate_ids": critical_failed_ids,
        "high_failed_gate_ids": high_failed_ids,
    }


def build_daily_learning_evidence_readiness_integration_snapshot(
    *,
    project_root: str | Path | None = None,
    scheduler_payload: Mapping[str, Any] | None = None,
    dashboard_payload: Mapping[str, Any] | None = None,
    orchestrator_payload: Mapping[str, Any] | None = None,
    qlib_research_dataset_payload: Mapping[str, Any] | None = None,
    ai_shadow_feedback_bridge_payload: Mapping[str, Any] | None = None,
    candidate_shadow_rule_registry_payload: Mapping[str, Any] | None = None,
    shadow_rule_oos_validation_payload: Mapping[str, Any] | None = None,
    paper_autotrain_feedback_loop_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a blocked informative readiness snapshot for Daily Learning."""

    payload_by_source: dict[str, Mapping[str, Any] | None] = {
        "scheduler": scheduler_payload,
        "dashboard_command_center": dashboard_payload,
        "orchestrator": orchestrator_payload,
        "qlib_research_dataset": qlib_research_dataset_payload,
        "ai_shadow_feedback_bridge": ai_shadow_feedback_bridge_payload,
        "candidate_shadow_rule_registry": candidate_shadow_rule_registry_payload,
        "shadow_rule_oos_validation": shadow_rule_oos_validation_payload,
        "paper_autotrain_feedback_loop_v1": paper_autotrain_feedback_loop_payload,
    }
    digests = [_build_source_digest(source_id, payload_by_source[source_id]) for source_id in _EVIDENCE_SOURCE_ORDER]
    source_summary = _summarize_sources(digests)
    gate_matrix = _build_gate_matrix(source_summary)
    gate_summary = _summarize_gates(gate_matrix)
    loaded_count = _to_int(source_summary.get("payload_loaded_count"))
    unsafe_count = _to_int(source_summary.get("unsafe_source_count"))

    validation_errors: list[str] = []
    if unsafe_count:
        validation_errors.append("failed_readiness_gate_source_payload_safety")

    input_mode = "in_memory_payloads_loaded" if loaded_count else "no_runtime_rows_loaded"
    readiness_status = "blocked"
    evidence_role = "informational_non_releasing"

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root) if project_root is not None else ".",
        "status": STATUS_BLOCKED,
        "reason": "daily_learning_evidence_readiness_integration_blocked_informational_only",
        "decision": DECISION_RESEARCH,
        "input_mode": input_mode,
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "daily_learning_evidence_is_informational": True,
        "daily_learning_evidence_role": evidence_role,
        "readiness_status": readiness_status,
        "readiness_snapshot_blocked": True,
        "readiness_release_authority": False,
        "operational_authority": False,
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_ai_shadow_sqlite": False,
        "registers_scheduler": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "executes_scheduler": False,
        "executes_orchestrator": False,
        "executes_stage_builders": False,
        "runs_training": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "updates_ai_shadow_policy": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_models": False,
        "applies_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
        "promotes_shadow_rules": False,
        "can_promote_model": False,
        "can_promote_rules": False,
        "daily_learning_can_release_canary": False,
        "daily_learning_can_release_live": False,
        "daily_learning_can_promote_model": False,
        "daily_learning_can_promote_rules": False,
        "daily_learning_can_apply_rules": False,
        "daily_learning_can_apply_feedback": False,
        "changes_model": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "runs_ocr": False,
        "runs_ai_shadow_incremental": False,
        "uses_net_pnl_as_feature": False,
        "uses_outcome_as_feature": False,
        "evidence_scope": {
            "integrates_daily_learning_evidence": True,
            "daily_learning_evidence_is_informational": True,
            "updates_readiness_runtime": False,
            "releases_canary": False,
            "releases_live": False,
            "grants_operational_authority": False,
            "registers_scheduler": False,
            "executes_scheduler": False,
            "executes_orchestrator": False,
            "executes_stage_builders": False,
            "runs_training": False,
            "promotes_model": False,
            "promotes_rules": False,
            "applies_shadow_rules": False,
            "applies_feedback_to_ai_shadow": False,
            "writes_data": False,
            "writes_runtime": False,
            "writes_reports": False,
            "uses_only_in_memory_inputs": True,
        },
        "readiness_policy": {
            "daily_learning_evidence_is_not_release_evidence": True,
            "daily_learning_evidence_does_not_release_canary": True,
            "daily_learning_evidence_does_not_release_live": True,
            "daily_learning_evidence_does_not_promote_model": True,
            "daily_learning_evidence_does_not_promote_rules": True,
            "manual_go_no_go_required": True,
            "model_training_requires_separate_branch": True,
            "model_promotion_requires_separate_registry_and_oos_review": True,
            "candidate_rules_require_runtime_contract_binding": True,
            "real_scheduler_registration_requires_separate_branch": True,
            "thirty_day_gap_free_soak_required_for_future_canary_review": True,
        },
        "readiness_decision": {
            "final_decision": FINAL_DECISION_BLOCKED,
            "daily_learning_informational_evidence_accepted": True,
            "daily_learning_release_authority": False,
            "canary_release_allowed": False,
            "live_release_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "scheduler_registration_allowed": False,
            "orchestrator_execution_allowed": False,
            "stage_builder_execution_allowed": False,
            "training_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "qlib_runtime_update_allowed": False,
            "ai_shadow_runtime_update_allowed": False,
        },
        "source_digests": [digest.as_dict() for digest in digests],
        "paper_autotrain_feedback_loop_v1": _build_paper_autotrain_feedback_loop_section(
            paper_autotrain_feedback_loop_payload
        ),
        "source_cards": [
            {
                "card_id": digest.source_id,
                "title": digest.source_name,
                "status": digest.status,
                "decision": digest.decision,
                "input_mode": digest.input_mode,
                "row_count": digest.row_count,
                "safe_for_readiness": digest.safe_for_readiness,
                "payload_provided": digest.payload_provided,
                "primary_note": digest.reason,
            }
            for digest in digests
        ],
        "source_summary": source_summary,
        "gate_matrix": gate_matrix,
        "gate_summary": gate_summary,
        "readiness_summary": {
            "readiness_status": readiness_status,
            "daily_learning_evidence_role": evidence_role,
            "informational_source_count": _to_int(source_summary.get("source_count")),
            "payload_loaded_count": loaded_count,
            "unsafe_source_count": unsafe_count,
            "release_gate_count": 0,
            "release_allowed": False,
            "canary_release_allowed": False,
            "live_release_allowed": False,
        },
        "forbidden_actions": [
            "usar Daily Learning como release evidence",
            "liberar canary",
            "liberar live",
            "promover modelo",
            "promover regra candidata",
            "aplicar candidate rule",
            "aplicar feedback na IA Shadow",
            "registrar scheduler real",
            "executar scheduler",
            "executar orquestrador",
            "executar builders",
            "treinar modelo nesta branch",
            "alterar Qlib runtime",
            "alterar IA Shadow runtime",
            "alterar Freqtrade",
            "alterar RiskManager",
            "enviar ordem real",
            "usar exchange privada",
            "escrever artefatos em data/runtime/reports/logs/freqtrade",
        ],
        "allowed_next_steps": [
            "criar closeout handover do Daily Learning Loop em branch futura",
            "executar revisao manual dos payloads research-only fora de runtime",
            "materializar evidence snapshot somente com output explicito fora de runtime",
            "criar treinamento Qlib research-only em branch separada futura",
        ],
        "validation_errors": sorted(set(validation_errors)),
    }
    return snapshot


def build_daily_learning_evidence_readiness_view_model(
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact presentation model for read-only review surfaces."""

    payload = dict(snapshot or build_daily_learning_evidence_readiness_integration_snapshot())
    return {
        "title": "SMART FUTUROS — Daily Learning Evidence Readiness",
        "subtitle": "Informational evidence only. No release authority.",
        "status": payload.get("status", STATUS_BLOCKED),
        "decision": payload.get("decision", DECISION_RESEARCH),
        "readiness_status": payload.get("readiness_status", STATUS_BLOCKED),
        "input_mode": payload.get("input_mode", "no_runtime_rows_loaded"),
        "cards": list(payload.get("source_cards", [])),
        "gates": list(payload.get("gate_matrix", [])),
        "readiness_summary": dict(payload.get("readiness_summary", {})),
        "safety_footer": {
            "daily_learning_evidence_is_informational": payload.get("daily_learning_evidence_is_informational"),
            "readiness_release_authority": payload.get("readiness_release_authority"),
            "operational_authority": payload.get("operational_authority"),
            "canary_release_allowed": payload.get("canary_release_allowed"),
            "live_release_allowed": payload.get("live_release_allowed"),
            "order_submission_enabled": payload.get("order_submission_enabled"),
        },
    }


def validate_daily_learning_evidence_readiness_integration_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    """Validate hard safety invariants for a readiness integration snapshot."""

    errors: list[str] = []
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if snapshot.get("status") != STATUS_BLOCKED:
        errors.append("status_must_be_blocked")
    if snapshot.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_manter_em_research")
    if snapshot.get("readiness_status") != STATUS_BLOCKED:
        errors.append("readiness_status_must_be_blocked")
    for key in _REQUIRED_TRUE_FLAGS:
        if snapshot.get(key) is not True:
            errors.append(f"{key}_must_be_true")
    for key in _REQUIRED_FALSE_FLAGS:
        if snapshot.get(key) is not False:
            errors.append(f"{key}_must_be_false")

    gate_summary = snapshot.get("gate_summary")
    if isinstance(gate_summary, Mapping):
        critical_failed = gate_summary.get("critical_failed_gate_ids", [])
        high_failed = gate_summary.get("high_failed_gate_ids", [])
        if critical_failed:
            errors.append("critical_readiness_gates_failed")
        if high_failed:
            errors.append("high_readiness_gates_failed")

    source_summary = snapshot.get("source_summary")
    if isinstance(source_summary, Mapping) and _to_int(source_summary.get("unsafe_source_count")) > 0:
        errors.append("unsafe_daily_learning_sources_present")

    validation_errors = snapshot.get("validation_errors", [])
    if isinstance(validation_errors, Sequence) and not isinstance(validation_errors, (str, bytes)):
        errors.extend(str(error) for error in validation_errors if error)
    elif validation_errors:
        errors.append(str(validation_errors))

    return sorted(set(errors))
