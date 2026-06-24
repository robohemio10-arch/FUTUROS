"""Pure normalization for the AI training research command center."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


RESEARCH_SOURCE_PATHS: dict[str, str] = {
    "ocr_v11_research_dataset_audit": (
        "data/reports/ocr_v11_research_dataset_audit.json"
    ),
    "ocr_v11_tp_sl_grid_summary": (
        "data/reports/ocr_v11_tp_sl_grid_summary.json"
    ),
    "ocr_v11_walkforward_montecarlo_summary": (
        "data/reports/ocr_v11_walkforward_montecarlo_summary.json"
    ),
    "qlib_ocr_v11_supervised_training_summary": (
        "data/reports/qlib_ocr_v11_supervised_training_summary.json"
    ),
    "smart_futuros_training_executive_pack": (
        "data/reports/training_reports/smart_futuros_training_executive_pack.json"
    ),
    "qlib_ocr_v11_shadow_model_candidate_registry_report": (
        "data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json"
    ),
    "ai_shadow_online_feedback_learning_loop_report": (
        "data/reports/ai_shadow_online_feedback_learning_loop_report.json"
    ),
    "freqtrade_paper_ai_selector_integration_report": (
        "data/reports/freqtrade_paper_ai_selector_integration_report.json"
    ),
}

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "updates_ai_shadow_runtime": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "registers_model": False,
    "production_enabled": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
}

UNSAFE_TRUE_FLAGS = (
    "updates_freqtrade",
    "updates_qlib_runtime",
    "updates_risk_manager",
    "updates_ai_shadow_runtime",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "registers_model",
    "production_enabled",
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
)


def normalize_ai_training_research_command_center(
    all_source_payloads: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize eight advisory research reports without merging their schemas."""
    payloads = {
        source_key: _source_payload(all_source_payloads, source_key)
        for source_key in RESEARCH_SOURCE_PATHS
    }
    missing = [key for key, payload in payloads.items() if not payload]
    cards = [
        _branch01_card(payloads["ocr_v11_research_dataset_audit"]),
        _branch02_card(payloads["ocr_v11_tp_sl_grid_summary"]),
        _branch03_card(payloads["ocr_v11_walkforward_montecarlo_summary"]),
        _branch04_card(payloads["qlib_ocr_v11_supervised_training_summary"]),
        _branch05_card(payloads["smart_futuros_training_executive_pack"]),
        _branch06_card(
            payloads["qlib_ocr_v11_shadow_model_candidate_registry_report"]
        ),
        _branch07_card(
            payloads["ai_shadow_online_feedback_learning_loop_report"]
        ),
        _branch08_card(
            payloads["freqtrade_paper_ai_selector_integration_report"]
        ),
    ]
    blockers = _research_blockers(payloads, missing)
    branch01 = payloads["ocr_v11_research_dataset_audit"]
    branch04 = payloads["qlib_ocr_v11_supervised_training_summary"]
    branch08 = payloads["freqtrade_paper_ai_selector_integration_report"]
    return {
        "section_status": "MISSING_OPTIONAL" if missing else "WARNING",
        "research_gate_status": "BLOCKED",
        "decision": "MANTER_EM_RESEARCH",
        "authority": "advisory_only",
        "operational_authority": False,
        "summary": {
            "source_count": len(RESEARCH_SOURCE_PATHS),
            "available_source_count": len(RESEARCH_SOURCE_PATHS) - len(missing),
            "missing_optional_source_count": len(missing),
            "research_dataset_rows": _number(branch01.get("research_dataset_rows")),
            "eligible_rows": _number(branch01.get("eligible_rows")),
            "blocked_rows": _number(branch01.get("blocked_rows")),
            "mean_roc_auc": _nested_number(
                branch04, "aggregate_metrics", "mean_roc_auc"
            ),
            "mean_f1": _nested_number(branch04, "aggregate_metrics", "mean_f1"),
            "selector_status": branch08.get("selector_status"),
            "selector_authority": branch08.get("selector_authority", "none"),
            "advisory_only": True,
        },
        "branch_cards": cards,
        "blockers": blockers,
        "safety_flags": dict(SAFETY_FLAGS),
        "missing_optional_sources": missing,
        "source_paths": dict(RESEARCH_SOURCE_PATHS),
        "note": "evidencia consultiva, sem autoridade operacional",
    }


def _source_payload(sources: Mapping[str, Any], source_key: str) -> dict[str, Any]:
    value = sources.get(source_key)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return dict(value[0]) if value and isinstance(value[0], Mapping) else {}
    return {}


def _card(
    *,
    branch_id: str,
    title: str,
    payload: Mapping[str, Any],
    source_key: str,
    headline_label: str,
    headline_value: Any,
    supporting_metrics: Mapping[str, Any],
    default_decision: str,
) -> dict[str, Any]:
    if not payload:
        return {
            "branch_id": branch_id,
            "title": title,
            "status": "MISSING_OPTIONAL",
            "decision": "UNKNOWN",
            "headline_metric": {"label": headline_label, "value": None},
            "supporting_metrics": dict(supporting_metrics),
            "reason": "optional_research_source_missing",
            "source_key": source_key,
            "source_path": RESEARCH_SOURCE_PATHS[source_key],
            "advisory_only": True,
        }
    return {
        "branch_id": branch_id,
        "title": title,
        "status": str(payload.get("status") or "UNKNOWN").upper(),
        "decision": str(payload.get("decision") or default_decision),
        "headline_metric": {"label": headline_label, "value": headline_value},
        "supporting_metrics": dict(supporting_metrics),
        "reason": str(payload.get("reason") or "research_evidence_observed"),
        "source_key": source_key,
        "source_path": RESEARCH_SOURCE_PATHS[source_key],
        "advisory_only": True,
    }


def _branch01_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _card(
        branch_id="branch01_dataset_alignment",
        title="OCR V1.1 Dataset & Candle Alignment",
        payload=payload,
        source_key="ocr_v11_research_dataset_audit",
        headline_label="Research trades",
        headline_value=_number(payload.get("research_dataset_rows")),
        supporting_metrics={
            "eligible_rows": _number(payload.get("eligible_rows")),
            "blocked_rows": _number(payload.get("blocked_rows")),
            "candles_rows": _number(payload.get("candles_rows")),
        },
        default_decision="DATASET_ALIGNED_RESEARCH_ONLY",
    )


def _branch02_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _card(
        branch_id="branch02_tp_sl_grid",
        title="TP/SL Grid & Trade Outcome Simulator",
        payload=payload,
        source_key="ocr_v11_tp_sl_grid_summary",
        headline_label="Grid strategies",
        headline_value=_number(payload.get("grid_rows")),
        supporting_metrics={
            "best_strategy_id": payload.get("best_strategy_id"),
            "best_net_pnl": _number(payload.get("best_net_pnl")),
            "original_net_pnl": _number(payload.get("original_net_pnl")),
        },
        default_decision="GRID_EVALUATED_RESEARCH_ONLY",
    )


def _branch03_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    monte_carlo = payload.get("monte_carlo")
    monte_carlo = monte_carlo if isinstance(monte_carlo, Mapping) else {}
    return _card(
        branch_id="branch03_walkforward_montecarlo",
        title="Walk-forward & Monte Carlo",
        payload=payload,
        source_key="ocr_v11_walkforward_montecarlo_summary",
        headline_label="Candidate walk-forward net PnL",
        headline_value=_number(payload.get("candidate_walkforward_net_pnl")),
        supporting_metrics={
            "original_walkforward_net_pnl": _number(
                payload.get("original_walkforward_net_pnl")
            ),
            "risk_of_ruin": _number(monte_carlo.get("risk_of_ruin")),
            "walkforward_folds": _number(payload.get("walkforward_folds")),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _branch04_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _card(
        branch_id="branch04_qlib_training",
        title="Qlib OCR V1.1 Supervised Training",
        payload=payload,
        source_key="qlib_ocr_v11_supervised_training_summary",
        headline_label="Selected net PnL",
        headline_value=_nested_number(payload, "aggregate_metrics", "selected_net_pnl"),
        supporting_metrics={
            "all_test_net_pnl": _nested_number(
                payload, "aggregate_metrics", "all_test_net_pnl"
            ),
            "mean_roc_auc": _nested_number(
                payload, "aggregate_metrics", "mean_roc_auc"
            ),
            "mean_f1": _nested_number(payload, "aggregate_metrics", "mean_f1"),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _branch05_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    kpis = payload.get("consolidated_kpis")
    kpis = kpis if isinstance(kpis, Mapping) else {}
    return _card(
        branch_id="branch05_executive_report",
        title="Training Executive Report Pack",
        payload=payload,
        source_key="smart_futuros_training_executive_pack",
        headline_label="Executive decision",
        headline_value=payload.get("decision"),
        supporting_metrics={
            "eligible_rows": _number(kpis.get("eligible_rows")),
            "blocked_rows": _number(kpis.get("blocked_rows")),
            "original_net_pnl": _number(kpis.get("original_net_pnl")),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _branch06_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return _card(
        branch_id="branch06_candidate_registry",
        title="Qlib OCR V1.1 Shadow Candidate Registry",
        payload=payload,
        source_key="qlib_ocr_v11_shadow_model_candidate_registry_report",
        headline_label="Promotion status",
        headline_value=payload.get("promotion_status"),
        supporting_metrics={
            "candidate_registry_status": payload.get("candidate_registry_status"),
            "promotion_eligible": payload.get("promotion_eligible"),
            "mean_roc_auc": _number(metrics.get("mean_roc_auc")),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _branch07_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _card(
        branch_id="branch07_feedback_loop",
        title="AI Shadow Online Feedback Learning Loop",
        payload=payload,
        source_key="ai_shadow_online_feedback_learning_loop_report",
        headline_label="Learning action",
        headline_value=payload.get("learning_action"),
        supporting_metrics={
            "training_allowed": payload.get("training_allowed"),
            "promotion_allowed": payload.get("promotion_allowed"),
            "event_count": _number(payload.get("event_count")),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _branch08_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _card(
        branch_id="branch08_freqtrade_selector",
        title="Freqtrade Paper AI Selector Integration",
        payload=payload,
        source_key="freqtrade_paper_ai_selector_integration_report",
        headline_label="Selector authority",
        headline_value=payload.get("selector_authority"),
        supporting_metrics={
            "selector_status": payload.get("selector_status"),
            "observation_count": _number(payload.get("observation_count")),
            "paper_signal_mutation_allowed": payload.get(
                "paper_signal_mutation_allowed"
            ),
        },
        default_decision="MANTER_EM_RESEARCH",
    )


def _research_blockers(
    payloads: Mapping[str, Mapping[str, Any]],
    missing: list[str],
) -> list[str]:
    blockers = [f"missing_optional_source:{source}" for source in missing]
    branch02 = payloads["ocr_v11_tp_sl_grid_summary"]
    branch03 = payloads["ocr_v11_walkforward_montecarlo_summary"]
    branch04 = payloads["qlib_ocr_v11_supervised_training_summary"]
    branch05 = payloads["smart_futuros_training_executive_pack"]
    branch06 = payloads["qlib_ocr_v11_shadow_model_candidate_registry_report"]
    branch07 = payloads["ai_shadow_online_feedback_learning_loop_report"]
    branch08 = payloads["freqtrade_paper_ai_selector_integration_report"]
    if (_number(branch02.get("best_net_pnl")) or 0.0) < 0.0:
        blockers.append("branch02_best_net_pnl_not_positive")
    if branch03.get("status") == "blocked" or branch03.get("decision") == "DESCARTAR_CANDIDATO":
        blockers.append("branch03_candidate_rejected")
    selected = _nested_number(branch04, "aggregate_metrics", "selected_net_pnl")
    all_test = _nested_number(branch04, "aggregate_metrics", "all_test_net_pnl")
    if branch04.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch04_kept_in_research")
    if selected is not None and all_test is not None and selected <= all_test:
        blockers.append("branch04_selected_not_above_all_test")
    if branch05.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch05_kept_in_research")
    if branch06.get("promotion_status") == "blocked":
        blockers.append("branch06_promotion_blocked")
    if branch06.get("promotion_eligible") is False:
        blockers.append("branch06_not_promotion_eligible")
    if branch07.get("learning_action") == "record_only":
        blockers.append("branch07_record_only_feedback")
    if branch07.get("training_allowed") is False:
        blockers.append("branch07_training_not_allowed")
    if branch07.get("promotion_allowed") is False:
        blockers.append("branch07_promotion_not_allowed")
    if branch08.get("selector_authority") in {None, "none"}:
        blockers.append("branch08_selector_has_no_authority")
    for source_key, payload in payloads.items():
        for flag in UNSAFE_TRUE_FLAGS:
            if payload.get(flag) is True:
                blockers.append(f"unsafe_source_flag:{source_key}:{flag}=true")
    blockers.append("dashboard_research_scope_forbids_operational_authority")
    return list(dict.fromkeys(blockers))


def _nested_number(payload: Mapping[str, Any], parent: str, key: str) -> float | int | None:
    nested = payload.get(parent)
    return _number(nested.get(key)) if isinstance(nested, Mapping) else None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number
