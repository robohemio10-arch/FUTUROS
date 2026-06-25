"""Research-only closeout for Paper vs trades_master divergence.

This module is intentionally deterministic and side-effect free. It records the
canonical evidence already collected during the Paper vs trades_master research
investigation, without reading runtime data or granting operational authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "paper_master_divergence_research_closeout_v1"

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_runtime": False,
    "writes_data": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "runs_training": False,
    "runs_ocr": False,
    "runs_ai_shadow_incremental": False,
}

CRITICAL_TRUE_FLAGS = ("research_only", "paper_only", "shadow_only")
CRITICAL_FALSE_FLAGS = tuple(
    key for key, expected in SAFETY_FLAGS.items() if expected is False
)

ALLOWED_NEXT_STEPS = [
    "versionar contratos do Daily Learning source map",
    "criar loaders read-only",
    "criar KPI pack diario",
    "criar divergence/alignment diario",
    "criar candle coverage/entry features diario",
    "criar mistake/winner catalog",
    "criar pattern mining research",
    "criar candidate shadow rule registry",
    "criar OOS validation",
]

FORBIDDEN_NEXT_STEPS = [
    "alterar Freqtrade strategy",
    "alterar RiskManager",
    "alterar stop-loss operacional",
    "alterar stake/leverage",
    "promover regra candidata",
    "promover modelo",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever em data/ ou runtime",
    "rodar OCR/rebuild/training/incremental IA Shadow nesta branch",
]


def build_paper_master_divergence_research_closeout() -> dict[str, Any]:
    """Build the canonical deterministic closeout payload."""
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "paper_does_not_replicate_trades_master_edge",
        **SAFETY_FLAGS,
        "executive_summary": {
            "title": "Paper vs trades_master divergence research closeout",
            "conclusion": "paper_freqtrade_does_not_replicate_master_edge",
            "operator_message": (
                "A janela Paper 19D ficou negativa enquanto o trades_master na "
                "mesma janela permaneceu positivo. A evidência bloqueia mudança "
                "operacional e mantém a investigação em research-only."
            ),
        },
        "paper_window": {
            "label": "Paper 19D",
            "scope": "paper_vs_trades_master_same_window",
        },
        "paper_vs_master_metrics": {
            "paper_trade_count": 239,
            "paper_net_pnl": -21.35477552,
            "paper_profit_factor": 0.8033314207,
            "paper_win_rate_pct": 40.5858,
            "paper_avg_duration_hours": 3.4234,
            "master_trade_count": 243,
            "master_net_pnl": 143.166332,
            "master_profit_factor": 2.0725730333,
            "master_win_rate_pct": 70.7819,
            "master_avg_duration_hours": 0.1937,
            "paper_minus_master_net_pnl": -164.52110752,
            "paper_minus_master_win_rate_pct_points": -30.1961,
            "conclusion": "paper_freqtrade_does_not_replicate_master_edge",
        },
        "root_cause_findings": {
            "roi_trade_count": 97,
            "roi_net_pnl": 87.22777285,
            "stop_loss_trade_count": 142,
            "stop_loss_net_pnl": -108.58254837,
            "remove_stop_loss_under_30m_simulated_net_pnl": 13.56136734,
            "remove_stop_loss_under_30m_delta": 34.91614286,
            "conclusion": (
                "principal_degradacao_vem_de_excesso_de_stop_loss_"
                "especialmente_eth_long_e_trades_under_30m"
            ),
        },
        "temporal_alignment_findings": {
            "matches_15m": 13,
            "matches_30m": 31,
            "matches_60m": 42,
            "opposite_side_30m": 18,
            "opposite_side_60m": 28,
            "paper_stop_after_master_win_30m": 26,
            "paper_stop_after_master_win_60m": 40,
            "conclusion": "timing_side_exit_mismatch_alem_de_stop_loss",
        },
        "coverage_findings": {
            "paper_trades_total": 239,
            "entry_candle_covered_trades": 192,
            "entry_candle_covered_pct": 80.33,
            "entry_candle_uncovered_trades": 47,
            "entry_candle_uncovered_pct": 19.67,
            "full_feature_materialization_allowed": False,
            "partial_feature_materialization_allowed": True,
            "conclusion": "features_analysis_only_partial_local_research_only",
        },
        "candidate_shadow_rules_summary": {
            "best_rule": {
                "lb_10m_ret_close_lte": -0.0038501215827868,
                "lb_30m_ret_close_lte": -0.0060685748963285,
            },
            "flagged_count": 32,
            "target_flagged": 21,
            "baseline_flagged": 11,
            "precision_pct": 65.625,
            "recall_pct": 41.176,
            "simulated_removed_pnl_delta": 8.9745,
            "can_review_candidate_shadow_rules": True,
            "can_promote_rules": False,
            "can_apply_to_freqtrade": False,
            "can_apply_to_risk_manager": False,
            "decision": "review_research_only_do_not_promote",
        },
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_next_steps": list(FORBIDDEN_NEXT_STEPS),
        "branch_scope": {
            "scope_type": "versioned_research_closeout",
            "reads_runtime": False,
            "writes_runtime": False,
            "reads_data": False,
            "writes_data": False,
            "authoritative_for_operation": False,
        },
    }
    payload["validation_errors"] = validate_paper_master_divergence_research_closeout(
        payload
    )
    return payload


def validate_paper_master_divergence_research_closeout(
    payload: Mapping[str, Any],
) -> list[str]:
    """Return deterministic validation errors for a closeout payload."""
    errors: list[str] = []
    expected_top_level: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "paper_does_not_replicate_trades_master_edge",
    }
    for key, expected in expected_top_level.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key in CRITICAL_TRUE_FLAGS:
        if payload.get(key) is not True:
            errors.append(f"{key}_must_be_true")
    for key in CRITICAL_FALSE_FLAGS:
        if payload.get(key) is not False:
            errors.append(f"{key}_must_be_false")
    for section in (
        "executive_summary",
        "paper_window",
        "paper_vs_master_metrics",
        "root_cause_findings",
        "temporal_alignment_findings",
        "coverage_findings",
        "candidate_shadow_rules_summary",
        "operator_decision",
        "allowed_next_steps",
        "forbidden_next_steps",
        "branch_scope",
    ):
        if section not in payload:
            errors.append(f"missing_{section}")
    metrics = _mapping(payload.get("paper_vs_master_metrics"))
    expected_metrics: dict[str, int | float | str] = {
        "paper_trade_count": 239,
        "paper_net_pnl": -21.35477552,
        "paper_profit_factor": 0.8033314207,
        "paper_win_rate_pct": 40.5858,
        "paper_avg_duration_hours": 3.4234,
        "master_trade_count": 243,
        "master_net_pnl": 143.166332,
        "master_profit_factor": 2.0725730333,
        "master_win_rate_pct": 70.7819,
        "master_avg_duration_hours": 0.1937,
        "paper_minus_master_net_pnl": -164.52110752,
        "paper_minus_master_win_rate_pct_points": -30.1961,
        "conclusion": "paper_freqtrade_does_not_replicate_master_edge",
    }
    for key, expected in expected_metrics.items():
        if metrics.get(key) != expected:
            errors.append(f"paper_vs_master_metrics_{key}_mismatch")
    operator = _mapping(payload.get("operator_decision"))
    if operator.get("final_decision") != "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH":
        errors.append("operator_final_decision_mismatch")
    return errors


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
