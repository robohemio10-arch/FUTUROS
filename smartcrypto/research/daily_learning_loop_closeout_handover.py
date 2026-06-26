"""SMART FUTUROS Daily Learning Loop closeout handover V1.

This module builds a deterministic, research-only closeout payload for the
Daily Learning Loop. It is intentionally inert: it does not load operational
runtime state, does not write by default, does not execute builders, and does
not grant release authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_NAME = "SMART FUTUROS"
SCHEMA_VERSION = "daily_learning_loop_closeout_handover_v1"
STATUS = "blocked"
DECISION = "MANTER_EM_RESEARCH"
REASON = "daily_learning_loop_closeout_handover_research_only_blocked"

FORBIDDEN_OUTPUT_PARTS = {
    "data",
    "runtime",
    "reports",
    "logs",
    "freqtrade",
    "user_data",
    "models",
    "artifacts",
}

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "closeout_handover_only": True,
    "handover_is_informational": True,
    "handover_release_authority": False,
    "readiness_snapshot_blocked": True,
    "readiness_release_authority": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "registers_scheduler": False,
    "executes_scheduler": False,
    "executes_orchestrator": False,
    "executes_stage_builders": False,
    "runs_training": False,
    "runs_ocr": False,
    "runs_ai_shadow_incremental": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "updates_ai_shadow_policy": False,
    "updates_ai_shadow_thresholds": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_models": False,
    "writes_runtime": False,
    "writes_data": False,
    "writes_reports": False,
    "writes_parquet": False,
    "writes_sqlite": False,
    "writes_ai_shadow_sqlite": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "promotes_shadow_rules": False,
    "can_promote_model": False,
    "can_promote_rules": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
}

FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "usar handover como release evidence",
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
)

ALLOWED_NEXT_STEPS: tuple[str, ...] = (
    "executar baseline pos-merge do closeout handover",
    "manter Daily Learning como trilha research-only ate nova decisao manual",
    "planejar treinamento Qlib research-only em branch separada futura",
    "planejar OOS/walk-forward adicional antes de qualquer discussao de promocao",
    "revisar paper-vs-master divergence antes de qualquer alteracao operacional",
)


@dataclass(frozen=True)
class CloseoutStage:
    sequence: int
    branch: str
    stage_id: str
    title: str
    evidence_role: str
    operational_authority: bool = False
    release_authority: bool = False
    status: str = STATUS
    decision: str = DECISION
    research_only: bool = True
    read_only: bool = True
    paper_only: bool = True
    shadow_only: bool = True
    write_performed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "branch": self.branch,
            "stage_id": self.stage_id,
            "title": self.title,
            "evidence_role": self.evidence_role,
            "status": self.status,
            "decision": self.decision,
            "research_only": self.research_only,
            "read_only": self.read_only,
            "paper_only": self.paper_only,
            "shadow_only": self.shadow_only,
            "operational_authority": self.operational_authority,
            "release_authority": self.release_authority,
            "write_performed": self.write_performed,
            "safe_for_closeout": self.is_safe_for_closeout,
        }

    @property
    def is_safe_for_closeout(self) -> bool:
        return (
            self.research_only
            and self.read_only
            and self.paper_only
            and self.shadow_only
            and not self.operational_authority
            and not self.release_authority
            and not self.write_performed
            and self.decision == DECISION
        )


def build_canonical_daily_learning_stages() -> list[dict[str, Any]]:
    """Return the canonical Daily Learning Loop branch ledger."""
    stages = [
        CloseoutStage(
            1,
            "codex/paper-master-divergence-research-closeout-v1",
            "paper_master_divergence_research_closeout",
            "Paper vs Master divergence closeout",
            "diagnostic_informational",
        ),
        CloseoutStage(
            2,
            "codex/daily-learning-contracts-and-source-map-v1",
            "daily_learning_contracts_and_source_map",
            "Daily Learning contracts and source map",
            "contract_informational",
        ),
        CloseoutStage(
            3,
            "codex/daily-learning-readonly-loaders-v1",
            "daily_learning_readonly_loaders",
            "Read-only source loaders",
            "loader_informational",
        ),
        CloseoutStage(
            4,
            "codex/daily-paper-master-kpi-pack-v1",
            "daily_paper_master_kpi_pack",
            "Daily Paper/Master KPI pack",
            "kpi_informational",
        ),
        CloseoutStage(
            5,
            "codex/daily-paper-master-divergence-and-alignment-v1",
            "daily_paper_master_divergence_and_alignment",
            "Daily Paper/Master divergence and temporal alignment",
            "diagnostic_informational",
        ),
        CloseoutStage(
            6,
            "codex/daily-candle-coverage-and-entry-features-v1",
            "daily_candle_coverage_and_entry_features",
            "Candle coverage and entry feature diagnostics",
            "feature_diagnostic_informational",
        ),
        CloseoutStage(
            7,
            "codex/daily-mistake-and-winner-catalog-v1",
            "daily_mistake_and_winner_catalog",
            "Mistake and winner catalog",
            "catalog_informational",
        ),
        CloseoutStage(
            8,
            "codex/daily-pattern-mining-research-v1",
            "daily_pattern_mining_research",
            "Daily pattern mining research",
            "pattern_research_informational",
        ),
        CloseoutStage(
            9,
            "codex/daily-candidate-shadow-rule-registry-v1",
            "daily_candidate_shadow_rule_registry",
            "Candidate shadow rule registry",
            "candidate_rule_registry_informational",
        ),
        CloseoutStage(
            10,
            "codex/daily-shadow-rule-oos-validation-v1",
            "daily_shadow_rule_oos_validation",
            "Shadow rule OOS validation",
            "oos_validation_informational",
        ),
        CloseoutStage(
            11,
            "codex/daily-learning-ai-shadow-feedback-bridge-v1",
            "daily_learning_ai_shadow_feedback_bridge",
            "AI Shadow feedback bridge",
            "feedback_bridge_informational",
        ),
        CloseoutStage(
            12,
            "codex/daily-learning-qlib-research-dataset-v1",
            "daily_learning_qlib_research_dataset",
            "Qlib research dataset",
            "dataset_research_informational",
        ),
        CloseoutStage(
            13,
            "codex/daily-paper-master-learning-loop-orchestrator-v1",
            "daily_paper_master_learning_loop_orchestrator",
            "Daily Paper/Master Learning Loop orchestrator",
            "orchestrator_contract_informational",
        ),
        CloseoutStage(
            14,
            "codex/daily-learning-scheduler-paper-v1",
            "daily_learning_scheduler_paper",
            "Daily Learning scheduler paper-only contract",
            "scheduler_contract_informational",
        ),
        CloseoutStage(
            15,
            "codex/dashboard-daily-learning-command-center-v1",
            "dashboard_daily_learning_command_center",
            "Dashboard Daily Learning Command Center",
            "dashboard_observability_informational",
        ),
        CloseoutStage(
            16,
            "codex/daily-learning-evidence-readiness-integration-v1",
            "daily_learning_evidence_readiness_integration",
            "Daily Learning evidence/readiness integration",
            "readiness_informational_non_releasing",
        ),
    ]
    return [stage.to_payload() for stage in stages]


def build_gate_matrix(stage_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unsafe_stages = [stage["stage_id"] for stage in stage_payloads if not stage["safe_for_closeout"]]
    return [
        {
            "gate_id": "closeout_informational_only",
            "gate_name": "Closeout handover is informational only",
            "severity": "critical",
            "passed": True,
            "evidence": "handover_is_informational=true; handover_release_authority=false",
        },
        {
            "gate_id": "readiness_stays_blocked",
            "gate_name": "Readiness remains blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "readiness_snapshot_blocked=true; final decision remains MANTER_EM_RESEARCH",
        },
        {
            "gate_id": "live_canary_orders_blocked",
            "gate_name": "Live, canary and orders remain blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "live_release_allowed=false; canary_release_allowed=false; sends_orders=false",
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
            "gate_id": "stage_ledger_safety",
            "gate_name": "All Daily Learning stages are safe for closeout",
            "severity": "critical",
            "passed": len(unsafe_stages) == 0,
            "evidence": f"unsafe_stage_count={len(unsafe_stages)}",
            "unsafe_stage_ids": unsafe_stages,
        },
        {
            "gate_id": "no_runtime_payload_required",
            "gate_name": "Runtime payload presence is not required for closeout handover",
            "severity": "info",
            "passed": True,
            "evidence": "no-runtime mode is acceptable; branch is a canonical closeout handover",
        },
    ]


def summarize_gates(gates: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [gate for gate in gates if not gate.get("passed", False)]
    critical_failed = [gate for gate in failed if gate.get("severity") == "critical"]
    high_failed = [gate for gate in failed if gate.get("severity") == "high"]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [gate["gate_id"] for gate in failed],
        "critical_gate_count": sum(1 for gate in gates if gate.get("severity") == "critical"),
        "critical_failed_gate_ids": [gate["gate_id"] for gate in critical_failed],
        "high_failed_gate_ids": [gate["gate_id"] for gate in high_failed],
    }


def build_closeout_handover_sections(stage_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "section_id": "executive_closeout",
            "title": "Daily Learning Loop closeout",
            "summary": "Loop fechado como research-only, sem autoridade operacional e sem release authority.",
            "contains_release_decision": False,
            "contains_operational_controls": False,
        },
        {
            "section_id": "canonical_stage_ledger",
            "title": "Canonical stage ledger",
            "summary": f"{len(stage_payloads)} etapas consolidadas como informativas e bloqueadas.",
            "contains_release_decision": False,
            "contains_operational_controls": False,
        },
        {
            "section_id": "safety_and_readiness",
            "title": "Safety and readiness status",
            "summary": "Readiness permanece blocked; live/canary/orders continuam bloqueados.",
            "contains_release_decision": False,
            "contains_operational_controls": False,
        },
        {
            "section_id": "next_research_path",
            "title": "Next research path",
            "summary": "Próximas ações exigem branches separadas, revisão manual e validação OOS adicional.",
            "contains_release_decision": False,
            "contains_operational_controls": False,
        },
    ]


def build_daily_learning_loop_closeout_handover(
    *, project_root: str | Path = ".", source_payloads: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """Build the deterministic closeout handover payload.

    ``source_payloads`` is intentionally optional. This branch does not load
    runtime files by default; provided payloads are only summarized as inert
    informational evidence.
    """
    root_display = str(project_root)
    stages = build_canonical_daily_learning_stages()
    gates = build_gate_matrix(stages)
    source_summary = summarize_optional_source_payloads(source_payloads or {})

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": root_display,
        "status": STATUS,
        "reason": REASON,
        "decision": DECISION,
        "input_mode": "no_runtime_rows_loaded",
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
        **SAFETY_FLAGS,
        "daily_learning_loop_closed": True,
        "daily_learning_loop_closeout_handover_created": True,
        "closeout_scope": {
            "consolidates_daily_learning_loop": True,
            "consolidates_stage_count": len(stages),
            "creates_canonical_handover": True,
            "handover_is_informational": True,
            "grants_operational_authority": False,
            "grants_release_authority": False,
            "uses_only_in_memory_inputs": True,
            "loads_runtime_sources_by_default": False,
            "writes_runtime": False,
            "writes_data": False,
            "writes_reports": False,
            "executes_scheduler": False,
            "executes_orchestrator": False,
            "executes_stage_builders": False,
            "runs_training": False,
            "updates_qlib_runtime": False,
            "updates_ai_shadow_runtime": False,
            "updates_freqtrade": False,
            "updates_risk_manager": False,
            "applies_shadow_rules": False,
            "applies_feedback_to_ai_shadow": False,
            "promotes_model": False,
            "promotes_rules": False,
            "releases_live": False,
            "releases_canary": False,
        },
        "canonical_stage_ledger": stages,
        "stage_summary": summarize_stages(stages),
        "handover_sections": build_closeout_handover_sections(stages),
        "gate_matrix": gates,
        "gate_summary": summarize_gates(gates),
        "readiness_policy": {
            "closeout_handover_is_not_release_evidence": True,
            "daily_learning_evidence_is_not_release_evidence": True,
            "daily_learning_evidence_does_not_release_live": True,
            "daily_learning_evidence_does_not_release_canary": True,
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
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
            "daily_learning_closeout_accepted": True,
            "closeout_release_authority": False,
            "daily_learning_release_authority": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "scheduler_registration_allowed": False,
            "orchestrator_execution_allowed": False,
            "stage_builder_execution_allowed": False,
            "training_allowed": False,
            "qlib_runtime_update_allowed": False,
            "ai_shadow_runtime_update_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
        },
        "readiness_summary": {
            "readiness_status": "blocked",
            "release_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "daily_learning_evidence_role": "informational_non_releasing",
            "closeout_handover_role": "canonical_research_closeout_non_releasing",
            "stage_count": len(stages),
            "safe_stage_count": sum(1 for stage in stages if stage["safe_for_closeout"]),
            "unsafe_stage_count": sum(1 for stage in stages if not stage["safe_for_closeout"]),
            "payload_loaded_count": source_summary["payload_loaded_count"],
            "unsafe_source_count": source_summary["unsafe_source_count"],
        },
        "source_summary": source_summary,
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "validation_errors": [],
    }

    payload["validation_errors"] = validate_daily_learning_loop_closeout_handover(payload)
    return payload


def summarize_stages(stages: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    statuses: dict[str, int] = {}
    roles: dict[str, int] = {}
    for stage in stages:
        decisions[stage["decision"]] = decisions.get(stage["decision"], 0) + 1
        statuses[stage["status"]] = statuses.get(stage["status"], 0) + 1
        roles[stage["evidence_role"]] = roles.get(stage["evidence_role"], 0) + 1
    return {
        "stage_count": len(stages),
        "safe_stage_count": sum(1 for stage in stages if stage["safe_for_closeout"]),
        "unsafe_stage_count": sum(1 for stage in stages if not stage["safe_for_closeout"]),
        "decision_counts": decisions,
        "status_counts": statuses,
        "evidence_role_counts": roles,
        "operational_authority_stage_count": sum(1 for stage in stages if stage["operational_authority"]),
        "release_authority_stage_count": sum(1 for stage in stages if stage["release_authority"]),
    }


def summarize_optional_source_payloads(source_payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    digests: list[dict[str, Any]] = []
    unsafe: list[str] = []
    for source_id, payload in sorted(source_payloads.items()):
        safe = is_source_payload_safe(payload)
        if not safe:
            unsafe.append(source_id)
        digests.append(
            {
                "source_id": source_id,
                "schema_version": payload.get("schema_version"),
                "status": payload.get("status", "unknown"),
                "decision": payload.get("decision", "unknown"),
                "research_only": bool(payload.get("research_only", False)),
                "read_only": bool(payload.get("read_only", False)),
                "operational_authority": bool(payload.get("operational_authority", True)),
                "release_authority": bool(
                    payload.get("readiness_release_authority", payload.get("release_authority", True))
                ),
                "write_performed": bool(payload.get("write_performed", True)),
                "safe_for_closeout": safe,
            }
        )
    return {
        "source_count": len(source_payloads),
        "payload_loaded_count": len(source_payloads),
        "safe_source_count": len(source_payloads) - len(unsafe),
        "unsafe_source_count": len(unsafe),
        "unsafe_sources": unsafe,
        "source_digests": digests,
    }


def is_source_payload_safe(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("decision") == DECISION
        and bool(payload.get("research_only", False))
        and bool(payload.get("read_only", False))
        and not bool(payload.get("operational_authority", True))
        and not bool(payload.get("write_performed", True))
        and not bool(payload.get("live_release_allowed", True))
        and not bool(payload.get("canary_release_allowed", True))
    )


def validate_daily_learning_loop_closeout_handover(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_true = (
        "research_only",
        "read_only",
        "paper_only",
        "shadow_only",
        "closeout_handover_only",
        "handover_is_informational",
        "readiness_snapshot_blocked",
        "daily_learning_loop_closed",
        "daily_learning_loop_closeout_handover_created",
    )
    expected_false = (
        "operational_authority",
        "handover_release_authority",
        "readiness_release_authority",
        "live_release_allowed",
        "canary_release_allowed",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "registers_scheduler",
        "executes_scheduler",
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
        "write_performed",
    )
    for key in expected_true:
        if payload.get(key) is not True:
            errors.append(f"expected_true:{key}")
    for key in expected_false:
        if payload.get(key) is not False:
            errors.append(f"expected_false:{key}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if payload.get("status") != STATUS:
        errors.append("invalid_status")
    if payload.get("decision") != DECISION:
        errors.append("invalid_decision")
    stages = payload.get("canonical_stage_ledger")
    if not isinstance(stages, list) or len(stages) != 16:
        errors.append("invalid_stage_count")
    else:
        for stage in stages:
            if not isinstance(stage, dict) or not stage.get("safe_for_closeout"):
                errors.append("unsafe_stage_detected")
                break
    gate_summary = payload.get("gate_summary", {})
    if gate_summary.get("critical_failed_gate_ids"):
        errors.append("critical_gate_failed")
    source_summary = payload.get("source_summary", {})
    if source_summary.get("unsafe_source_count", 0) != 0:
        errors.append("unsafe_source_payload_detected")
    return errors


def is_output_path_forbidden(project_root: str | Path, output_path: str | Path) -> bool:
    root = Path(project_root).resolve()
    out = Path(output_path).resolve()
    try:
        rel_parts = out.relative_to(root).parts
    except ValueError:
        return False
    normalized = {part.lower() for part in rel_parts}
    return bool(normalized.intersection(FORBIDDEN_OUTPUT_PARTS))


def write_json_payload(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
