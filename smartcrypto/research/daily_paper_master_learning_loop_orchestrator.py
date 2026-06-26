"""Research-only orchestrator for the SMART FUTUROS Daily Learning Loop.

The orchestrator consolidates already-implemented Daily Learning research
contracts into a deterministic in-memory payload. It does not load runtime data
by default, does not write project artifacts by default, does not train models,
does not apply rules, and does not grant operational authority.
"""

from __future__ import annotations

import importlib
import inspect
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION = (
    "daily_paper_master_learning_loop_orchestrator_v1"
)
DEFAULT_SAMPLE_LIMIT = 20

_FALSE_OPERATIONAL_FLAGS = (
    "applies_feedback_to_ai_shadow",
    "applies_shadow_rules",
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "can_promote_model",
    "can_promote_rules",
    "canary_release_allowed",
    "changes_model",
    "changes_risk",
    "exchange_private_access",
    "live_release_allowed",
    "live_trading_enabled",
    "operational_authority",
    "order_submission_enabled",
    "promotes_shadow_rules",
    "real_order_submission_enabled",
    "runs_ai_shadow_incremental",
    "runs_ocr",
    "runs_training",
    "sends_orders",
    "updates_ai_shadow_policy",
    "updates_ai_shadow_runtime",
    "updates_ai_shadow_thresholds",
    "updates_freqtrade",
    "updates_models",
    "updates_qlib_runtime",
    "updates_risk_manager",
    "uses_net_pnl_as_feature",
    "uses_outcome_as_feature",
    "writes_ai_shadow_sqlite",
    "writes_data",
    "writes_parquet",
    "writes_reports",
    "writes_runtime",
    "writes_sqlite",
)

_TRUE_SAFETY_FLAGS = (
    "paper_only",
    "read_only",
    "research_only",
    "shadow_only",
)

_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "sequence": 1,
        "stage_id": "contracts_source_map",
        "stage_name": "Daily Learning contracts and source map",
        "branch": "codex/daily-learning-contracts-and-source-map-v1",
        "module": "smartcrypto.research.daily_learning_contracts",
        "builder": "build_daily_learning_contracts_and_source_map_report",
        "consumes": [],
        "produces": ["contracts", "source_map"],
    },
    {
        "sequence": 2,
        "stage_id": "readonly_loaders",
        "stage_name": "Daily Learning read-only loaders",
        "branch": "codex/daily-learning-readonly-loaders-v1",
        "module": "smartcrypto.research.daily_learning_readonly_loaders",
        "builder": "build_daily_learning_readonly_loaders_report",
        "consumes": ["contracts", "source_map"],
        "produces": ["loaded_research_inputs"],
    },
    {
        "sequence": 3,
        "stage_id": "paper_master_kpi_pack",
        "stage_name": "Daily paper/master KPI pack",
        "branch": "codex/daily-paper-master-kpi-pack-v1",
        "module": "smartcrypto.research.daily_paper_master_kpi_pack",
        "builder": "build_daily_paper_master_kpi_pack_report",
        "consumes": ["loaded_research_inputs"],
        "produces": ["paper_master_kpis"],
    },
    {
        "sequence": 4,
        "stage_id": "divergence_alignment",
        "stage_name": "Daily paper/master divergence and temporal alignment",
        "branch": "codex/daily-paper-master-divergence-and-alignment-v1",
        "module": "smartcrypto.research.daily_paper_master_divergence_and_alignment",
        "builder": "build_daily_paper_master_divergence_and_alignment_report",
        "consumes": ["paper_master_kpis", "loaded_research_inputs"],
        "produces": ["divergence_summary", "alignment_summary"],
    },
    {
        "sequence": 5,
        "stage_id": "candle_coverage_entry_features",
        "stage_name": "Daily candle coverage and entry features",
        "branch": "codex/daily-candle-coverage-and-entry-features-v1",
        "module": "smartcrypto.research.daily_candle_coverage_entry_features",
        "builder": "build_daily_candle_coverage_entry_features_report",
        "consumes": ["loaded_research_inputs"],
        "produces": ["entry_feature_rows", "candle_coverage_summary"],
    },
    {
        "sequence": 6,
        "stage_id": "mistake_winner_catalog",
        "stage_name": "Daily mistake and winner catalog",
        "branch": "codex/daily-mistake-and-winner-catalog-v1",
        "module": "smartcrypto.research.daily_mistake_and_winner_catalog",
        "builder": "build_daily_mistake_and_winner_catalog_report",
        "consumes": ["paper_master_kpis", "divergence_summary", "entry_feature_rows"],
        "produces": ["mistake_winner_catalog"],
    },
    {
        "sequence": 7,
        "stage_id": "pattern_mining_research",
        "stage_name": "Daily pattern mining research",
        "branch": "codex/daily-pattern-mining-research-v1",
        "module": "smartcrypto.research.daily_pattern_mining_research",
        "builder": "build_daily_pattern_mining_research_report",
        "consumes": ["mistake_winner_catalog", "entry_feature_rows"],
        "produces": ["research_patterns"],
    },
    {
        "sequence": 8,
        "stage_id": "candidate_shadow_rule_registry",
        "stage_name": "Daily candidate shadow rule registry",
        "branch": "codex/daily-candidate-shadow-rule-registry-v1",
        "module": "smartcrypto.research.daily_candidate_shadow_rule_registry",
        "builder": "build_daily_candidate_shadow_rule_registry_report",
        "consumes": ["research_patterns"],
        "produces": ["candidate_shadow_rules"],
    },
    {
        "sequence": 9,
        "stage_id": "shadow_rule_oos_validation",
        "stage_name": "Daily shadow rule OOS validation",
        "branch": "codex/daily-shadow-rule-oos-validation-v1",
        "module": "smartcrypto.research.daily_shadow_rule_oos_validation",
        "builder": "build_daily_shadow_rule_oos_validation_report",
        "consumes": ["candidate_shadow_rules", "loaded_research_inputs"],
        "produces": ["oos_validation_results"],
    },
    {
        "sequence": 10,
        "stage_id": "ai_shadow_feedback_bridge",
        "stage_name": "Daily Learning AI Shadow feedback bridge",
        "branch": "codex/daily-learning-ai-shadow-feedback-bridge-v1",
        "module": "smartcrypto.research.daily_learning_ai_shadow_feedback_bridge",
        "builder": "build_daily_learning_ai_shadow_feedback_bridge_report",
        "consumes": ["oos_validation_results", "candidate_shadow_rules"],
        "produces": ["ai_shadow_feedback_events"],
    },
    {
        "sequence": 11,
        "stage_id": "qlib_research_dataset",
        "stage_name": "Daily Learning Qlib research dataset",
        "branch": "codex/daily-learning-qlib-research-dataset-v1",
        "module": "smartcrypto.research.daily_learning_qlib_research_dataset",
        "builder": "build_daily_learning_qlib_research_dataset_report",
        "consumes": ["mistake_winner_catalog", "entry_feature_rows", "oos_validation_results", "ai_shadow_feedback_events"],
        "produces": ["qlib_research_dataset"],
    },
)


class StageInvocationError(RuntimeError):
    """Raised when an optional stage builder cannot be executed safely."""


def _safe_str(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "sim"}:
            return True
        if text in {"0", "false", "no", "n", "nao", "não"}:
            return False
    return None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_payload_mapping(
    stage_payloads: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if stage_payloads is None:
        return {}
    return {
        _safe_str(stage_id): payload
        for stage_id, payload in stage_payloads.items()
        if isinstance(payload, Mapping)
    }


def get_daily_learning_stage_plan() -> list[dict[str, Any]]:
    """Return the canonical Daily Learning stage plan."""

    return [dict(stage) for stage in _STAGE_DEFINITIONS]


def _extract_nested_count(payload: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        value = payload.get(key)
        count = _safe_int(value)
        if count is not None:
            return count
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = _extract_nested_count(value, keys)
            if nested is not None:
                return nested
    return None


def _extract_stage_payload_digest(payload: Mapping[str, Any]) -> dict[str, Any]:
    row_count = _extract_nested_count(
        payload,
        (
            "row_count",
            "dataset_row_count",
            "feedback_event_count",
            "candidate_rule_count",
            "oos_validation_result_count",
            "catalog_entry_count",
            "feature_row_count",
            "trade_count",
            "input_trade_count",
            "loaded_row_count",
        ),
    )
    return {
        "schema_version": payload.get("schema_version"),
        "status": _safe_str(payload.get("status"), "unknown"),
        "decision": _safe_str(payload.get("decision"), "unknown"),
        "reason": _safe_str(payload.get("reason"), "unknown"),
        "input_mode": _safe_str(payload.get("input_mode"), "unknown"),
        "row_count": row_count if row_count is not None else 0,
        "write_performed": bool(_safe_bool(payload.get("write_performed")) or False),
        "research_only": bool(_safe_bool(payload.get("research_only")) or False),
        "read_only": bool(_safe_bool(payload.get("read_only")) or False),
        "operational_authority": bool(
            _safe_bool(payload.get("operational_authority")) or False
        ),
    }


def _build_missing_stage_payload(definition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": None,
        "status": "not_executed",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "no_runtime_rows_loaded",
        "input_mode": "no_runtime_rows_loaded",
        "row_count": 0,
        "write_performed": False,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "expected_module": definition["module"],
        "expected_builder": definition["builder"],
    }


def _invoke_stage_builder(definition: Mapping[str, Any], project_root: Path) -> Mapping[str, Any]:
    module_name = _safe_str(definition.get("module"))
    builder_name = _safe_str(definition.get("builder"))
    try:
        module = importlib.import_module(module_name)
        builder = getattr(module, builder_name)
    except (ImportError, AttributeError) as exc:
        return {
            "schema_version": None,
            "status": "unavailable",
            "decision": "MANTER_EM_RESEARCH",
            "reason": f"stage_builder_unavailable:{exc.__class__.__name__}",
            "input_mode": "no_runtime_rows_loaded",
            "write_performed": False,
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
        }

    try:
        signature = inspect.signature(builder)
        if "project_root" in signature.parameters:
            payload = builder(project_root=project_root)
        else:
            payload = builder()
    except Exception as exc:  # pragma: no cover - defensive boundary for optional plugins.
        return {
            "schema_version": None,
            "status": "error",
            "decision": "MANTER_EM_RESEARCH",
            "reason": f"stage_builder_failed:{exc.__class__.__name__}",
            "input_mode": "builder_execution_failed",
            "write_performed": False,
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
        }

    if not isinstance(payload, Mapping):
        return {
            "schema_version": None,
            "status": "error",
            "decision": "MANTER_EM_RESEARCH",
            "reason": "stage_builder_returned_non_mapping_payload",
            "input_mode": "builder_execution_failed",
            "write_performed": False,
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
        }
    return payload


def build_daily_learning_stage_result(
    definition: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    project_root: str | Path | None = None,
    execute_stage_builder: bool = False,
) -> dict[str, Any]:
    """Build a deterministic stage result without granting operational authority."""

    resolved_project_root = Path(project_root or ".")
    source = "provided_payload" if payload is not None else "not_executed"
    effective_payload: Mapping[str, Any]
    if payload is not None:
        effective_payload = payload
    elif execute_stage_builder:
        source = "optional_stage_builder"
        effective_payload = _invoke_stage_builder(definition, resolved_project_root)
    else:
        effective_payload = _build_missing_stage_payload(definition)

    digest = _extract_stage_payload_digest(effective_payload)
    status = _safe_str(digest.get("status"), "unknown")
    validation_errors = _validate_stage_payload_flags(
        _safe_str(definition.get("stage_id")), effective_payload
    )
    return {
        "sequence": int(definition["sequence"]),
        "stage_id": _safe_str(definition.get("stage_id")),
        "stage_name": _safe_str(definition.get("stage_name")),
        "branch": _safe_str(definition.get("branch")),
        "expected_module": _safe_str(definition.get("module")),
        "expected_builder": _safe_str(definition.get("builder")),
        "consumes": list(definition.get("consumes", [])),
        "produces": list(definition.get("produces", [])),
        "source": source,
        "status": status,
        "decision": _safe_str(digest.get("decision"), "MANTER_EM_RESEARCH"),
        "reason": _safe_str(digest.get("reason"), "unknown"),
        "input_mode": _safe_str(digest.get("input_mode"), "unknown"),
        "schema_version": digest.get("schema_version"),
        "row_count": int(digest.get("row_count", 0) or 0),
        "write_performed": bool(digest.get("write_performed") or False),
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "safe_for_research_orchestration": not validation_errors,
        "payload_digest": digest,
        "validation_errors": validation_errors,
    }


def _validate_stage_payload_flags(stage_id: str, payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for flag in _FALSE_OPERATIONAL_FLAGS:
        value = _safe_bool(payload.get(flag))
        if value is True:
            errors.append(f"{stage_id}:{flag}_must_be_false")
    for flag in _TRUE_SAFETY_FLAGS:
        value = _safe_bool(payload.get(flag))
        if value is False:
            errors.append(f"{stage_id}:{flag}_must_not_be_false")
    return sorted(set(errors))


def _summarize_stage_results(stage_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(_safe_str(stage.get("status")) for stage in stage_results)
    decision_counts = Counter(_safe_str(stage.get("decision")) for stage in stage_results)
    source_counts = Counter(_safe_str(stage.get("source")) for stage in stage_results)
    row_counts = {
        _safe_str(stage.get("stage_id")): int(stage.get("row_count", 0) or 0)
        for stage in stage_results
    }
    return {
        "stage_count": len(stage_results),
        "status_counts": dict(sorted(status_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "row_counts_by_stage": row_counts,
        "total_reported_rows": sum(row_counts.values()),
        "failed_stage_count": sum(
            1 for stage in stage_results if _safe_str(stage.get("status")) == "error"
        ),
        "not_executed_stage_count": sum(
            1 for stage in stage_results if _safe_str(stage.get("status")) == "not_executed"
        ),
        "provided_payload_stage_count": sum(
            1
            for stage in stage_results
            if _safe_str(stage.get("source")) == "provided_payload"
        ),
        "unsafe_stage_count": sum(
            1 for stage in stage_results if not bool(stage.get("safe_for_research_orchestration"))
        ),
    }


def build_daily_learning_loop_orchestration(
    *,
    project_root: str | Path | None = None,
    stage_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    execute_stage_builders: bool = False,
) -> dict[str, Any]:
    """Build the stage orchestration payload in memory."""

    payload_by_stage = _as_payload_mapping(stage_payloads)
    stage_results = [
        build_daily_learning_stage_result(
            definition,
            payload=payload_by_stage.get(_safe_str(definition["stage_id"])),
            project_root=project_root,
            execute_stage_builder=execute_stage_builders,
        )
        for definition in _STAGE_DEFINITIONS
    ]
    return {
        "orchestrator_scope": {
            "orchestrates_daily_learning_loop": True,
            "research_orchestration_only": True,
            "read_only_orchestration": True,
            "uses_only_in_memory_inputs": not execute_stage_builders,
            "optional_stage_builder_execution_requested": execute_stage_builders,
            "loads_runtime_sources_by_default": False,
            "writes_project_artifacts_by_default": False,
            "applies_shadow_rules": False,
            "applies_feedback_to_ai_shadow": False,
            "runs_training": False,
            "updates_qlib_runtime": False,
            "updates_ai_shadow_runtime": False,
            "updates_freqtrade": False,
            "updates_risk_manager": False,
            "promotes_rules": False,
            "promotes_model": False,
            "writes_data": False,
            "writes_reports": False,
            "writes_runtime": False,
        },
        "stage_plan": get_daily_learning_stage_plan(),
        "stage_results": stage_results,
        "stage_summary": _summarize_stage_results(stage_results),
    }


def _infer_input_mode(
    stage_payloads: Mapping[str, Mapping[str, Any]] | None,
    execute_stage_builders: bool,
) -> str:
    if execute_stage_builders:
        return "optional_stage_builder_execution_requested"
    if stage_payloads:
        return "in_memory_stage_payloads"
    return "no_runtime_rows_loaded"


def build_daily_paper_master_learning_loop_orchestrator_report(
    *,
    project_root: str | Path | None = None,
    stage_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    execute_stage_builders: bool = False,
) -> dict[str, Any]:
    """Build the canonical Branch 13 Daily Learning Loop orchestrator report."""

    orchestration = build_daily_learning_loop_orchestration(
        project_root=project_root,
        stage_payloads=stage_payloads,
        execute_stage_builders=execute_stage_builders,
    )
    report: dict[str, Any] = {
        "schema_version": DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "project_root": str(project_root or "."),
        "status": "blocked",
        "reason": "daily_learning_orchestrator_research_only_without_operational_authority",
        "decision": "MANTER_EM_RESEARCH",
        "input_mode": _infer_input_mode(stage_payloads, execute_stage_builders),
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
        "applies_feedback_to_ai_shadow": False,
        "applies_shadow_rules": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_model": False,
        "can_promote_rules": False,
        "canary_release_allowed": False,
        "changes_model": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "live_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "promotes_shadow_rules": False,
        "real_order_submission_enabled": False,
        "runs_ai_shadow_incremental": False,
        "runs_ocr": False,
        "runs_training": False,
        "sends_orders": False,
        "updates_ai_shadow_policy": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "updates_freqtrade": False,
        "updates_models": False,
        "updates_qlib_runtime": False,
        "updates_risk_manager": False,
        "uses_net_pnl_as_feature": False,
        "uses_outcome_as_feature": False,
        "writes_ai_shadow_sqlite": False,
        "writes_data": False,
        "writes_parquet": False,
        "writes_reports": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "daily_learning_orchestrator": orchestration,
        "orchestrator_scope": orchestration["orchestrator_scope"],
        "stage_summary": orchestration["stage_summary"],
        "operator_decision": {
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
            "daily_learning_loop_operational_application_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "ai_shadow_runtime_update_allowed": False,
            "qlib_runtime_update_allowed": False,
            "training_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
        },
        "readiness_policy": {
            "daily_learning_orchestrator_is_not_readiness_evidence": True,
            "daily_learning_outputs_do_not_release_live": True,
            "daily_learning_outputs_do_not_release_canary": True,
            "manual_go_no_go_required": True,
            "model_training_requires_separate_branch": True,
            "model_promotion_requires_separate_registry_and_oos_review": True,
            "candidate_rules_require_runtime_contract_binding": True,
            "thirty_day_gap_free_soak_required_for_future_canary_review": True,
        },
        "forbidden_actions": [
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
            "usar orquestrador para liberar operacao",
            "treinar modelo nesta branch",
            "promover modelo",
            "promover regra candidata",
            "aplicar candidate rule",
            "aplicar feedback na IA Shadow",
        ],
        "allowed_next_steps": [
            "criar scheduler paper em branch futura",
            "criar dashboard daily learning command center em branch futura",
            "criar evidence readiness integration em branch futura",
            "criar treinamento Qlib research-only em branch futura",
            "executar revisão manual sobre payloads research-only materializados fora de runtime",
        ],
        "validation_errors": [],
    }
    report["validation_errors"] = validate_daily_paper_master_learning_loop_orchestrator_report(
        report
    )
    return report


def validate_daily_paper_master_learning_loop_orchestrator_report(
    payload: Mapping[str, Any]
) -> list[str]:
    """Validate the Branch 13 safety contract."""

    errors: list[str] = []
    if payload.get("schema_version") != DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if payload.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if payload.get("decision") != "MANTER_EM_RESEARCH":
        errors.append("decision_must_remain_mantem_em_research")
    for flag in _TRUE_SAFETY_FLAGS:
        if payload.get(flag) is not True:
            errors.append(f"{flag}_must_be_true")
    for flag in _FALSE_OPERATIONAL_FLAGS:
        if payload.get(flag) is not False:
            errors.append(f"{flag}_must_be_false")
    if payload.get("write_performed") not in {False, True}:
        errors.append("write_performed_must_be_boolean")
    orchestration = _as_mapping(payload.get("daily_learning_orchestrator"))
    stage_results = orchestration.get("stage_results")
    if not isinstance(stage_results, Sequence) or isinstance(stage_results, (str, bytes)):
        errors.append("stage_results_missing")
        stage_results = []
    expected_ids = [_safe_str(stage["stage_id"]) for stage in _STAGE_DEFINITIONS]
    observed_ids = [_safe_str(_as_mapping(stage).get("stage_id")) for stage in stage_results]
    if observed_ids != expected_ids:
        errors.append("stage_order_mismatch")
    for stage in stage_results:
        stage_map = _as_mapping(stage)
        stage_id = _safe_str(stage_map.get("stage_id"))
        if stage_map.get("research_only") is not True:
            errors.append(f"{stage_id}:research_only_must_be_true")
        if stage_map.get("read_only") is not True:
            errors.append(f"{stage_id}:read_only_must_be_true")
        if stage_map.get("operational_authority") is not False:
            errors.append(f"{stage_id}:operational_authority_must_be_false")
        if stage_map.get("write_performed") is True:
            errors.append(f"{stage_id}:write_performed_must_be_false")
        nested_errors = stage_map.get("validation_errors", [])
        if isinstance(nested_errors, Sequence) and not isinstance(nested_errors, (str, bytes)):
            errors.extend(str(error) for error in nested_errors)
    scope = _as_mapping(payload.get("orchestrator_scope"))
    if scope.get("orchestrates_daily_learning_loop") is not True:
        errors.append("orchestrator_scope_missing")
    if scope.get("loads_runtime_sources_by_default") is not False:
        errors.append("orchestrator_must_not_load_runtime_sources_by_default")
    if scope.get("writes_project_artifacts_by_default") is not False:
        errors.append("orchestrator_must_not_write_project_artifacts_by_default")
    return sorted(set(errors))
