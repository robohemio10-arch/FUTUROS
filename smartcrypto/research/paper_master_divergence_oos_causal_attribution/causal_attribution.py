"""OOS causal attribution plan for Paper/Master divergence.

This module is intentionally research-only. It converts the already confirmed
Paper/Master divergence and the H1/H2/H6 hypotheses into an auditable causal
attribution plan. It does not validate a rule, train a model, register a
candidate, change runtime, or emit operational recommendations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_master_divergence_oos_causal_attribution_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "MANTER_EM_RESEARCH"

DEFAULT_PAPER_KPIS: dict[str, Any] = {
    "trade_count": 239,
    "net_pnl": -21.35477552,
    "profit_factor": 0.803331,
    "win_rate": 0.405858,
    "gross_profit": 87.22777285,
    "gross_loss": -108.58254837,
    "fees": 11.89221848,
    "avg_duration_hours": 3.4234,
    "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
}

DEFAULT_MASTER_KPIS: dict[str, Any] = {
    "trade_count": 243,
    "net_pnl": 143.166332,
    "profit_factor": 2.072573,
    "win_rate": 0.707819,
    "max_drawdown": -9.378203,
    "mean_trade_roi": 0.001181,
}

DEFAULT_DIVERGENCE_METRICS: dict[str, Any] = {
    "paper_minus_master_trade_count": -4,
    "paper_minus_master_net_pnl": -164.52110752,
    "paper_minus_master_profit_factor": -1.269242,
    "paper_minus_master_win_rate_points": -30.1961,
    "paper_replicates_master_edge": False,
}

CANONICAL_CLUSTER_EVIDENCE: dict[str, Any] = {
    "roi_net_pnl": 87.22777285,
    "stop_loss_net_pnl": -108.58254837,
    "remove_stop_loss_under_30m_delta": 34.9161,
    "fast_stop_under_30m": "critical",
    "eth_long_stop_loss_cluster": "critical",
    "candidate_shadow_rule": (
        "lb_10m_ret_close <= -0.0038501215827868 "
        "AND lb_30m_ret_close <= -0.0060685748963285"
    ),
    "candidate_shadow_rule_precision": 0.65625,
    "candidate_shadow_rule_recall": 0.41176,
    "candidate_shadow_rule_simulated_removed_pnl_delta": 8.9745,
}

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
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
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "registers_candidate_rules": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "runs_training": False,
    "executes_scheduler": False,
    "executes_orchestrator": False,
    "executes_stage_builders": False,
}

FORBIDDEN_ACTIONS = [
    "aplicar regra no Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar ou promover candidate rule",
    "promover modelo",
    "executar treino operacional",
    "habilitar live ou canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade por padrão",
]

MINIMUM_NEXT_RESEARCH_GATES = [
    "OOS por dia/símbolo/lado/exit_reason/duração",
    "proteção explícita contra remover ROI winners",
    "controle de falso positivo e falso negativo por hipótese",
    "separação covered vs uncovered antes de extrapolar",
    "expected trade value líquido ajustado por custo, slippage, drawdown, drift e regime",
    "nenhuma hipótese pode virar regra sem registry shadow bloqueado e OOS aprovado",
]


@dataclass(frozen=True)
class CausalHypothesisAttribution:
    """Research-only causal attribution contract for one hypothesis."""

    hypothesis_id: str
    priority: str
    problem_area: str
    statement: str
    causal_status: str
    attribution_class: str
    supporting_metrics: dict[str, Any]
    oos_required: bool
    oos_passed: bool
    required_slices: list[str]
    falsification_tests: list[str]
    winner_protection_tests: list[str]
    minimum_acceptance_criteria: list[str]
    promotion_status: str
    operational_authority: bool = False
    can_apply_to_freqtrade: bool = False
    can_apply_to_risk_manager: bool = False
    can_promote_rules: bool = False
    can_promote_model: bool = False
    research_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "priority": self.priority,
            "problem_area": self.problem_area,
            "statement": self.statement,
            "causal_status": self.causal_status,
            "attribution_class": self.attribution_class,
            "supporting_metrics": self.supporting_metrics,
            "oos_required": self.oos_required,
            "oos_passed": self.oos_passed,
            "required_slices": self.required_slices,
            "falsification_tests": self.falsification_tests,
            "winner_protection_tests": self.winner_protection_tests,
            "minimum_acceptance_criteria": self.minimum_acceptance_criteria,
            "promotion_status": self.promotion_status,
            "operational_authority": self.operational_authority,
            "can_apply_to_freqtrade": self.can_apply_to_freqtrade,
            "can_apply_to_risk_manager": self.can_apply_to_risk_manager,
            "can_promote_rules": self.can_promote_rules,
            "can_promote_model": self.can_promote_model,
            "research_only": self.research_only,
        }


def _build_h1_fast_stop_loss_attribution() -> CausalHypothesisAttribution:
    return CausalHypothesisAttribution(
        hypothesis_id="H1",
        priority="P1",
        problem_area="exit_risk_stop_loss",
        statement="Stop-loss rápido está destruindo expectancy líquido do paper.",
        causal_status="plausible_not_oos_validated",
        attribution_class="direct_loss_cluster_candidate",
        supporting_metrics={
            "roi_net_pnl": CANONICAL_CLUSTER_EVIDENCE["roi_net_pnl"],
            "stop_loss_net_pnl": CANONICAL_CLUSTER_EVIDENCE["stop_loss_net_pnl"],
            "remove_stop_loss_under_30m_delta": CANONICAL_CLUSTER_EVIDENCE[
                "remove_stop_loss_under_30m_delta"
            ],
            "fast_stop_under_30m": CANONICAL_CLUSTER_EVIDENCE["fast_stop_under_30m"],
        },
        oos_required=True,
        oos_passed=False,
        required_slices=[
            "day",
            "symbol",
            "side",
            "exit_reason",
            "duration_bucket",
            "covered_feature_subset",
        ],
        falsification_tests=[
            "delta de remover fast stop deve permanecer positivo fora da amostra",
            "efeito não pode depender de único dia ou único símbolo",
            "efeito deve sobreviver a custos/fees/slippage conservadores",
        ],
        winner_protection_tests=[
            "não remover ROI winners",
            "não reduzir winner retention abaixo do baseline",
            "não trocar stop_loss por drawdown maior em simulação de hold",
        ],
        minimum_acceptance_criteria=[
            "net_pnl_delta_oos > 0",
            "profit_factor_oos não pode degradar",
            "winner_retention_rate deve permanecer alto",
            "minimum_trade_count por bucket deve ser respeitado",
        ],
        promotion_status="blocked_pending_oos",
    )


def _build_h2_eth_long_attribution() -> CausalHypothesisAttribution:
    return CausalHypothesisAttribution(
        hypothesis_id="H2",
        priority="P1",
        problem_area="symbol_side_cluster",
        statement="ETH long pode ser cluster estruturalmente negativo no paper.",
        causal_status="underidentified_requires_symbol_side_oos",
        attribution_class="symbol_side_regime_candidate",
        supporting_metrics={
            "eth_long_stop_loss_cluster": CANONICAL_CLUSTER_EVIDENCE[
                "eth_long_stop_loss_cluster"
            ],
            "fast_stop_under_30m": CANONICAL_CLUSTER_EVIDENCE["fast_stop_under_30m"],
            "paper_symbols": DEFAULT_PAPER_KPIS["symbols"],
        },
        oos_required=True,
        oos_passed=False,
        required_slices=[
            "day",
            "symbol",
            "side",
            "regime_proxy",
            "exit_reason",
            "duration_bucket",
        ],
        falsification_tests=[
            "ETH long precisa permanecer negativo fora da amostra",
            "cluster não pode ser proxy de stop_loss genérico",
            "cluster não pode ser consequência de janela temporal específica",
        ],
        winner_protection_tests=[
            "não bloquear ETH long winners de alta qualidade",
            "separar ETH long por regime antes de propor qualquer veto",
            "comparar contra BTC long e ETH short",
        ],
        minimum_acceptance_criteria=[
            "efeito negativo estável por múltiplos dias",
            "efeito não desaparece ao controlar exit_reason",
            "regime/side bucket tem amostra suficiente",
        ],
        promotion_status="blocked_pending_oos_and_regime_control",
    )


def _build_h6_filter_quality_attribution() -> CausalHypothesisAttribution:
    return CausalHypothesisAttribution(
        hypothesis_id="H6",
        priority="P1",
        problem_area="filter_quality",
        statement="Filtros atuais podem preservar losers ou remover winners.",
        causal_status="partial_filter_candidate_not_validated",
        attribution_class="classification_quality_candidate",
        supporting_metrics={
            "candidate_shadow_rule": CANONICAL_CLUSTER_EVIDENCE["candidate_shadow_rule"],
            "candidate_shadow_rule_precision": CANONICAL_CLUSTER_EVIDENCE[
                "candidate_shadow_rule_precision"
            ],
            "candidate_shadow_rule_recall": CANONICAL_CLUSTER_EVIDENCE[
                "candidate_shadow_rule_recall"
            ],
            "candidate_shadow_rule_simulated_removed_pnl_delta": CANONICAL_CLUSTER_EVIDENCE[
                "candidate_shadow_rule_simulated_removed_pnl_delta"
            ],
            "paper_minus_master_win_rate_points": DEFAULT_DIVERGENCE_METRICS[
                "paper_minus_master_win_rate_points"
            ],
        },
        oos_required=True,
        oos_passed=False,
        required_slices=[
            "day",
            "symbol",
            "side",
            "winner_loser_label",
            "covered_feature_subset",
            "uncovered_feature_subset",
        ],
        falsification_tests=[
            "precision precisa permanecer acima do baseline fora da amostra",
            "recall baixo não pode deixar a maioria dos losses intacta",
            "delta positivo não pode vir de poucos outliers",
        ],
        winner_protection_tests=[
            "false_positive_winners deve ficar abaixo de tolerância rígida",
            "winner_pnl_removed deve ser explicitamente reportado",
            "ROI winners removidos bloqueiam promoção",
        ],
        minimum_acceptance_criteria=[
            "oos_precision estável",
            "oos_net_removed_pnl_delta > 0",
            "false_positive_winner_loss controlado",
            "coverage suficiente antes de generalizar",
        ],
        promotion_status="blocked_pending_oos_false_positive_false_negative_audit",
    )


def build_causal_hypothesis_attributions() -> list[dict[str, Any]]:
    """Return the scoped H1/H2/H6 attribution contracts."""
    return [
        _build_h1_fast_stop_loss_attribution().to_dict(),
        _build_h2_eth_long_attribution().to_dict(),
        _build_h6_filter_quality_attribution().to_dict(),
    ]


def build_oos_protocol() -> dict[str, Any]:
    """Return the required OOS protocol before any candidate action."""
    return {
        "protocol_status": "required_not_executed",
        "oos_validated": False,
        "split_dimensions": [
            "day",
            "symbol",
            "side",
            "exit_reason",
            "duration_bucket",
            "covered_vs_uncovered",
        ],
        "minimum_metrics": [
            "trade_count",
            "net_pnl",
            "profit_factor",
            "win_rate",
            "max_drawdown",
            "winner_retention_rate",
            "winner_pnl_removed",
            "loser_pnl_removed",
            "false_positive_count",
            "false_negative_count",
            "precision",
            "recall",
            "coverage_ratio",
        ],
        "hard_blockers": [
            "OOS ausente",
            "amostra insuficiente por bucket",
            "ROI winners degradados",
            "efeito concentrado em um único dia",
            "covered/uncovered bias não mensurado",
            "qualquer tentativa de promoção sem registry shadow bloqueado",
        ],
        "expected_value_contract": (
            "expected_trade_value = Qlib_expected_return_net × "
            "Shadow_probability_quality × Regime_confidence - Estimated_fee - "
            "Estimated_spread - Estimated_slippage - Latency_penalty - "
            "Drawdown_penalty - Drift_penalty"
        ),
    }


def _build_gate_matrix(
    attributions: list[dict[str, Any]],
    divergence_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": True,
            "evidence": "research_only=true; operational_authority=false",
        },
        {
            "gate_id": "paper_master_divergence_confirmed",
            "gate_name": "Paper/Master divergence remains explicit",
            "severity": "critical",
            "passed": divergence_metrics["paper_replicates_master_edge"] is False,
            "evidence": (
                "paper_minus_master_net_pnl="
                f"{divergence_metrics['paper_minus_master_net_pnl']}"
            ),
        },
        {
            "gate_id": "scoped_hypotheses_attributed",
            "gate_name": "H1/H2/H6 causal attribution created",
            "severity": "high",
            "passed": [item["hypothesis_id"] for item in attributions] == ["H1", "H2", "H6"],
            "evidence": f"hypotheses={[item['hypothesis_id'] for item in attributions]}",
        },
        {
            "gate_id": "oos_required_not_bypassed",
            "gate_name": "OOS validation remains mandatory",
            "severity": "critical",
            "passed": all(item["oos_required"] and not item["oos_passed"] for item in attributions),
            "evidence": "all scoped hypotheses remain blocked pending OOS",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "can_promote_rules=false; can_promote_model=false",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": True,
            "evidence": "no runtime updates; sends_orders=false",
        },
    ]


def _summarize_gates(gates: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [gate["gate_id"] for gate in gates if not bool(gate["passed"])]
    critical_failed = [
        gate["gate_id"]
        for gate in gates
        if not bool(gate["passed"]) and gate["severity"] == "critical"
    ]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": failed,
        "critical_failed_gate_ids": critical_failed,
    }


def build_oos_causal_attribution_report(project_root: str | Path = ".") -> dict[str, Any]:
    """Build the pure research-only OOS causal attribution report."""
    divergence_metrics = dict(DEFAULT_DIVERGENCE_METRICS)
    attributions = build_causal_hypothesis_attributions()
    gate_matrix = _build_gate_matrix(attributions, divergence_metrics)

    return {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root),
        "status": "blocked",
        "decision": DECISION,
        "reason": "paper_master_divergence_requires_oos_causal_attribution_before_any_operation",
        "input_mode": "no_runtime_rows_loaded",
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "write_blocked_reason": None,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_parquet": False,
        "writes_sqlite": False,
        "divergence_confirmed": True,
        "paper_replicates_master_edge": False,
        "causal_attribution_created": True,
        "causal_attribution_scope": ["H1", "H2", "H6"],
        "causal_attribution_hypothesis_count": len(attributions),
        "oos_validation_required": True,
        "oos_validated": False,
        "ready_for_candidate_registry": False,
        "remediation_application_allowed": False,
        "paper_kpis": dict(DEFAULT_PAPER_KPIS),
        "master_kpis": dict(DEFAULT_MASTER_KPIS),
        "divergence_metrics": divergence_metrics,
        "canonical_cluster_evidence": dict(CANONICAL_CLUSTER_EVIDENCE),
        "causal_hypothesis_attributions": attributions,
        "oos_protocol": build_oos_protocol(),
        "minimum_next_research_gates": list(MINIMUM_NEXT_RESEARCH_GATES),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "allowed_next_steps": [
            "executar OOS por dia/símbolo/lado para H1/H2/H6",
            "medir winner retention e false positives antes de qualquer candidate rule",
            "separar covered vs uncovered para evitar extrapolação indevida",
            "somente depois criar registry shadow bloqueado se OOS passar",
        ],
        "gate_matrix": gate_matrix,
        "gate_summary": _summarize_gates(gate_matrix),
    }


def _resolve_output_path(project_root: Path, output_path: str | Path | None) -> Path:
    if output_path is not None:
        return Path(output_path)
    return project_root / "data" / "reports" / "paper_master_divergence_oos_causal_attribution_v1.json"


def run_oos_causal_attribution_research(
    project_root: str | Path = ".",
    *,
    write: bool = False,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the research report builder and optionally write an unversioned report."""
    root = Path(project_root)
    report = build_oos_causal_attribution_report(root)
    report["write_requested"] = bool(write)

    if not write:
        return report

    resolved_output = _resolve_output_path(root, output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )

    report["output_path"] = str(resolved_output)
    report["write_performed"] = True
    report["writes_reports"] = True
    return report
