from __future__ import annotations

import math
from typing import Any

from smartcrypto.ops.dashboard_snapshots.aibot_parity_integration import (
    build_aibot_parity_dashboard_section,
)
from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.ai_training_research_command_center import (
    RESEARCH_SOURCE_PATHS,
    normalize_ai_training_research_command_center,
)
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    all_source_payloads,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    load_page_sources,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
    HardBlockStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import safe_div, safe_mean


REQUIRED_SECTIONS = (
    "model_state",
    "qlib_ranking",
    "shadow_veto",
    "decision_governance",
    "drift_regime",
    "shadow_classification_metrics",
    "reward_research",
    "model_governance",
    "ai_training_research_command_center",
    "aibot_parity",
    "audit",
)


def expected_trade_value(
    qlib_expected_return_net: float,
    shadow_probability_quality: float,
    regime_confidence: float,
    *,
    estimated_fee: float = 0.0,
    estimated_spread: float = 0.0,
    estimated_slippage: float = 0.0,
    latency_penalty: float = 0.0,
    drawdown_penalty: float = 0.0,
    drift_penalty: float = 0.0,
) -> float:
    return (
        qlib_expected_return_net * shadow_probability_quality * regime_confidence
        - estimated_fee
        - estimated_spread
        - estimated_slippage
        - latency_penalty
        - drawdown_penalty
        - drift_penalty
    )


def classification_metrics(tp: int, fp: int, tn: int, fn: int) -> dict[str, float]:
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1_score": safe_div(2.0 * precision * recall, precision + recall),
        "accuracy": safe_div(tp + tn, tp + tn + fp + fn),
    }


def calculate_brier_score(probabilities: list[float], outcomes: list[float]) -> float:
    return safe_mean((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes))


def calculate_psi(expected_pct: list[float], actual_pct: list[float]) -> float:
    terms: list[float] = []
    for expected, actual in zip(expected_pct, actual_pct):
        if expected > 0 and actual > 0:
            terms.append((actual - expected) * math.log(actual / expected))
    return sum(terms)


def classify_drift(psi: float) -> str:
    if psi > 0.25:
        return DashboardSectionStatus.BLOCKED.value
    if psi >= 0.10:
        return DashboardSectionStatus.WARNING.value
    return DashboardSectionStatus.OK.value


def promotion_gate(
    *,
    feature_contract_ok: bool,
    dataset_manifest_ok: bool,
    anti_leakage_ok: bool,
    walkforward_ok: bool,
    financial_metrics_ok: bool,
    drawdown_ok: bool,
    drift_status: str,
    event_driven_backtest_ok: bool,
    monte_carlo_ok: bool,
    rollback_pointer_exists: bool,
    manual_approval_present: bool,
) -> bool:
    return all(
        (
            feature_contract_ok,
            dataset_manifest_ok,
            anti_leakage_ok,
            walkforward_ok,
            financial_metrics_ok,
            drawdown_ok,
            drift_status != DashboardSectionStatus.BLOCKED.value,
            event_driven_backtest_ok,
            monte_carlo_ok,
            rollback_pointer_exists,
            manual_approval_present,
        )
    )


def build_ai_governance_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.ai_governance)
    operational_sources = _without_advisory_research_sources(sources)
    data = all_source_payloads(operational_sources)
    decisions = records(first_payload(operational_sources, "model_decisions"))
    if not decisions:
        decisions = records(
            first_payload(operational_sources, "ai_shadow_outcome_attribution_report")
        )
    accepts = sum(str(row.get("decision", row.get("ai_decision", ""))).upper() == "AI_ACCEPT" for row in decisions)
    rejects = sum(str(row.get("decision", row.get("ai_decision", ""))).upper() == "AI_REJECT" for row in decisions)
    total = accepts + rejects
    expected = _number_list(first_value(data, ("expected_distribution", "expected_pct"), []))
    actual = _number_list(first_value(data, ("actual_distribution", "actual_pct"), []))
    psi = finite_float(first_value(data, ("psi", "psi_score")))
    if psi is None:
        psi = calculate_psi(expected, actual)
    drift_status = classify_drift(psi)
    tp = _integer(first_value(data, ("tp", "true_positive")))
    fp = _integer(first_value(data, ("fp", "false_positive")))
    tn = _integer(first_value(data, ("tn", "true_negative")))
    fn = _integer(first_value(data, ("fn", "false_negative")))
    metrics = classification_metrics(tp, fp, tn, fn)
    probabilities = _number_list(first_value(data, ("predicted_probabilities", "probabilities"), []))
    outcomes = _number_list(first_value(data, ("actual_outcomes", "outcomes"), []))
    metrics["brier_score"] = calculate_brier_score(probabilities, outcomes)
    model_registry = first_payload(operational_sources, "model_registry")
    active_model = first_payload(operational_sources, "active_model")
    ranking = records(first_payload(operational_sources, "latest_qlib_predictions"))
    ranking = sorted(ranking, key=lambda row: float(row.get("expected_trade_value", row.get("score", 0.0)) or 0.0), reverse=True)

    sections = {
        "model_state": section(DashboardSectionStatus.OK if model_registry or active_model else DashboardSectionStatus.UNKNOWN, registry=model_registry, active_model=active_model),
        "qlib_ranking": section(DashboardSectionStatus.OK if ranking else DashboardSectionStatus.UNKNOWN, ranking=ranking),
        "shadow_veto": section(DashboardSectionStatus.OK, ai_accept_count=accepts, ai_reject_count=rejects, ai_accept_rate_pct=safe_div(accepts, total) * 100.0, ai_reject_rate_pct=safe_div(rejects, total) * 100.0),
        "decision_governance": section(DashboardSectionStatus.OK, final_action="NO_TRADE", riskmanager_authority=True, ai_can_increase_risk=False),
        "drift_regime": section(drift_status, psi=psi, drift_status=drift_status),
        "shadow_classification_metrics": section(DashboardSectionStatus.OK if tp + fp + tn + fn else DashboardSectionStatus.UNKNOWN, **metrics),
        "reward_research": section(DashboardSectionStatus.UNKNOWN, research_only=True),
        "model_governance": section(DashboardSectionStatus.OK, auto_promotion_allowed=False, live_model_promotion_allowed=False, model_promotion_allowed_from_dashboard=False, accuracy_is_primary_metric=False, promotion_status=HardBlockStatus.HARD_BLOCKED.value),
        "aibot_parity": build_aibot_parity_dashboard_section(
            operational_sources, "ai_governance"
        ),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True, trains_model=False, promotes_model=False),
    }
    snapshot = build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.ai_governance,
        schema_version=DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
        sections=sections,
        source_state=operational_sources,
    )
    research_payloads = sources.get("payloads", {})
    normalized_research = normalize_ai_training_research_command_center(
        research_payloads if isinstance(research_payloads, dict) else {}
    )
    snapshot["sections"]["ai_training_research_command_center"] = {
        "status": normalized_research["section_status"],
        "reason": "research_evidence_advisory_only",
        **normalized_research,
    }
    return snapshot


def _without_advisory_research_sources(sources: dict[str, Any]) -> dict[str, Any]:
    paths = set(RESEARCH_SOURCE_PATHS.values())
    keys = set(RESEARCH_SOURCE_PATHS)
    payload_map = sources.get("payloads", {})
    inventory = sources.get("inventory", [])
    return {
        **sources,
        "payloads": {
            key: value
            for key, value in payload_map.items()
            if key not in keys
        }
        if isinstance(payload_map, dict)
        else {},
        "inventory": [
            row
            for row in inventory
            if not isinstance(row, dict) or row.get("path") not in paths
        ]
        if isinstance(inventory, list)
        else [],
        "missing_optional_sources": [
            path
            for path in sources.get("missing_optional_sources", [])
            if path not in paths
        ],
        "errors": [
            error
            for error in sources.get("errors", [])
            if not any(str(error).startswith(f"{path}:") for path in paths)
        ],
    }


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    return [number for item in value if (number := finite_float(item)) is not None]


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
