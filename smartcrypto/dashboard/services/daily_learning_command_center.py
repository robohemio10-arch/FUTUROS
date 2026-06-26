"""Read-only Daily Learning Command Center snapshot for SMART FUTUROS.

This module is intentionally limited to dashboard presentation contracts. It does
not execute the Daily Learning scheduler, orchestrator, stage builders, model
training, Qlib runtime updates, IA Shadow updates, Freqtrade changes, or risk
changes. All generated structures are safe, deterministic, and suitable for a
read-only dashboard surface.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_NAME = "SMART FUTUROS"
SCHEMA_VERSION = "dashboard_daily_learning_command_center_v1"
STATUS_BLOCKED = "blocked"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
FINAL_DECISION_BLOCKED = "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"

_DASHBOARD_SOURCE_ORDER: tuple[str, ...] = (
    "scheduler",
    "orchestrator",
    "qlib_research_dataset",
    "ai_shadow_feedback_bridge",
    "candidate_shadow_rule_registry",
    "shadow_rule_oos_validation",
)

_SOURCE_LABELS: dict[str, str] = {
    "scheduler": "Daily Learning scheduler paper-only",
    "orchestrator": "Daily Paper/Master Learning Loop orchestrator",
    "qlib_research_dataset": "Qlib research dataset",
    "ai_shadow_feedback_bridge": "AI Shadow feedback bridge",
    "candidate_shadow_rule_registry": "Candidate shadow rule registry",
    "shadow_rule_oos_validation": "Shadow rule OOS validation",
}

_REQUIRED_FALSE_FLAGS: tuple[str, ...] = (
    "operational_authority",
    "dashboard_operational_controls",
    "dashboard_write_controls",
    "dashboard_scheduler_execution",
    "dashboard_orchestrator_execution",
    "dashboard_stage_builder_execution",
    "dashboard_rule_application",
    "dashboard_feedback_application",
    "dashboard_model_training",
    "dashboard_model_promotion",
    "dashboard_rule_promotion",
    "dashboard_qlib_runtime_update",
    "dashboard_ai_shadow_runtime_update",
    "dashboard_freqtrade_update",
    "dashboard_risk_manager_update",
    "dashboard_live_release",
    "dashboard_canary_release",
    "dashboard_order_submission",
    "writes_data",
    "writes_runtime",
    "writes_reports",
    "writes_sqlite",
    "writes_parquet",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "exchange_private_access",
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
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
)

_TRUE_FLAGS: tuple[str, ...] = (
    "research_only",
    "read_only",
    "paper_only",
    "shadow_only",
    "dashboard_readonly",
)


@dataclass(frozen=True)
class SourceDigest:
    """Compact representation of a dashboard source payload."""

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
    operational_authority: bool
    safe_for_dashboard: bool
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
            "operational_authority": self.operational_authority,
            "safe_for_dashboard": self.safe_for_dashboard,
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


def _infer_row_count(source_id: str, payload: Mapping[str, Any]) -> int:
    direct = _first_present(
        payload,
        (
            "row_count",
            "dataset_row_count",
            "feedback_event_count",
            "candidate_rule_count",
            "oos_validation_result_count",
            "total_reported_rows",
        ),
        None,
    )
    if direct is not None:
        return max(0, _to_int(direct))

    nested_paths: tuple[tuple[str, ...], ...] = (
        ("stage_summary", "total_reported_rows"),
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
    for path in nested_paths:
        value = _extract_nested(payload, path)
        if value is not None:
            return max(0, _to_int(value))
    return 0


def _digest_source(
    source_id: str,
    payload: Mapping[str, Any] | None,
) -> SourceDigest:
    mapping = _as_mapping(payload)
    provided = bool(mapping)
    if not provided:
        return SourceDigest(
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
            operational_authority=False,
            safe_for_dashboard=True,
            validation_errors=(),
            payload_provided=False,
        )

    status = str(mapping.get("status", "unknown"))
    decision = str(mapping.get("decision", DECISION_RESEARCH))
    schema_version = mapping.get("schema_version")
    schema_text = str(schema_version) if schema_version is not None else None
    input_mode = str(mapping.get("input_mode", "in_memory_payload"))
    reason = str(mapping.get("reason", "payload_provided"))
    row_count = _infer_row_count(source_id, mapping)
    write_performed = _to_bool(mapping.get("write_performed"), default=False)
    research_only = _to_bool(mapping.get("research_only"), default=True)
    read_only = _to_bool(mapping.get("read_only"), default=True)
    operational_authority = _to_bool(mapping.get("operational_authority"), default=False)
    errors = list(_collect_validation_errors(mapping))

    unsafe_flags = {
        "write_performed": write_performed,
        "research_only_false": not research_only,
        "read_only_false": not read_only,
        "operational_authority": operational_authority,
        "decision_not_research": decision != DECISION_RESEARCH,
    }
    for flag_name, unsafe in unsafe_flags.items():
        if unsafe:
            errors.append(f"unsafe_source_{source_id}_{flag_name}")

    safe_for_dashboard = not errors
    return SourceDigest(
        source_id=source_id,
        source_name=_SOURCE_LABELS[source_id],
        status=status,
        decision=decision,
        schema_version=schema_text,
        input_mode=input_mode,
        reason=reason,
        row_count=row_count,
        write_performed=write_performed,
        research_only=research_only,
        read_only=read_only,
        operational_authority=operational_authority,
        safe_for_dashboard=safe_for_dashboard,
        validation_errors=tuple(sorted(set(errors))),
        payload_provided=True,
    )


def _normalize_sources(
    *,
    scheduler_payload: Mapping[str, Any] | None,
    orchestrator_payload: Mapping[str, Any] | None,
    qlib_research_dataset_payload: Mapping[str, Any] | None,
    ai_shadow_feedback_bridge_payload: Mapping[str, Any] | None,
    candidate_shadow_rule_registry_payload: Mapping[str, Any] | None,
    shadow_rule_oos_validation_payload: Mapping[str, Any] | None,
) -> list[SourceDigest]:
    payloads: dict[str, Mapping[str, Any] | None] = {
        "scheduler": scheduler_payload,
        "orchestrator": orchestrator_payload,
        "qlib_research_dataset": qlib_research_dataset_payload,
        "ai_shadow_feedback_bridge": ai_shadow_feedback_bridge_payload,
        "candidate_shadow_rule_registry": candidate_shadow_rule_registry_payload,
        "shadow_rule_oos_validation": shadow_rule_oos_validation_payload,
    }
    return [_digest_source(source_id, payloads[source_id]) for source_id in _DASHBOARD_SOURCE_ORDER]


def _build_dashboard_scope() -> dict[str, bool]:
    scope = {flag: False for flag in _REQUIRED_FALSE_FLAGS}
    scope.update({flag: True for flag in _TRUE_FLAGS})
    scope.update(
        {
            "builds_dashboard_snapshot": True,
            "shows_daily_learning_state": True,
            "shows_scheduler_contract": True,
            "shows_orchestrator_status": True,
            "shows_research_gates": True,
            "uses_only_in_memory_inputs": True,
            "loads_runtime_sources_by_default": False,
            "executes_any_code_from_dashboard": False,
            "creates_operational_buttons": False,
            "creates_go_live_button": False,
            "creates_promote_button": False,
        }
    )
    return scope


def _build_gate_matrix(source_digests: Sequence[SourceDigest]) -> list[dict[str, Any]]:
    all_sources_safe = all(source.safe_for_dashboard for source in source_digests)
    any_payload_loaded = any(source.payload_provided for source in source_digests)
    return [
        {
            "gate_id": "dashboard_readonly",
            "gate_name": "Dashboard remains read-only",
            "passed": True,
            "severity": "critical",
            "evidence": "dashboard_readonly=true; operational controls disabled",
        },
        {
            "gate_id": "no_scheduler_registration",
            "gate_name": "No scheduler registration",
            "passed": True,
            "severity": "critical",
            "evidence": "registers_scheduler=false; cron/systemd/windows task/service false",
        },
        {
            "gate_id": "no_runtime_execution",
            "gate_name": "No runtime execution",
            "passed": True,
            "severity": "critical",
            "evidence": "executes_orchestrator=false; executes_stage_builders=false",
        },
        {
            "gate_id": "no_operational_authority",
            "gate_name": "No operational authority",
            "passed": True,
            "severity": "critical",
            "evidence": "operational_authority=false; final decision blocked",
        },
        {
            "gate_id": "source_payload_safety",
            "gate_name": "Loaded source payloads are safe for dashboard",
            "passed": all_sources_safe,
            "severity": "high",
            "evidence": "all provided payloads must be research-only/read-only/no-write/no-authority",
        },
        {
            "gate_id": "source_payload_presence",
            "gate_name": "Source payload presence",
            "passed": any_payload_loaded,
            "severity": "info",
            "evidence": "dashboard can render empty no-runtime state; loaded payloads enrich cards",
        },
    ]


def _build_card_rows(source_digests: Sequence[SourceDigest]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for source in source_digests:
        cards.append(
            {
                "card_id": source.source_id,
                "title": source.source_name,
                "status": source.status,
                "decision": source.decision,
                "schema_version": source.schema_version,
                "input_mode": source.input_mode,
                "row_count": source.row_count,
                "safe_for_dashboard": source.safe_for_dashboard,
                "payload_provided": source.payload_provided,
                "primary_note": source.reason,
            }
        )
    return cards


def _summarize_sources(source_digests: Sequence[SourceDigest]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    input_mode_counts: dict[str, int] = {}
    unsafe_sources: list[str] = []
    for source in source_digests:
        status_counts[source.status] = status_counts.get(source.status, 0) + 1
        decision_counts[source.decision] = decision_counts.get(source.decision, 0) + 1
        input_mode_counts[source.input_mode] = input_mode_counts.get(source.input_mode, 0) + 1
        if not source.safe_for_dashboard:
            unsafe_sources.append(source.source_id)
    return {
        "source_count": len(source_digests),
        "payload_loaded_count": sum(1 for source in source_digests if source.payload_provided),
        "safe_source_count": sum(1 for source in source_digests if source.safe_for_dashboard),
        "unsafe_source_count": len(unsafe_sources),
        "unsafe_sources": unsafe_sources,
        "status_counts": status_counts,
        "decision_counts": decision_counts,
        "input_mode_counts": input_mode_counts,
        "total_reported_rows": sum(source.row_count for source in source_digests),
    }


def build_dashboard_daily_learning_command_center_snapshot(
    *,
    project_root: str | Path | None = None,
    scheduler_payload: Mapping[str, Any] | None = None,
    orchestrator_payload: Mapping[str, Any] | None = None,
    qlib_research_dataset_payload: Mapping[str, Any] | None = None,
    ai_shadow_feedback_bridge_payload: Mapping[str, Any] | None = None,
    candidate_shadow_rule_registry_payload: Mapping[str, Any] | None = None,
    shadow_rule_oos_validation_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic read-only dashboard snapshot.

    Payload arguments are optional and must already be research-only outputs. The
    function never loads runtime files, never calls stage builders, and never
    writes artifacts.
    """

    source_digests = _normalize_sources(
        scheduler_payload=scheduler_payload,
        orchestrator_payload=orchestrator_payload,
        qlib_research_dataset_payload=qlib_research_dataset_payload,
        ai_shadow_feedback_bridge_payload=ai_shadow_feedback_bridge_payload,
        candidate_shadow_rule_registry_payload=candidate_shadow_rule_registry_payload,
        shadow_rule_oos_validation_payload=shadow_rule_oos_validation_payload,
    )
    source_summary = _summarize_sources(source_digests)
    gate_matrix = _build_gate_matrix(source_digests)
    validation_errors: list[str] = []
    for source in source_digests:
        validation_errors.extend(source.validation_errors)
    failed_gate_ids = [gate["gate_id"] for gate in gate_matrix if not gate["passed"] and gate["severity"] != "info"]
    for gate_id in failed_gate_ids:
        validation_errors.append(f"failed_dashboard_gate_{gate_id}")

    input_mode = (
        "in_memory_payloads_loaded"
        if source_summary["payload_loaded_count"] > 0
        else "no_runtime_rows_loaded"
    )
    dashboard_scope = _build_dashboard_scope()

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root) if project_root is not None else ".",
        "status": STATUS_BLOCKED,
        "reason": "dashboard_daily_learning_command_center_read_only_without_operational_authority",
        "decision": DECISION_RESEARCH,
        "operator_decision": {
            "final_decision": FINAL_DECISION_BLOCKED,
            "dashboard_operational_action_allowed": False,
            "scheduler_registration_allowed": False,
            "orchestrator_execution_allowed": False,
            "stage_builder_execution_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "model_promotion_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "qlib_runtime_update_allowed": False,
            "ai_shadow_runtime_update_allowed": False,
            "training_allowed": False,
        },
        "input_mode": input_mode,
        "dashboard_scope": dashboard_scope,
        "source_summary": source_summary,
        "source_cards": _build_card_rows(source_digests),
        "source_digests": [source.as_dict() for source in source_digests],
        "gate_matrix": gate_matrix,
        "gate_summary": {
            "gate_count": len(gate_matrix),
            "passed_gate_count": sum(1 for gate in gate_matrix if gate["passed"]),
            "failed_gate_count": sum(1 for gate in gate_matrix if not gate["passed"]),
            "critical_gate_count": sum(1 for gate in gate_matrix if gate["severity"] == "critical"),
            "failed_gate_ids": [gate["gate_id"] for gate in gate_matrix if not gate["passed"]],
        },
        "command_center_sections": [
            {
                "section_id": "daily_learning_overview",
                "title": "Daily Learning Overview",
                "read_only": True,
                "contains_operational_controls": False,
            },
            {
                "section_id": "scheduler_contract",
                "title": "Scheduler Contract",
                "read_only": True,
                "contains_operational_controls": False,
            },
            {
                "section_id": "research_pipeline",
                "title": "Research Pipeline",
                "read_only": True,
                "contains_operational_controls": False,
            },
            {
                "section_id": "safety_gates",
                "title": "Safety Gates",
                "read_only": True,
                "contains_operational_controls": False,
            },
        ],
        "readiness_policy": {
            "dashboard_is_not_readiness_evidence": True,
            "dashboard_outputs_do_not_release_live": True,
            "dashboard_outputs_do_not_release_canary": True,
            "manual_go_no_go_required": True,
            "real_scheduler_registration_requires_separate_branch": True,
            "model_training_requires_separate_branch": True,
            "model_promotion_requires_separate_registry_and_oos_review": True,
            "candidate_rules_require_runtime_contract_binding": True,
            "thirty_day_gap_free_soak_required_for_future_canary_review": True,
        },
        "allowed_next_steps": [
            "criar evidence readiness integration em branch futura",
            "criar treinamento Qlib research-only em branch futura",
            "executar revisao manual sobre payloads research-only fora de runtime",
            "materializar snapshots somente com output explicito fora de runtime",
        ],
        "forbidden_actions": [
            "criar botao operacional no dashboard",
            "registrar scheduler real pelo dashboard",
            "executar scheduler pelo dashboard",
            "executar orquestrador pelo dashboard",
            "executar builders pelo dashboard",
            "alterar Freqtrade",
            "alterar RiskManager",
            "alterar Qlib runtime",
            "alterar IA Shadow runtime",
            "alterar modelos",
            "alterar datasets operacionais",
            "habilitar live",
            "habilitar canary",
            "enviar ordem real",
            "usar exchange privada",
            "escrever artefatos em data/runtime/reports/logs/freqtrade",
            "usar dashboard para liberar operacao",
            "treinar modelo nesta branch",
            "promover modelo",
            "promover regra candidata",
            "aplicar candidate rule",
            "aplicar feedback na IA Shadow",
        ],
        "write_requested": False,
        "write_performed": False,
        "validation_errors": sorted(set(validation_errors)),
    }
    snapshot.update({flag: True for flag in _TRUE_FLAGS})
    snapshot.update({flag: False for flag in _REQUIRED_FALSE_FLAGS})
    snapshot["dashboard_readonly"] = True
    snapshot["dashboard_operational_controls"] = False
    snapshot["dashboard_write_controls"] = False
    snapshot["output_path"] = None
    return snapshot


def build_daily_learning_command_center_view_model(
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact view model suitable for Streamlit rendering."""

    payload = deepcopy(
        dict(snapshot)
        if snapshot is not None
        else build_dashboard_daily_learning_command_center_snapshot()
    )
    cards = list(payload.get("source_cards", []))
    gates = list(payload.get("gate_matrix", []))
    summary = dict(payload.get("source_summary", {}))
    return {
        "title": "SMART FUTUROS — Daily Learning Command Center",
        "subtitle": "Read-only research dashboard; operational authority remains blocked.",
        "status": payload.get("status", STATUS_BLOCKED),
        "decision": payload.get("decision", DECISION_RESEARCH),
        "input_mode": payload.get("input_mode", "no_runtime_rows_loaded"),
        "source_summary": summary,
        "cards": cards,
        "gates": gates,
        "safety_footer": {
            "dashboard_readonly": bool(payload.get("dashboard_readonly", True)),
            "operational_authority": bool(payload.get("operational_authority", False)),
            "live_release_allowed": bool(payload.get("live_release_allowed", False)),
            "canary_release_allowed": bool(payload.get("canary_release_allowed", False)),
            "order_submission_enabled": bool(payload.get("order_submission_enabled", False)),
        },
    }


def validate_dashboard_daily_learning_command_center_snapshot(
    snapshot: Mapping[str, Any],
) -> list[str]:
    """Return validation errors for a dashboard snapshot."""

    errors: list[str] = []
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if snapshot.get("status") != STATUS_BLOCKED:
        errors.append("status_must_remain_blocked")
    if snapshot.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_manter_em_research")
    for flag in _TRUE_FLAGS:
        if snapshot.get(flag) is not True:
            errors.append(f"{flag}_must_be_true")
    for flag in _REQUIRED_FALSE_FLAGS:
        if snapshot.get(flag) is not False:
            errors.append(f"{flag}_must_be_false")
    gate_summary = snapshot.get("gate_summary")
    if not isinstance(gate_summary, Mapping):
        errors.append("missing_gate_summary")
    else:
        failed_critical_gates = [
            gate.get("gate_id")
            for gate in snapshot.get("gate_matrix", [])
            if isinstance(gate, Mapping)
            and gate.get("severity") == "critical"
            and gate.get("passed") is not True
        ]
        if failed_critical_gates:
            errors.append("critical_dashboard_gate_failed")
    if snapshot.get("write_performed") is not False:
        errors.append("write_performed_must_be_false")
    return sorted(set(errors))


__all__ = [
    "PROJECT_NAME",
    "SCHEMA_VERSION",
    "build_dashboard_daily_learning_command_center_snapshot",
    "build_daily_learning_command_center_view_model",
    "validate_dashboard_daily_learning_command_center_snapshot",
]
