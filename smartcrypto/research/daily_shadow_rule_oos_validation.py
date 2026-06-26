"""Research-only OOS validation for candidate shadow rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_candidate_shadow_rule_registry import (
    calculate_candidate_blockers,
)
from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_pattern_mining_research import build_feature_bins


DAILY_SHADOW_RULE_OOS_VALIDATION_SCHEMA_VERSION = (
    "daily_shadow_rule_oos_validation_v1"
)
DEFAULT_OOS_FRACTION = 0.35
DEFAULT_MIN_OOS_SUPPORT_COUNT = 2
DEFAULT_MIN_OOS_CONFIDENCE = 0.5
DEFAULT_MAX_CONFIDENCE_DEGRADATION = 0.25
MAX_VALIDATION_SAMPLE = 20

MANDATORY_OOS_BLOCKERS = [
    "research_only_oos_validation",
    "not_reviewed_by_operator",
    "not_bound_to_runtime_contract",
    "not_approved_for_ai_shadow_runtime",
    "not_approved_for_freqtrade",
    "not_approved_for_risk_manager",
    "not_gap_free_soak_validated",
    "live_canary_blocked",
]

VALIDATION_SCOPE: dict[str, bool] = {
    "runs_oos_validation": True,
    "research_oos_only": True,
    "uses_only_in_memory_inputs": True,
    "uses_net_pnl_as_feature": False,
    "applies_candidate_rules": False,
    "updates_ai_shadow_runtime": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "promotes_rules": False,
    "writes_reports": False,
    "writes_runtime": False,
    "writes_data": False,
}

READINESS_POLICY: dict[str, bool] = {
    "oos_validation_is_not_readiness_evidence": True,
    "oos_validation_outputs_do_not_release_live": True,
    "oos_validation_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "candidate_rules_require_ai_shadow_feedback_bridge": True,
    "candidate_rules_require_30_day_gap_free_soak": True,
    "candidate_rules_require_runtime_contract_binding": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar AI Shadow feedback bridge em branch futura",
    "criar Qlib research dataset em branch futura",
    "criar daily learning orchestrator em branch futura",
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
    "usar OOS validation para liberar operacao",
    "promover regra candidata",
    "aplicar candidate rule",
    "registrar em registry operacional",
    "alterar IA Shadow runtime com regra validada",
    "gerar codigo operacional de veto",
]

EXTRA_BRANCH_FLAGS: dict[str, bool] = {
    "runs_oos_validation": True,
    "applies_shadow_rules": False,
    "promotes_shadow_rules": False,
}


def build_daily_shadow_rule_oos_validation_report(
    project_root: str | Path | None = None,
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    oos_fraction: float = DEFAULT_OOS_FRACTION,
    min_oos_support_count: int = DEFAULT_MIN_OOS_SUPPORT_COUNT,
    min_oos_confidence: float = DEFAULT_MIN_OOS_CONFIDENCE,
    max_confidence_degradation: float = DEFAULT_MAX_CONFIDENCE_DEGRADATION,
) -> dict[str, Any]:
    """Build a blocked OOS research report without reading runtime sources."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    if candidate_rules is None and catalog_entries is None and feature_rows is None:
        rules: list[Mapping[str, Any]] = []
        entries: list[Mapping[str, Any]] = []
        input_mode = "no_runtime_rows_loaded"
    else:
        rules = [] if candidate_rules is None else list(candidate_rules)
        entries = [] if catalog_entries is None else list(catalog_entries)
        input_mode = "in_memory_oos_validation_inputs"

    validation = evaluate_candidate_rules_oos(
        rules,
        entries,
        feature_rows,
        oos_fraction=oos_fraction,
        min_oos_support_count=min_oos_support_count,
        min_oos_confidence=min_oos_confidence,
        max_confidence_degradation=max_confidence_degradation,
    )
    payload: dict[str, Any] = {
        "schema_version": DAILY_SHADOW_RULE_OOS_VALIDATION_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "shadow_rule_oos_validation_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        **EXTRA_BRANCH_FLAGS,
        "input_mode": input_mode,
        "oos_fraction": _bounded_oos_fraction(oos_fraction),
        "min_oos_support_count": max(1, int(min_oos_support_count)),
        "min_oos_confidence": _bounded_confidence(min_oos_confidence),
        "max_confidence_degradation": _bounded_confidence(
            max_confidence_degradation,
        ),
        "oos_validation": validation,
        "oos_summary": _oos_summary(validation),
        "validation_scope": dict(VALIDATION_SCOPE),
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
    payload["validation_errors"] = validate_daily_shadow_rule_oos_validation_report(
        payload,
    )
    return payload


def split_entries_for_oos(
    entries: Sequence[Mapping[str, Any]],
    oos_fraction: float = DEFAULT_OOS_FRACTION,
) -> dict[str, Any]:
    """Split entries deterministically, using the temporal tail as OOS."""
    rows = [
        {"index": index, "entry": entry}
        for index, entry in enumerate(entries)
    ]
    fraction = _bounded_oos_fraction(oos_fraction)
    total = len(rows)
    if total < 2:
        return {
            "entry_count": total,
            "in_sample_count": total,
            "out_of_sample_count": 0,
            "oos_fraction": fraction,
            "split_method": "deterministic_temporal_tail",
            "split_status": "insufficient_entries",
            "in_sample_entries": [row["entry"] for row in rows],
            "out_of_sample_entries": [],
        }
    rows.sort(key=_split_sort_key)
    oos_count = max(1, min(total - 1, round(total * fraction)))
    in_sample = rows[: total - oos_count]
    out_of_sample = rows[total - oos_count :]
    return {
        "entry_count": total,
        "in_sample_count": len(in_sample),
        "out_of_sample_count": len(out_of_sample),
        "oos_fraction": fraction,
        "split_method": "deterministic_temporal_tail",
        "split_status": "ok",
        "in_sample_entries": [row["entry"] for row in in_sample],
        "out_of_sample_entries": [row["entry"] for row in out_of_sample],
    }


def evaluate_candidate_rule_oos(
    candidate_rule: Mapping[str, Any],
    in_sample_entries: Sequence[Mapping[str, Any]],
    out_of_sample_entries: Sequence[Mapping[str, Any]],
    feature_rows_by_trade_id: Mapping[str, Mapping[str, Any]] | None = None,
    min_oos_support_count: int = DEFAULT_MIN_OOS_SUPPORT_COUNT,
    min_oos_confidence: float = DEFAULT_MIN_OOS_CONFIDENCE,
    max_confidence_degradation: float = DEFAULT_MAX_CONFIDENCE_DEGRADATION,
) -> dict[str, Any]:
    """Evaluate one candidate rule in-sample and out-of-sample."""
    features = _mapping(feature_rows_by_trade_id)
    target = str(candidate_rule.get("target") or "")
    in_metrics = _rule_metrics(candidate_rule, target, in_sample_entries, features)
    out_metrics = _rule_metrics(candidate_rule, target, out_of_sample_entries, features)
    in_conf = float(in_metrics["confidence"])
    out_conf = float(out_metrics["confidence"])
    confidence_degradation = max(0.0, in_conf - out_conf)
    min_support = max(1, int(min_oos_support_count))
    min_conf = _bounded_confidence(min_oos_confidence)
    max_degradation = _bounded_confidence(max_confidence_degradation)

    if len(out_of_sample_entries) == 0:
        oos_status = "no_oos_data"
        support_status = "no_oos_data"
    elif int(out_metrics["match_count"]) < min_support:
        oos_status = "insufficient_oos_support"
        support_status = "insufficient_oos_support"
    elif (
        out_conf >= min_conf
        and confidence_degradation <= max_degradation
        and float(out_metrics["lift"]) >= 1.0
    ):
        oos_status = "oos_research_pass"
        support_status = "oos_support_ok"
    else:
        oos_status = "oos_research_fail"
        support_status = "oos_support_ok"

    result: dict[str, Any] = {
        "candidate_rule_id": str(candidate_rule.get("candidate_rule_id") or "unknown"),
        "target": target,
        "rule_kind": str(candidate_rule.get("rule_kind") or "unknown"),
        "conditions": list(candidate_rule.get("conditions") or []),
        "in_sample_count": len(in_sample_entries),
        "out_of_sample_count": len(out_of_sample_entries),
        "in_sample_match_count": in_metrics["match_count"],
        "out_of_sample_match_count": out_metrics["match_count"],
        "in_sample_target_match_count": in_metrics["target_match_count"],
        "out_of_sample_target_match_count": out_metrics["target_match_count"],
        "in_sample_confidence": in_metrics["confidence"],
        "out_of_sample_confidence": out_metrics["confidence"],
        "in_sample_baseline_rate": in_metrics["baseline_rate"],
        "out_of_sample_baseline_rate": out_metrics["baseline_rate"],
        "in_sample_lift": in_metrics["lift"],
        "out_of_sample_lift": out_metrics["lift"],
        "confidence_degradation": round(confidence_degradation, 6),
        "support_status": support_status,
        "oos_status": oos_status,
        "research_validation_passed": oos_status == "oos_research_pass",
        "promotion_status": "blocked",
        "application_status": "not_applied",
        "operational_action_allowed": False,
        "applies_to_ai_shadow_runtime": False,
        "applies_to_freqtrade": False,
        "applies_to_risk_manager": False,
        "requires_manual_go_no_go": True,
        "requires_30_day_gap_free_soak": True,
        "promotion_allowed": False,
        "blockers": [],
    }
    result["blockers"] = _result_blockers(candidate_rule, result)
    return result


def evaluate_candidate_rules_oos(
    candidate_rules: Sequence[Mapping[str, Any]],
    catalog_entries: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    oos_fraction: float = DEFAULT_OOS_FRACTION,
    min_oos_support_count: int = DEFAULT_MIN_OOS_SUPPORT_COUNT,
    min_oos_confidence: float = DEFAULT_MIN_OOS_CONFIDENCE,
    max_confidence_degradation: float = DEFAULT_MAX_CONFIDENCE_DEGRADATION,
) -> dict[str, Any]:
    """Evaluate candidate rules using deterministic in-memory OOS split."""
    split = split_entries_for_oos(catalog_entries, oos_fraction)
    feature_lookup = _features_by_trade_id(feature_rows)
    results = [
        evaluate_candidate_rule_oos(
            rule,
            split["in_sample_entries"],
            split["out_of_sample_entries"],
            feature_lookup,
            min_oos_support_count=min_oos_support_count,
            min_oos_confidence=min_oos_confidence,
            max_confidence_degradation=max_confidence_degradation,
        )
        for rule in candidate_rules
    ]
    pass_count = sum(1 for item in results if item["oos_status"] == "oos_research_pass")
    insufficient_count = sum(
        1
        for item in results
        if item["oos_status"] in {"no_oos_data", "insufficient_oos_support"}
    )
    fail_count = sum(1 for item in results if item["oos_status"] == "oos_research_fail")
    public_split = {
        key: value
        for key, value in split.items()
        if key not in {"in_sample_entries", "out_of_sample_entries"}
    }
    return {
        "candidate_count": len(candidate_rules),
        "validated_candidate_count": len(results),
        "oos_pass_count": pass_count,
        "oos_fail_count": fail_count,
        "insufficient_oos_count": insufficient_count,
        "validation_results": results,
        "validation_results_sample": results[:MAX_VALIDATION_SAMPLE],
        "split": public_split,
        "validation_scope": dict(VALIDATION_SCOPE),
        "validation_quality_notes": _validation_quality_notes(
            len(candidate_rules),
            len(catalog_entries),
            pass_count,
            fail_count,
            insufficient_count,
        ),
    }


def entry_matches_candidate_rule(
    candidate_rule: Mapping[str, Any],
    catalog_entry: Mapping[str, Any],
    feature_row: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether an entry contains every candidate condition bucket."""
    bucket_set = _entry_bucket_set(catalog_entry, feature_row)
    conditions = [str(item) for item in candidate_rule.get("conditions", [])]
    return bool(conditions) and all(condition in bucket_set for condition in conditions)


def entry_matches_target(target: str, catalog_entry: Mapping[str, Any]) -> bool:
    """Return whether a catalog entry satisfies a descriptive target."""
    classification = str(catalog_entry.get("classification") or "")
    subclassification = str(catalog_entry.get("subclassification") or "")
    evidence = {
        str(item)
        for item in catalog_entry.get("evidence", [])
        if item is not None
    }
    if target == "mistake":
        return classification == "mistake"
    if target == "winner":
        return classification == "winner"
    if target == "stop_loss_loss":
        return subclassification == "stop_loss_loss" or "stop_loss_loss" in evidence
    if target == "fast_loss_under_30m":
        return "fast_loss_under_30m" in evidence
    if target == "profitable_trade":
        return classification == "winner" or subclassification == "profitable_trade"
    return False


def validate_daily_shadow_rule_oos_validation_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the OOS report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_SHADOW_RULE_OOS_VALIDATION_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "shadow_rule_oos_validation_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in {**SAFETY_FLAGS, **EXTRA_BRANCH_FLAGS}.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("validation_scope"))
    for key, expected in VALIDATION_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"validation_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    validation = _mapping(payload.get("oos_validation"))
    if not validation:
        errors.append("oos_validation_must_be_object")
        return errors
    results = validation.get("validation_results")
    if not isinstance(results, Sequence) or isinstance(results, str):
        errors.append("validation_results_must_be_list")
        return errors
    for index, result in enumerate(results):
        item = _mapping(result)
        if item.get("operational_action_allowed") is not False:
            errors.append(f"result_{index}_operational_action_allowed_must_be_false")
        if item.get("promotion_allowed") is not False:
            errors.append(f"result_{index}_promotion_allowed_must_be_false")
        if item.get("promotion_status") != "blocked":
            errors.append(f"result_{index}_promotion_status_mismatch")
        if item.get("application_status") != "not_applied":
            errors.append(f"result_{index}_application_status_mismatch")
        if item.get("applies_to_ai_shadow_runtime") is not False:
            errors.append(f"result_{index}_ai_shadow_runtime_must_be_false")
        for blocker in MANDATORY_OOS_BLOCKERS:
            if blocker not in item.get("blockers", []):
                errors.append(f"result_{index}_missing_blocker:{blocker}")
    return errors


def _rule_metrics(
    candidate_rule: Mapping[str, Any],
    target: str,
    entries: Sequence[Mapping[str, Any]],
    features: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    match_count = 0
    target_match_count = 0
    baseline_target_count = 0
    for entry in entries:
        if entry_matches_target(target, entry):
            baseline_target_count += 1
        feature = features.get(str(entry.get("trade_id") or ""))
        if not entry_matches_candidate_rule(candidate_rule, entry, feature):
            continue
        match_count += 1
        if entry_matches_target(target, entry):
            target_match_count += 1
    confidence = target_match_count / match_count if match_count else 0.0
    baseline_rate = baseline_target_count / len(entries) if entries else 0.0
    lift = confidence / baseline_rate if baseline_rate else 0.0
    return {
        "match_count": match_count,
        "target_match_count": target_match_count,
        "confidence": round(confidence, 6),
        "baseline_rate": round(baseline_rate, 6),
        "lift": round(lift, 6),
    }


def _entry_bucket_set(
    catalog_entry: Mapping[str, Any],
    feature_row: Mapping[str, Any] | None,
) -> set[str]:
    merged = dict(_mapping(feature_row))
    merged.setdefault("symbol", catalog_entry.get("symbol"))
    merged.setdefault("side", catalog_entry.get("side"))
    buckets = set(build_feature_bins(merged).values())
    classification = str(catalog_entry.get("classification") or "").strip()
    if classification:
        buckets.add(f"classification_{classification}")
    subclassification = str(catalog_entry.get("subclassification") or "").strip()
    if subclassification:
        buckets.add(f"sub_{subclassification}")
    severity = str(catalog_entry.get("severity") or "").strip()
    if severity:
        buckets.add(f"severity_{severity}")
    symbol = str(catalog_entry.get("symbol") or "").strip().upper().replace("/", "")
    if symbol:
        buckets.add(f"symbol_{symbol}")
    side = _normalize_side(catalog_entry.get("side"))
    buckets.add(f"side_{side or 'unknown'}")
    for evidence in catalog_entry.get("evidence", []):
        if evidence is not None:
            buckets.add(f"evidence_{evidence}")
    return buckets


def _split_sort_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, int]:
    entry = _mapping(row.get("entry"))
    timestamp = (
        str(entry.get("entry_time") or "")
        or str(entry.get("open_time") or "")
        or str(entry.get("close_time") or "")
    )
    return (
        timestamp,
        str(entry.get("open_time") or ""),
        str(entry.get("close_time") or ""),
        str(entry.get("trade_id") or ""),
        int(row.get("index") or 0),
    )


def _result_blockers(
    candidate_rule: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[str]:
    blockers = list(MANDATORY_OOS_BLOCKERS)
    blockers.extend(calculate_candidate_blockers(candidate_rule))
    if result.get("oos_status") != "oos_research_pass":
        blockers.append(str(result.get("oos_status") or "oos_not_passed"))
    return list(dict.fromkeys(blockers))


def _features_by_trade_id(
    feature_rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in feature_rows or ():
        trade_id = row.get("trade_id")
        if trade_id is not None:
            result[str(trade_id)] = row
    return result


def _oos_summary(validation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_count": int(validation.get("candidate_count") or 0),
        "validated_candidate_count": int(
            validation.get("validated_candidate_count") or 0
        ),
        "oos_pass_count": int(validation.get("oos_pass_count") or 0),
        "oos_fail_count": int(validation.get("oos_fail_count") or 0),
        "insufficient_oos_count": int(validation.get("insufficient_oos_count") or 0),
        "split": dict(_mapping(validation.get("split"))),
    }


def _validation_quality_notes(
    candidate_count: int,
    entry_count: int,
    pass_count: int,
    fail_count: int,
    insufficient_count: int,
) -> list[str]:
    notes = [
        "research_oos_only",
        "oos_pass_does_not_promote_rule",
        "requires_future_feedback_bridge_and_runtime_contract",
    ]
    if candidate_count == 0:
        notes.append("no_candidate_rules_available")
    if entry_count < 2:
        notes.append("insufficient_entries_for_oos_split")
    if pass_count > 0:
        notes.append("some_candidates_passed_research_oos_but_remain_blocked")
    if fail_count > 0:
        notes.append("some_candidates_failed_research_oos")
    if insufficient_count > 0:
        notes.append("some_candidates_have_insufficient_oos_support")
    return notes


def _normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return None


def _bounded_oos_fraction(value: float) -> float:
    number = _to_float(value)
    if number is None:
        return DEFAULT_OOS_FRACTION
    return min(0.80, max(0.10, number))


def _bounded_confidence(value: float) -> float:
    number = _to_float(value)
    if number is None:
        return DEFAULT_MIN_OOS_CONFIDENCE
    return min(1.0, max(0.0, number))


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
