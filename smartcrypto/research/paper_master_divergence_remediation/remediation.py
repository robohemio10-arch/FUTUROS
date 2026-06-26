"""Research-only Paper/Master divergence remediation pack.

This module converts the known Paper-vs-Master divergence into a structured,
auditable remediation research report. It is deliberately non-operational:
there is no Freqtrade mutation, RiskManager mutation, Qlib runtime update,
IA Shadow runtime update, order submission, scheduler registration, or live
release authority.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = "paper_master_divergence_remediation_research_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "MANTER_EM_RESEARCH"

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "applies_remediation": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "registers_candidate_rules": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_training": False,
    "executes_scheduler": False,
    "executes_orchestrator": False,
    "executes_stage_builders": False,
    "sends_orders": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "writes_data": False,
    "writes_runtime": False,
    "writes_reports": False,
    "writes_sqlite": False,
    "writes_parquet": False,
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

CANONICAL_BASELINE_EVIDENCE: dict[str, Any] = {
    "source": "paper_master_divergence_closeout_context",
    "paper_19d": {
        "trade_count": 239,
        "net_pnl": -21.35477552,
        "profit_factor": 0.803331,
        "win_rate": 0.405858,
        "avg_duration_hours": 3.4234,
        "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
        "gross_profit": 87.22777285,
        "gross_loss": -108.58254837,
        "fees": 11.89221848,
    },
    "master_same_window": {
        "trade_count": 243,
        "net_pnl": 143.166332,
        "profit_factor": 2.072573,
        "win_rate": 0.707819,
        "max_drawdown": -9.378203,
        "mean_trade_roi": 0.001181,
    },
    "divergence": {
        "paper_minus_master_trade_count": -4,
        "paper_minus_master_net_pnl": -164.52110752,
        "paper_minus_master_profit_factor": -1.269242,
        "paper_minus_master_win_rate_points": -30.1961,
        "paper_replicates_master_edge": False,
    },
    "cluster_evidence": {
        "stop_loss_net_pnl": -108.58254837,
        "roi_net_pnl": 87.22777285,
        "remove_stop_loss_under_30m_delta": 34.9161,
        "eth_long_stop_loss_cluster": "critical",
        "fast_stop_under_30m": "critical",
        "candidate_shadow_rule": (
            "lb_10m_ret_close <= -0.0038501215827868 AND "
            "lb_30m_ret_close <= -0.0060685748963285"
        ),
        "candidate_shadow_rule_precision": 0.65625,
        "candidate_shadow_rule_recall": 0.41176,
        "candidate_shadow_rule_simulated_removed_pnl_delta": 8.9745,
    },
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _safe_string(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _extract_pnl(row: Mapping[str, Any]) -> float:
    for key in (
        "net_pnl",
        "pnl",
        "profit_abs",
        "close_profit_abs",
        "realized_pnl",
        "pnl_abs",
    ):
        if key in row:
            return _safe_float(row.get(key))
    return 0.0


def _extract_duration_hours(row: Mapping[str, Any]) -> float:
    for key in ("duration_hours", "trade_duration_hours", "duration_h"):
        if key in row:
            return _safe_float(row.get(key))
    minutes = row.get("duration_minutes") or row.get("trade_duration_minutes")
    return _safe_float(minutes) / 60.0 if minutes is not None else 0.0


def classify_duration_bucket(duration_hours: float) -> str:
    """Classify duration into stable Paper/Master diagnostic buckets."""
    minutes = max(0.0, duration_hours * 60.0)
    if minutes < 15:
        return "<15m"
    if minutes < 30:
        return "15-30m"
    if minutes < 60:
        return "30-60m"
    if minutes < 180:
        return "1-3h"
    if minutes < 360:
        return "3-6h"
    return ">6h"


def calculate_trade_kpis(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate compact, leakage-free PnL KPIs from in-memory trade rows."""
    materialized = list(rows)
    pnls = [_extract_pnl(row) for row in materialized]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_pnl = sum(pnls)
    abs_loss = abs(gross_loss)
    profit_factor = gross_profit / abs_loss if abs_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    durations = [_extract_duration_hours(row) for row in materialized]
    avg_duration_hours = sum(durations) / len(durations) if durations else 0.0
    return {
        "trade_count": len(materialized),
        "net_pnl": round(net_pnl, 10),
        "gross_profit": round(gross_profit, 10),
        "gross_loss": round(gross_loss, 10),
        "profit_factor": round(profit_factor, 10) if math.isfinite(profit_factor) else "inf",
        "win_rate": round(len(wins) / len(pnls), 10) if pnls else 0.0,
        "loss_rate": round(len(losses) / len(pnls), 10) if pnls else 0.0,
        "expectancy": round(net_pnl / len(pnls), 10) if pnls else 0.0,
        "avg_duration_hours": round(avg_duration_hours, 10),
    }


def build_cluster_summary(rows: Iterable[Mapping[str, Any]], group_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Aggregate in-memory trades by explicit diagnostic group keys."""
    buckets: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key_parts: list[str] = []
        for key in group_keys:
            if key == "duration_bucket":
                key_parts.append(classify_duration_bucket(_extract_duration_hours(row)))
            else:
                key_parts.append(_safe_string(row.get(key)))
        buckets[tuple(key_parts)].append(row)

    summaries: list[dict[str, Any]] = []
    for key_tuple, bucket_rows in buckets.items():
        kpis = calculate_trade_kpis(bucket_rows)
        summaries.append(
            {
                "group_keys": list(group_keys),
                "group_values": list(key_tuple),
                **kpis,
                "is_material_negative_cluster": bool(kpis["trade_count"] >= 2 and kpis["net_pnl"] < 0),
            }
        )
    return sorted(summaries, key=lambda item: (item["net_pnl"], -item["trade_count"]))


def _hypothesis(
    hypothesis_id: str,
    priority: str,
    problem_area: str,
    statement: str,
    evidence: list[str],
    remediation_direction: str,
    required_validation: list[str],
    status: str = "candidate_for_research",
) -> dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "priority": priority,
        "problem_area": problem_area,
        "statement": statement,
        "supporting_evidence": evidence,
        "remediation_direction": remediation_direction,
        "required_validation": required_validation,
        "status": status,
        "research_only": True,
        "operational_authority": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
    }


def build_remediation_hypotheses() -> list[dict[str, Any]]:
    """Return the canonical Paper/Master remediation hypotheses."""
    cluster = CANONICAL_BASELINE_EVIDENCE["cluster_evidence"]
    divergence = CANONICAL_BASELINE_EVIDENCE["divergence"]
    return [
        _hypothesis(
            "H1",
            "P1",
            "exit_risk_stop_loss",
            "Stop-loss rápido está destruindo expectancy líquido do paper.",
            [
                f"stop_loss_net_pnl={cluster['stop_loss_net_pnl']}",
                f"remove_stop_loss_under_30m_delta={cluster['remove_stop_loss_under_30m_delta']}",
            ],
            "Validar filtros shadow de fast-stop antes de qualquer alteração operacional.",
            ["OOS por dia", "OOS por símbolo", "não degradar ROI winners", "mínimo de trades"],
        ),
        _hypothesis(
            "H2",
            "P1",
            "symbol_side_cluster",
            "ETH long é cluster estruturalmente negativo no paper.",
            ["eth_long_stop_loss_cluster=critical", "fast_stop_under_30m=critical"],
            "Isolar ETH long por regime e lado antes de transformar em regra candidata.",
            ["estabilidade por regime", "estabilidade por lado", "comparação BTC vs ETH"],
        ),
        _hypothesis(
            "H3",
            "P1",
            "late_entry",
            "Paper pode estar entrando tarde em relação ao master.",
            ["paper_entered_after_master_exit deve ser medido diariamente"],
            "Criar análise temporal de atraso entrada/master antes de qualquer filtro.",
            ["matching temporal", "janela de tolerância", "latency penalty"],
            status="needs_runtime_alignment_evidence",
        ),
        _hypothesis(
            "H4",
            "P1",
            "missed_master_winners",
            "Paper perde winners do master por desalinhamento temporal ou gating incorreto.",
            [f"paper_minus_master_net_pnl={divergence['paper_minus_master_net_pnl']}"],
            "Mapear winners do master não capturados pelo paper e separar por causa provável.",
            ["matched winners", "missed winners", "winner retention check"],
            status="needs_matching_evidence",
        ),
        _hypothesis(
            "H5",
            "P2",
            "opposite_side",
            "Paper pode inverter lado em janelas onde o master capturou edge.",
            ["opposite_side_count deve permanecer métrica obrigatória"],
            "Auditar divergência por side antes de propor bloqueio direcional.",
            ["side stability", "symbol/side buckets", "não mascarar winners short"],
            status="needs_side_alignment_evidence",
        ),
        _hypothesis(
            "H6",
            "P1",
            "filter_quality",
            "Filtros atuais podem preservar losers e remover winners.",
            [
                f"paper_minus_master_win_rate_points={divergence['paper_minus_master_win_rate_points']}",
                f"candidate_rule_precision={cluster['candidate_shadow_rule_precision']}",
            ],
            "Validar candidate rules com false-positive/false-negative e impacto em winners.",
            ["OOS", "false positive rate", "false negative rate", "winner degradation"],
        ),
        _hypothesis(
            "H7",
            "P2",
            "data_coverage",
            "Candle/feature coverage parcial pode distorcer diagnóstico e decisão.",
            ["full_coverage=false deve bloquear extrapolação"],
            "Separar covered/uncovered subset e bloquear conclusão global sem cobertura suficiente.",
            ["coverage by symbol", "coverage by date", "covered vs uncovered bias"],
            status="needs_coverage_evidence",
        ),
        _hypothesis(
            "H8",
            "P1",
            "selector_expected_value",
            "Qlib/selector pode sinalizar sem penalizar regime, drawdown, slippage e custos.",
            ["paper_replicates_master_edge=false", f"paper_profit_factor={CANONICAL_BASELINE_EVIDENCE['paper_19d']['profit_factor']}"],
            "Recalibrar pesquisa em expected trade value líquido ajustado a risco, sem promoção.",
            ["expected value audit", "cost/slippage penalties", "drawdown penalty", "regime confidence"],
        ),
    ]


def _build_gate_matrix(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": bool(report["research_only"] and report["operational_authority"] is False),
            "evidence": "research_only=true; operational_authority=false",
        },
        {
            "gate_id": "paper_master_divergence_confirmed",
            "gate_name": "Paper/Master divergence remains explicit",
            "severity": "critical",
            "passed": bool(report["divergence_confirmed"] and report["paper_replicates_master_edge"] is False),
            "evidence": f"paper_minus_master_net_pnl={report['divergence_metrics']['paper_minus_master_net_pnl']}",
        },
        {
            "gate_id": "hypotheses_created",
            "gate_name": "Remediation hypotheses created without operational application",
            "severity": "high",
            "passed": bool(report["remediation_hypotheses_created"] and not report["applies_remediation"]),
            "evidence": f"hypothesis_count={len(report['remediation_hypotheses'])}",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": bool(not report["can_promote_rules"] and not report["can_promote_model"]),
            "evidence": "can_promote_rules=false; can_promote_model=false",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": bool(
                not report["updates_freqtrade"]
                and not report["updates_risk_manager"]
                and not report["updates_qlib_runtime"]
                and not report["updates_ai_shadow_runtime"]
                and not report["sends_orders"]
            ),
            "evidence": "no runtime updates; sends_orders=false",
        },
    ]


def _summarize_gates(gates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    gate_list = list(gates)
    failed = [gate for gate in gate_list if not gate.get("passed")]
    return {
        "gate_count": len(gate_list),
        "passed_gate_count": len(gate_list) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [str(gate["gate_id"]) for gate in failed],
        "critical_failed_gate_ids": [
            str(gate["gate_id"]) for gate in failed if gate.get("severity") == "critical"
        ],
    }


def _build_divergence_metrics(
    paper_kpis: Mapping[str, Any],
    master_kpis: Mapping[str, Any],
    no_runtime_rows: bool,
) -> dict[str, Any]:
    if no_runtime_rows:
        return dict(CANONICAL_BASELINE_EVIDENCE["divergence"])
    paper_pf = _safe_float(paper_kpis.get("profit_factor"))
    master_pf = _safe_float(master_kpis.get("profit_factor"))
    paper_win_rate = _safe_float(paper_kpis.get("win_rate"))
    master_win_rate = _safe_float(master_kpis.get("win_rate"))
    return {
        "paper_minus_master_trade_count": int(paper_kpis.get("trade_count", 0)) - int(master_kpis.get("trade_count", 0)),
        "paper_minus_master_net_pnl": round(_safe_float(paper_kpis.get("net_pnl")) - _safe_float(master_kpis.get("net_pnl")), 10),
        "paper_minus_master_profit_factor": round(paper_pf - master_pf, 10),
        "paper_minus_master_win_rate_points": round((paper_win_rate - master_win_rate) * 100.0, 10),
        "paper_replicates_master_edge": bool(
            _safe_float(paper_kpis.get("net_pnl")) > 0
            and paper_pf >= 1.0
            and _safe_float(master_kpis.get("net_pnl")) > 0
        ),
    }


def build_paper_master_divergence_remediation_report(
    paper_rows: Iterable[Mapping[str, Any]] | None = None,
    master_rows: Iterable[Mapping[str, Any]] | None = None,
    *,
    write_requested: bool = False,
) -> dict[str, Any]:
    """Build the Paper/Master remediation research report.

    The default mode loads no runtime rows and uses the canonical closeout
    evidence already established by the project. Supplying in-memory rows is
    allowed for tests and future research loaders, but it still does not grant
    operational authority.
    """
    paper_list = list(paper_rows or [])
    master_list = list(master_rows or [])
    no_runtime_rows = not paper_list and not master_list

    if no_runtime_rows:
        paper_kpis = dict(CANONICAL_BASELINE_EVIDENCE["paper_19d"])
        master_kpis = dict(CANONICAL_BASELINE_EVIDENCE["master_same_window"])
        input_mode = "no_runtime_rows_loaded"
        cluster_summary: list[dict[str, Any]] = []
    else:
        paper_kpis = calculate_trade_kpis(paper_list)
        master_kpis = calculate_trade_kpis(master_list)
        input_mode = "in_memory_rows_only"
        cluster_summary = build_cluster_summary(
            paper_list,
            ("symbol", "side", "exit_reason", "duration_bucket"),
        )

    divergence_metrics = _build_divergence_metrics(paper_kpis, master_kpis, no_runtime_rows)
    paper_replicates_master_edge = bool(divergence_metrics["paper_replicates_master_edge"])
    divergence_confirmed = bool(not paper_replicates_master_edge)
    hypotheses = build_remediation_hypotheses()

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "status": "blocked",
        "reason": "paper_master_divergence_requires_research_remediation_before_any_operation",
        "decision": DECISION,
        "input_mode": input_mode,
        "write_requested": bool(write_requested),
        "write_performed": False,
        "write_blocked_reason": "read_only_research_contract" if write_requested else None,
        "divergence_confirmed": divergence_confirmed,
        "paper_replicates_master_edge": paper_replicates_master_edge,
        "paper_kpis": paper_kpis,
        "master_kpis": master_kpis,
        "divergence_metrics": divergence_metrics,
        "cluster_summary": cluster_summary,
        "canonical_cluster_evidence": dict(CANONICAL_BASELINE_EVIDENCE["cluster_evidence"]),
        "remediation_hypotheses_created": bool(hypotheses),
        "remediation_hypotheses": hypotheses,
        "minimum_next_research_gates": [
            "OOS por dia/símbolo/lado",
            "proteção contra remover ROI winners",
            "controle de falso positivo/falso negativo",
            "cobertura de candles e features antes de extrapolar",
            "expected trade value líquido ajustado por custo, slippage, drawdown, drift e regime",
        ],
        "allowed_next_steps": [
            "materializar diagnóstico em relatório research-only",
            "desenhar validação OOS de hipóteses H1-H8",
            "criar candidate rules apenas em registry shadow bloqueado",
            "comparar covered vs uncovered antes de generalizar conclusões",
        ],
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        **SAFETY_FLAGS,
    }
    gate_matrix = _build_gate_matrix(report)
    report["gate_matrix"] = gate_matrix
    report["gate_summary"] = _summarize_gates(gate_matrix)
    return report
