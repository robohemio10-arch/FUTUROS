"""Research-only registry for candidate shadow rules."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_pattern_mining_research import mine_descriptive_patterns


DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_SCHEMA_VERSION = (
    "daily_candidate_shadow_rule_registry_v1"
)
DEFAULT_MIN_SUPPORT_COUNT = 2
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_LIFT = 1.0
MAX_CANDIDATE_SAMPLE = 20

ALLOWED_TARGETS = {
    "mistake",
    "winner",
    "stop_loss_loss",
    "fast_loss_under_30m",
    "profitable_trade",
}

MANDATORY_BLOCKERS = [
    "research_only_candidate",
    "not_oos_validated",
    "not_reviewed_by_operator",
    "not_gap_free_soak_validated",
    "not_bound_to_runtime_contract",
    "not_approved_for_ai_shadow_runtime",
    "not_approved_for_freqtrade",
    "not_approved_for_risk_manager",
    "live_canary_blocked",
]

REGISTRY_SCOPE: dict[str, bool] = {
    "creates_candidate_rules": True,
    "registers_candidate_rules": True,
    "research_registry_only": True,
    "applies_candidate_rules": False,
    "updates_ai_shadow_runtime": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "runs_oos_validation": False,
    "promotes_rules": False,
    "uses_only_in_memory_inputs": True,
    "uses_net_pnl_as_feature": False,
    "writes_reports": False,
    "writes_runtime": False,
    "writes_data": False,
}

READINESS_POLICY: dict[str, bool] = {
    "candidate_registry_is_not_readiness_evidence": True,
    "candidate_registry_outputs_do_not_release_live": True,
    "candidate_registry_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "candidate_rules_require_oos_validation_branch": True,
    "candidate_rules_require_ai_shadow_feedback_bridge": True,
    "candidate_rules_require_30_day_gap_free_soak": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar OOS validation em branch futura",
    "criar AI Shadow feedback bridge em branch futura",
    "criar Qlib research dataset em branch futura",
    "criar daily learning orchestrator em branch futura",
    "criar dashboard daily learning command center em branch futura",
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
    "usar candidate registry para liberar operacao",
    "promover regra candidata",
    "aplicar candidate rule",
    "registrar em registry operacional",
    "rodar OOS validation nesta branch",
    "gerar codigo operacional de veto",
]

EXTRA_BRANCH_FLAGS: dict[str, bool] = {
    "runs_oos_validation": False,
    "applies_shadow_rules": False,
    "promotes_shadow_rules": False,
}


def build_daily_candidate_shadow_rule_registry_report(
    project_root: str | Path | None = None,
    patterns: Sequence[Mapping[str, Any]] | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    min_support_count: int = DEFAULT_MIN_SUPPORT_COUNT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_lift: float = DEFAULT_MIN_LIFT,
) -> dict[str, Any]:
    """Build the blocked research registry report without reading real sources."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    if patterns is None and catalog_entries is None and feature_rows is None:
        pattern_rows: list[Mapping[str, Any]] = []
        input_mode = "no_runtime_rows_loaded"
    elif patterns is not None:
        pattern_rows = list(patterns)
        input_mode = "in_memory_candidate_registry_inputs"
    else:
        mined = mine_descriptive_patterns(
            [] if catalog_entries is None else catalog_entries,
            feature_rows,
            min_support_count=min_support_count,
            min_confidence=min_confidence,
        )
        pattern_rows = list(mined["patterns"])
        input_mode = "in_memory_candidate_registry_inputs"

    registry = build_candidate_shadow_rule_registry(
        pattern_rows,
        min_support_count=min_support_count,
        min_confidence=min_confidence,
        min_lift=min_lift,
    )
    payload: dict[str, Any] = {
        "schema_version": DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": (
            "candidate_shadow_rule_registry_research_only_without_operational_authority"
        ),
        "project_root": str(root),
        **SAFETY_FLAGS,
        **EXTRA_BRANCH_FLAGS,
        "input_mode": input_mode,
        "min_support_count": max(1, int(min_support_count)),
        "min_confidence": _bounded_confidence(min_confidence),
        "min_lift": max(0.0, _to_float(min_lift) or DEFAULT_MIN_LIFT),
        "candidate_registry": registry,
        "registry_summary": _registry_summary(registry),
        "registry_scope": dict(REGISTRY_SCOPE),
        "readiness_policy": dict(READINESS_POLICY),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "write_requested": False,
        "write_performed": False,
    }
    payload["validation_errors"] = validate_daily_candidate_shadow_rule_registry_report(
        payload,
    )
    return payload


def build_candidate_shadow_rule_registry(
    patterns: Sequence[Mapping[str, Any]],
    min_support_count: int = DEFAULT_MIN_SUPPORT_COUNT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_lift: float = DEFAULT_MIN_LIFT,
) -> dict[str, Any]:
    """Convert eligible in-memory patterns into blocked research candidates."""
    min_support = max(1, int(min_support_count))
    confidence_floor = _bounded_confidence(min_confidence)
    lift_floor = max(0.0, _to_float(min_lift) or DEFAULT_MIN_LIFT)
    candidate_rules: list[dict[str, Any]] = []
    rejected_patterns: list[dict[str, Any]] = []
    for index, pattern in enumerate(patterns, start=1):
        rejection_reasons = _pattern_rejection_reasons(
            pattern,
            min_support,
            confidence_floor,
            lift_floor,
        )
        if rejection_reasons:
            rejected_patterns.append(
                {
                    "pattern_id": str(pattern.get("pattern_id") or f"pattern_{index}"),
                    "target": str(pattern.get("target") or "unknown"),
                    "conditions": list(pattern.get("conditions") or []),
                    "rejection_reasons": rejection_reasons,
                }
            )
            continue
        candidate_rules.append(pattern_to_candidate_shadow_rule(pattern, index))

    return {
        "candidate_count": len(candidate_rules),
        "rejected_pattern_count": len(rejected_patterns),
        "candidate_rules": candidate_rules,
        "candidate_rules_sample": candidate_rules[:MAX_CANDIDATE_SAMPLE],
        "rejected_patterns_sample": rejected_patterns[:MAX_CANDIDATE_SAMPLE],
        "candidate_counts_by_rule_kind": _counter(candidate_rules, "rule_kind"),
        "candidate_counts_by_target": _counter(candidate_rules, "target"),
        "registry_scope": dict(REGISTRY_SCOPE),
        "registry_quality_notes": _registry_quality_notes(
            len(patterns),
            len(candidate_rules),
            len(rejected_patterns),
            min_support,
            confidence_floor,
            lift_floor,
        ),
    }


def pattern_to_candidate_shadow_rule(
    pattern: Mapping[str, Any],
    index: int = 0,
) -> dict[str, Any]:
    """Convert one descriptive pattern into a blocked research candidate."""
    source_pattern_id = str(pattern.get("pattern_id") or f"pattern_{index}")
    target = str(pattern.get("target") or "unknown")
    candidate: dict[str, Any] = {
        "candidate_rule_id": f"research_shadow_rule_{index:04d}_{source_pattern_id}",
        "source_pattern_id": source_pattern_id,
        "source_pattern_type": str(pattern.get("pattern_type") or "unknown"),
        "target": target,
        "conditions": list(pattern.get("conditions") or []),
        "support_count": int(pattern.get("support_count") or 0),
        "target_count": int(pattern.get("target_count") or 0),
        "confidence": _round_metric(pattern.get("confidence")),
        "baseline_rate": _round_metric(pattern.get("baseline_rate")),
        "lift": _round_metric(pattern.get("lift")),
        "coverage_pct": _round_metric(pattern.get("coverage_pct")),
        "rule_family": "shadow_filter_candidate",
        "rule_kind": _rule_kind_for_target(target),
        "candidate_status": "research_candidate",
        "registry_status": "registered_research_only",
        "promotion_status": "blocked",
        "application_status": "not_applied",
        "blockers": [],
        "safety_flags": {
            **SAFETY_FLAGS,
            **EXTRA_BRANCH_FLAGS,
            "applies_to_runtime": False,
            "applies_to_freqtrade": False,
            "applies_to_risk_manager": False,
            "applies_to_ai_shadow_runtime": False,
        },
        "research_interpretation": str(pattern.get("research_interpretation") or ""),
        "creates_candidate_rule": True,
        "registers_candidate_rule": True,
        "operational_action_allowed": False,
        "applies_to_runtime": False,
        "applies_to_freqtrade": False,
        "applies_to_risk_manager": False,
        "applies_to_ai_shadow_runtime": False,
        "requires_oos_validation": True,
        "requires_manual_go_no_go": True,
        "requires_30_day_gap_free_soak": True,
        "promotion_allowed": False,
        "writes_runtime": False,
        "writes_data": False,
    }
    candidate["blockers"] = calculate_candidate_blockers(candidate)
    return candidate


def calculate_candidate_blockers(candidate: Mapping[str, Any]) -> list[str]:
    """Return mandatory blockers plus any contract-specific blocker."""
    blockers = list(MANDATORY_BLOCKERS)
    if candidate.get("operational_action_allowed") is not False:
        blockers.append("operational_action_not_blocked")
    if candidate.get("requires_oos_validation") is not True:
        blockers.append("missing_oos_validation_requirement")
    if candidate.get("promotion_allowed") is not False:
        blockers.append("promotion_not_blocked")
    if candidate.get("applies_to_ai_shadow_runtime") is not False:
        blockers.append("ai_shadow_runtime_binding_not_blocked")
    return list(dict.fromkeys(blockers))


def validate_daily_candidate_shadow_rule_registry_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the candidate registry research contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": (
            "candidate_shadow_rule_registry_research_only_without_operational_authority"
        ),
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in {**SAFETY_FLAGS, **EXTRA_BRANCH_FLAGS}.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("registry_scope"))
    for key, expected in REGISTRY_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"registry_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    registry = _mapping(payload.get("candidate_registry"))
    if not registry:
        errors.append("candidate_registry_must_be_object")
        return errors
    candidates = registry.get("candidate_rules")
    if not isinstance(candidates, Sequence) or isinstance(candidates, str):
        errors.append("candidate_rules_must_be_list")
        return errors
    for index, candidate in enumerate(candidates):
        item = _mapping(candidate)
        if item.get("operational_action_allowed") is not False:
            errors.append(f"candidate_{index}_operational_action_allowed_must_be_false")
        if item.get("requires_oos_validation") is not True:
            errors.append(f"candidate_{index}_requires_oos_validation_must_be_true")
        if item.get("promotion_allowed") is not False:
            errors.append(f"candidate_{index}_promotion_allowed_must_be_false")
        if item.get("applies_to_ai_shadow_runtime") is not False:
            errors.append(f"candidate_{index}_ai_shadow_runtime_must_be_false")
        if item.get("application_status") != "not_applied":
            errors.append(f"candidate_{index}_application_status_mismatch")
        for blocker in MANDATORY_BLOCKERS:
            if blocker not in item.get("blockers", []):
                errors.append(f"candidate_{index}_missing_blocker:{blocker}")
    return errors


def _pattern_rejection_reasons(
    pattern: Mapping[str, Any],
    min_support: int,
    min_confidence: float,
    min_lift: float,
) -> list[str]:
    reasons: list[str] = []
    if int(pattern.get("support_count") or 0) < min_support:
        reasons.append("support_below_minimum")
    if _to_float(pattern.get("confidence")) is None:
        reasons.append("missing_confidence")
    elif float(pattern.get("confidence") or 0.0) < min_confidence:
        reasons.append("confidence_below_minimum")
    if _to_float(pattern.get("lift")) is None:
        reasons.append("missing_lift")
    elif float(pattern.get("lift") or 0.0) < min_lift:
        reasons.append("lift_below_minimum")
    if str(pattern.get("target") or "") not in ALLOWED_TARGETS:
        reasons.append("target_not_allowed")
    if pattern.get("operational_action_allowed") is not False:
        reasons.append("pattern_operational_action_not_blocked")
    if pattern.get("promotion_allowed") is not False:
        reasons.append("pattern_promotion_not_blocked")
    return reasons


def _rule_kind_for_target(target: str) -> str:
    if target in {"mistake", "stop_loss_loss", "fast_loss_under_30m"}:
        return "block_candidate"
    if target in {"winner", "profitable_trade"}:
        return "allow_candidate"
    return "observe_only_candidate"


def _registry_summary(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_count": int(registry.get("candidate_count") or 0),
        "rejected_pattern_count": int(registry.get("rejected_pattern_count") or 0),
        "candidate_counts_by_rule_kind": dict(
            _mapping(registry.get("candidate_counts_by_rule_kind"))
        ),
        "candidate_counts_by_target": dict(
            _mapping(registry.get("candidate_counts_by_target"))
        ),
    }


def _registry_quality_notes(
    pattern_count: int,
    candidate_count: int,
    rejected_count: int,
    min_support: int,
    min_confidence: float,
    min_lift: float,
) -> list[str]:
    notes = [
        "research_registry_only",
        "candidates_are_not_runtime_rules",
        "requires_future_oos_validation",
        "requires_manual_go_no_go",
        f"min_support_count={min_support}",
        f"min_confidence={min_confidence:.6f}",
        f"min_lift={min_lift:.6f}",
    ]
    if pattern_count == 0:
        notes.append("no_patterns_available")
    if candidate_count == 0:
        notes.append("no_candidates_passed_filters")
    if rejected_count > 0:
        notes.append("some_patterns_rejected_by_filters")
    return notes


def _counter(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        value = item.get(key)
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def _bounded_confidence(value: float) -> float:
    number = _to_float(value)
    if number is None:
        return DEFAULT_MIN_CONFIDENCE
    return min(1.0, max(0.0, number))


def _round_metric(value: Any) -> float:
    number = _to_float(value)
    return round(0.0 if number is None else number, 6)


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
