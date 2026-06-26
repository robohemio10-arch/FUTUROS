"""Research-only AI Shadow feedback bridge for SMART FUTUROS.

This module converts out-of-sample validation results into audit-friendly
feedback events for future IA Shadow research. It is intentionally inert:
it does not read runtime sources, it does not write operational state, and
it never grants trading authority.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION = (
    "daily_learning_ai_shadow_feedback_bridge_v1"
)

PROJECT_NAME = "SMART FUTUROS"
STATUS_BLOCKED = "blocked"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
REPORT_REASON = "ai_shadow_feedback_bridge_research_only_without_operational_authority"

MANDATORY_FEEDBACK_BLOCKERS = [
    "research_only_feedback",
    "not_reviewed_by_operator",
    "not_bound_to_ai_shadow_runtime_contract",
    "not_approved_for_ai_shadow_runtime",
    "not_approved_for_freqtrade",
    "not_approved_for_risk_manager",
    "not_gap_free_soak_validated",
    "live_canary_blocked",
]

ALLOWED_NEXT_STEPS = [
    "criar Qlib research dataset em branch futura",
    "criar daily learning orchestrator em branch futura",
    "criar scheduler paper em branch futura",
    "criar dashboard daily learning command center em branch futura",
    "criar evidence readiness integration em branch futura",
]

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
    "usar feedback bridge para liberar operacao",
    "promover regra candidata",
    "aplicar candidate rule",
    "registrar em registry operacional",
    "alterar IA Shadow runtime com feedback",
    "escrever banco local IA Shadow",
    "atualizar threshold IA Shadow",
    "gerar codigo operacional de veto",
]

SAFETY_FALSE_FLAGS = [
    "operational_authority",
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "can_promote_rules",
    "can_promote_model",
    "live_trading_enabled",
    "canary_release_allowed",
    "live_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "updates_freqtrade",
    "updates_risk_manager",
    "updates_qlib_runtime",
    "updates_ai_shadow_runtime",
    "writes_runtime",
    "writes_data",
    "writes_sqlite",
    "writes_parquet",
    "runs_training",
    "runs_ocr",
    "runs_ai_shadow_incremental",
    "applies_shadow_rules",
    "promotes_shadow_rules",
    "applies_feedback_to_ai_shadow",
    "writes_ai_shadow_sqlite",
    "updates_ai_shadow_thresholds",
    "updates_ai_shadow_policy",
]

SAFETY_TRUE_FLAGS = [
    "research_only",
    "paper_only",
    "shadow_only",
    "read_only",
]


def _safe_str(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _slug(value: Any) -> str:
    text = _safe_str(value, "unknown").lower()
    chars: list[str] = []
    for char in text:
        if char.isalnum():
            chars.append(char)
        elif char in {"/", "-", "_", ":", ".", " "}:
            chars.append("_")
    collapsed = "_".join(part for part in "".join(chars).split("_") if part)
    return collapsed or "unknown"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    return [value]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(values: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if values is None:
        return []
    return [item for item in values if isinstance(item, Mapping)]


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        result = float(value)
        if result != result:
            return default
        return result
    if isinstance(value, str):
        try:
            result = float(value.strip())
            if result != result:
                return default
            return result
        except ValueError:
            return default
    return default


def _clamp01(value: Any) -> float:
    numeric = _as_float(value, 0.0)
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _dedupe_preserve_order(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _safe_str(value, "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _candidate_rule_index(candidate_rules: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for candidate in _mapping_list(candidate_rules):
        candidate_id = _safe_str(candidate.get("candidate_rule_id"), "")
        if candidate_id:
            indexed[candidate_id] = candidate
    return indexed


def _safety_flags() -> dict[str, bool]:
    payload = {key: False for key in SAFETY_FALSE_FLAGS}
    payload.update({key: True for key in SAFETY_TRUE_FLAGS})
    return payload


def _operator_decision() -> dict[str, bool | str]:
    return {
        "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "freqtrade_strategy_change_allowed": False,
        "risk_manager_change_allowed": False,
        "model_promotion_allowed": False,
        "shadow_rule_promotion_allowed": False,
        "ai_shadow_feedback_application_allowed": False,
    }


def _feedback_scope() -> dict[str, bool]:
    return {
        "builds_ai_shadow_feedback": True,
        "research_feedback_only": True,
        "feedback_record_only": True,
        "uses_only_in_memory_inputs": True,
        "applies_feedback_to_ai_shadow": False,
        "writes_ai_shadow_sqlite": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "updates_ai_shadow_policy": False,
        "applies_shadow_rules": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_models": False,
        "promotes_rules": False,
        "writes_reports": False,
        "writes_runtime": False,
        "writes_data": False,
    }


def _readiness_policy() -> dict[str, bool]:
    return {
        "feedback_bridge_is_not_readiness_evidence": True,
        "feedback_bridge_outputs_do_not_release_live": True,
        "feedback_bridge_outputs_do_not_release_canary": True,
        "manual_go_no_go_required": True,
        "candidate_rules_require_runtime_contract_binding": True,
        "candidate_rules_require_30_day_gap_free_soak": True,
        "candidate_rules_require_operator_review": True,
        "thirty_day_gap_free_soak_required_for_future_canary_review": True,
    }


def classify_feedback_event(
    oos_result: Mapping[str, Any],
    candidate_rule: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one OOS result into an inert research feedback category."""

    result = _as_mapping(oos_result)
    candidate = _as_mapping(candidate_rule)
    candidate_id = _safe_str(
        result.get("candidate_rule_id") or candidate.get("candidate_rule_id"),
        "",
    )
    oos_status = _safe_str(result.get("oos_status"), "unknown")
    rule_kind = _safe_str(result.get("rule_kind") or candidate.get("rule_kind"), "unknown")
    oos_confidence = _clamp01(
        result.get("out_of_sample_confidence", candidate.get("confidence", 0.0))
    )

    reason_codes: list[str] = []

    if not result or not candidate_id:
        reason_codes.extend(["missing_oos_result_or_candidate_id", "manual_review_required"])
        return {
            "feedback_type": "insufficient_evidence",
            "feedback_direction": "none",
            "feedback_confidence": 0.0,
            "review_required": True,
            "reason_codes": reason_codes,
        }

    if oos_status == "oos_research_pass" and rule_kind == "block_candidate":
        reason_codes.extend(["oos_passed", "block_candidate_research_signal"])
        return {
            "feedback_type": "candidate_negative_signal",
            "feedback_direction": "reinforce",
            "feedback_confidence": oos_confidence,
            "review_required": True,
            "reason_codes": reason_codes,
        }

    if oos_status == "oos_research_pass" and rule_kind == "allow_candidate":
        reason_codes.extend(["oos_passed", "allow_candidate_research_signal"])
        return {
            "feedback_type": "candidate_positive_signal",
            "feedback_direction": "reinforce",
            "feedback_confidence": oos_confidence,
            "review_required": True,
            "reason_codes": reason_codes,
        }

    if oos_status == "oos_research_fail":
        reason_codes.extend(["oos_failed", "candidate_requires_deprioritization_review"])
        return {
            "feedback_type": "needs_review",
            "feedback_direction": "deprioritize",
            "feedback_confidence": oos_confidence,
            "review_required": True,
            "reason_codes": reason_codes,
        }

    if oos_status in {"insufficient_oos_support", "no_oos_data"}:
        reason_codes.extend([oos_status, "insufficient_research_evidence"])
        return {
            "feedback_type": "insufficient_evidence",
            "feedback_direction": "observe",
            "feedback_confidence": oos_confidence,
            "review_required": True,
            "reason_codes": reason_codes,
        }

    reason_codes.extend(["observe_only_default", f"oos_status_{_slug(oos_status)}"])
    return {
        "feedback_type": "observe_only",
        "feedback_direction": "observe",
        "feedback_confidence": oos_confidence,
        "review_required": True,
        "reason_codes": reason_codes,
    }


def build_feedback_event(
    oos_result: Mapping[str, Any],
    candidate_rule: Mapping[str, Any] | None = None,
    index: int = 0,
) -> dict[str, Any]:
    """Build one record-only IA Shadow research feedback event."""

    result = _as_mapping(oos_result)
    candidate = _as_mapping(candidate_rule)
    classification = classify_feedback_event(result, candidate)

    candidate_id = _safe_str(
        result.get("candidate_rule_id") or candidate.get("candidate_rule_id"),
        "missing_candidate_id",
    )
    target = _safe_str(result.get("target") or candidate.get("target"), "unknown")
    rule_kind = _safe_str(result.get("rule_kind") or candidate.get("rule_kind"), "unknown")
    conditions = [str(item) for item in _as_list(result.get("conditions") or candidate.get("conditions"))]
    oos_status = _safe_str(result.get("oos_status"), "unknown")

    metric_keys = [
        "in_sample_count",
        "out_of_sample_count",
        "in_sample_match_count",
        "out_of_sample_match_count",
        "in_sample_target_match_count",
        "out_of_sample_target_match_count",
        "in_sample_confidence",
        "out_of_sample_confidence",
        "in_sample_baseline_rate",
        "out_of_sample_baseline_rate",
        "in_sample_lift",
        "out_of_sample_lift",
        "confidence_degradation",
        "support_status",
        "oos_status",
        "research_validation_passed",
    ]
    research_payload = {key: result.get(key) for key in metric_keys if key in result}
    research_payload.update(
        {
            "source_candidate_status": candidate.get("candidate_status"),
            "source_registry_status": candidate.get("registry_status"),
            "source_application_status": candidate.get("application_status"),
            "source_promotion_status": candidate.get("promotion_status"),
        }
    )

    inherited_blockers = _as_list(result.get("blockers")) + _as_list(candidate.get("blockers"))
    blockers = _dedupe_preserve_order(MANDATORY_FEEDBACK_BLOCKERS + inherited_blockers)

    return {
        "feedback_event_id": f"ai_shadow_feedback_{index:04d}_{_slug(candidate_id)}",
        "source": "daily_learning_oos_validation",
        "candidate_rule_id": candidate_id,
        "target": target,
        "rule_kind": rule_kind,
        "conditions": conditions,
        "oos_status": oos_status,
        "feedback_type": classification["feedback_type"],
        "feedback_direction": classification["feedback_direction"],
        "feedback_confidence": classification["feedback_confidence"],
        "review_required": True,
        "reason_codes": classification["reason_codes"],
        "research_payload": research_payload,
        "feedback_status": "record_only",
        "feedback_application_status": "not_applied",
        "promotion_status": "blocked",
        "ai_shadow_runtime_update_allowed": False,
        "ai_shadow_sqlite_write_allowed": False,
        "ai_shadow_threshold_update_allowed": False,
        "ai_shadow_policy_update_allowed": False,
        "operational_action_allowed": False,
        "promotion_allowed": False,
        "requires_manual_go_no_go": True,
        "requires_30_day_gap_free_soak": True,
        "requires_runtime_contract_binding": True,
        "writes_runtime": False,
        "writes_data": False,
        "blockers": blockers,
    }


def summarize_feedback_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate feedback events by stable audit dimensions."""

    rows = _mapping_list(events)
    return {
        "feedback_event_count": len(rows),
        "feedback_type_counts": dict(Counter(_safe_str(row.get("feedback_type")) for row in rows)),
        "feedback_direction_counts": dict(
            Counter(_safe_str(row.get("feedback_direction")) for row in rows)
        ),
        "rule_kind_counts": dict(Counter(_safe_str(row.get("rule_kind")) for row in rows)),
        "target_counts": dict(Counter(_safe_str(row.get("target")) for row in rows)),
        "review_required_count": sum(1 for row in rows if row.get("review_required") is True),
        "record_only_count": sum(1 for row in rows if row.get("feedback_status") == "record_only"),
        "not_applied_count": sum(
            1 for row in rows if row.get("feedback_application_status") == "not_applied"
        ),
    }


def build_ai_shadow_feedback_bridge(
    oos_validation_results: Sequence[Mapping[str, Any]],
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a record-only feedback bridge payload from in-memory rows."""

    results = _mapping_list(oos_validation_results)
    candidates_by_id = _candidate_rule_index(candidate_rules)
    events = [
        build_feedback_event(
            oos_result=result,
            candidate_rule=candidates_by_id.get(_safe_str(result.get("candidate_rule_id"), "")),
            index=index,
        )
        for index, result in enumerate(results)
    ]

    quality_notes = [
        "research_feedback_only",
        "feedback_events_are_record_only",
        "feedback_does_not_update_ai_shadow_runtime",
        "no_ai_shadow_local_store_write",
        "requires_operator_review_and_runtime_contract",
    ]
    if not results:
        quality_notes.append("no_oos_validation_results_available")
    if candidate_rules is None:
        quality_notes.append("candidate_rules_not_loaded")
    if catalog_entries is None:
        quality_notes.append("catalog_entries_not_loaded")
    if feature_rows is None:
        quality_notes.append("feature_rows_not_loaded")

    summary = summarize_feedback_events(events)
    return {
        "feedback_event_count": len(events),
        "feedback_events": events,
        "feedback_events_sample": events[:20],
        "feedback_summary": summary,
        "feedback_scope": _feedback_scope(),
        "feedback_quality_notes": quality_notes,
        "input_counts": {
            "oos_validation_result_count": len(results),
            "candidate_rule_count": len(_mapping_list(candidate_rules)),
            "catalog_entry_count": len(_mapping_list(catalog_entries)),
            "feature_row_count": len(_mapping_list(feature_rows)),
        },
    }


def build_daily_learning_ai_shadow_feedback_bridge_report(
    project_root: str | Path | None = None,
    oos_validation_results: Sequence[Mapping[str, Any]] | None = None,
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the Branch 11 report without reading runtime sources."""

    all_inputs_none = (
        oos_validation_results is None
        and candidate_rules is None
        and catalog_entries is None
        and feature_rows is None
    )
    input_mode = "no_runtime_rows_loaded" if all_inputs_none else "in_memory_inputs"

    bridge = build_ai_shadow_feedback_bridge(
        oos_validation_results=[] if oos_validation_results is None else oos_validation_results,
        candidate_rules=candidate_rules,
        catalog_entries=catalog_entries,
        feature_rows=feature_rows,
    )

    report: dict[str, Any] = {
        "schema_version": DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root) if project_root is not None else None,
        "status": STATUS_BLOCKED,
        "decision": DECISION_RESEARCH,
        "reason": REPORT_REASON,
        "input_mode": input_mode,
        "feedback_bridge": bridge,
        "feedback_summary": bridge["feedback_summary"],
        "feedback_scope": bridge["feedback_scope"],
        "readiness_policy": _readiness_policy(),
        "allowed_next_steps": ALLOWED_NEXT_STEPS,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "operator_decision": _operator_decision(),
        "validation_errors": [],
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
    }
    report.update(_safety_flags())
    report["validation_errors"] = validate_daily_learning_ai_shadow_feedback_bridge_report(report)
    return report


def _expect_false(payload: Mapping[str, Any], key: str, errors: list[str]) -> None:
    if payload.get(key) is not False:
        errors.append(f"{key}_must_be_false")


def _expect_true(payload: Mapping[str, Any], key: str, errors: list[str]) -> None:
    if payload.get(key) is not True:
        errors.append(f"{key}_must_be_true")


def validate_daily_learning_ai_shadow_feedback_bridge_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate report safety contract and event-level inertness."""

    errors: list[str] = []

    if payload.get("schema_version") != DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if payload.get("status") != STATUS_BLOCKED:
        errors.append("status_must_be_blocked")
    if payload.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_be_manter_em_research")

    for key in SAFETY_TRUE_FLAGS:
        _expect_true(payload, key, errors)
    for key in SAFETY_FALSE_FLAGS:
        _expect_false(payload, key, errors)

    scope = _as_mapping(payload.get("feedback_scope"))
    expected_scope = _feedback_scope()
    for key, expected_value in expected_scope.items():
        if scope.get(key) is not expected_value:
            errors.append(f"feedback_scope_{key}_must_be_{str(expected_value).lower()}")

    bridge = _as_mapping(payload.get("feedback_bridge"))
    events = _mapping_list(bridge.get("feedback_events"))  # type: ignore[arg-type]
    for index, event in enumerate(events):
        prefix = f"feedback_event_{index}"
        if event.get("feedback_status") != "record_only":
            errors.append(f"{prefix}_feedback_status_must_be_record_only")
        if event.get("feedback_application_status") != "not_applied":
            errors.append(f"{prefix}_feedback_application_status_must_be_not_applied")
        event_false_flags = [
            "ai_shadow_runtime_update_allowed",
            "ai_shadow_sqlite_write_allowed",
            "ai_shadow_threshold_update_allowed",
            "ai_shadow_policy_update_allowed",
            "operational_action_allowed",
            "promotion_allowed",
            "writes_runtime",
            "writes_data",
        ]
        for key in event_false_flags:
            if event.get(key) is not False:
                errors.append(f"{prefix}_{key}_must_be_false")
        if event.get("review_required") is not True:
            errors.append(f"{prefix}_review_required_must_be_true")
        if event.get("requires_manual_go_no_go") is not True:
            errors.append(f"{prefix}_requires_manual_go_no_go_must_be_true")
        if event.get("requires_30_day_gap_free_soak") is not True:
            errors.append(f"{prefix}_requires_30_day_gap_free_soak_must_be_true")
        blockers = set(str(item) for item in _as_list(event.get("blockers")))
        missing_blockers = [item for item in MANDATORY_FEEDBACK_BLOCKERS if item not in blockers]
        if missing_blockers:
            errors.append(f"{prefix}_missing_mandatory_blockers")

    if payload.get("write_performed") is not False:
        errors.append("write_performed_must_be_false_by_report_builder")

    return errors


__all__ = [
    "DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION",
    "build_ai_shadow_feedback_bridge",
    "build_daily_learning_ai_shadow_feedback_bridge_report",
    "build_feedback_event",
    "classify_feedback_event",
    "summarize_feedback_events",
    "validate_daily_learning_ai_shadow_feedback_bridge_report",
]
