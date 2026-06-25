"""Research-only KPI pack for Daily Paper/Master Learning Loop."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_learning_readonly_loaders import (
    build_daily_learning_readonly_loader_report,
    validate_daily_learning_readonly_loader_report,
)


DAILY_PAPER_MASTER_KPI_PACK_SCHEMA_VERSION = "daily_paper_master_kpi_pack_v1"

KPI_SCOPE: dict[str, bool] = {
    "computes_aggregate_kpis": True,
    "loads_runtime_trade_rows": False,
    "loads_excel_rows": False,
    "loads_sqlite_rows": False,
    "computes_temporal_alignment": False,
    "computes_candle_coverage": False,
    "computes_entry_features": False,
    "mines_patterns": False,
    "registers_candidate_rules": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
    "writes_reports": False,
}

READINESS_POLICY: dict[str, bool] = {
    "kpi_pack_is_not_readiness_evidence": True,
    "kpi_pack_outputs_do_not_release_live": True,
    "kpi_pack_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar divergence/alignment diario em branch futura",
    "criar candle coverage/entry features em branch futura",
    "criar mistake/winner catalog em branch futura",
    "criar pattern mining research em branch futura",
    "criar candidate shadow rule registry em branch futura",
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
    "usar KPIs para liberar operacao",
    "promover regra candidata",
    "promover modelo",
]


def build_daily_paper_master_kpi_pack(
    project_root: str | Path | None = None,
    paper_trades: Sequence[Mapping[str, Any]] | None = None,
    master_trades: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a blocked research-only KPI pack from in-memory trades."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    loader_report = build_daily_learning_readonly_loader_report(root)
    loader_validation_errors = validate_daily_learning_readonly_loader_report(
        loader_report
    )
    paper_rows = [] if paper_trades is None else list(paper_trades)
    master_rows = [] if master_trades is None else list(master_trades)
    input_mode = (
        "no_runtime_rows_loaded"
        if paper_trades is None and master_trades is None
        else "in_memory_trade_rows"
    )
    paper_kpis = calculate_trade_kpis(paper_rows)
    master_kpis = calculate_trade_kpis(master_rows)
    payload: dict[str, Any] = {
        "schema_version": DAILY_PAPER_MASTER_KPI_PACK_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "kpi_pack_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "loader_report_status": loader_report.get("status"),
        "loader_validation_errors": loader_validation_errors,
        "input_mode": input_mode,
        "paper_kpis": paper_kpis,
        "master_kpis": master_kpis,
        "kpi_comparison": compare_kpi_summaries(paper_kpis, master_kpis),
        "kpi_scope": dict(KPI_SCOPE),
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
    payload["validation_errors"] = validate_daily_paper_master_kpi_pack(payload)
    return payload


def calculate_trade_kpis(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate aggregate KPIs from already-normalized in-memory trade rows."""
    pnl_values = [_to_float(trade.get("net_pnl")) for trade in trades]
    valid_pnls = [value for value in pnl_values if value is not None]
    trade_count = len(valid_pnls)
    wins = [value for value in valid_pnls if value > 0]
    losses = [value for value in valid_pnls if value < 0]
    flats = [value for value in valid_pnls if value == 0]
    gross_profit = sum(wins)
    gross_loss_abs = abs(sum(losses))
    net_pnl = sum(valid_pnls)
    durations = [
        duration
        for duration in (_to_float(trade.get("duration_minutes")) for trade in trades)
        if duration is not None
    ]
    return {
        "trade_count": trade_count,
        "win_count": len(wins),
        "loss_count": len(losses),
        "flat_count": len(flats),
        "win_rate_pct": _rate(len(wins), trade_count),
        "loss_rate_pct": _rate(len(losses), trade_count),
        "gross_profit": gross_profit,
        "gross_loss_abs": gross_loss_abs,
        "net_pnl": net_pnl,
        "profit_factor": _profit_factor(gross_profit, gross_loss_abs),
        "profit_factor_reason": _profit_factor_reason(gross_profit, gross_loss_abs),
        "expectancy": _safe_divide(net_pnl, trade_count),
        "avg_win": _safe_divide(gross_profit, len(wins)),
        "avg_loss_abs": _safe_divide(gross_loss_abs, len(losses)),
        "best_trade": max(valid_pnls) if valid_pnls else None,
        "worst_trade": min(valid_pnls) if valid_pnls else None,
        "max_drawdown": _max_drawdown(valid_pnls),
        "avg_duration_minutes": (
            _safe_divide(sum(durations), len(durations)) if durations else None
        ),
        "exit_reason_counts": _counter(trades, "exit_reason"),
        "symbol_counts": _counter(trades, "symbol"),
        "side_counts": _counter(trades, "side"),
    }


def compare_kpi_summaries(
    paper_kpis: Mapping[str, Any],
    master_kpis: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare aggregate Paper and Master KPI summaries."""
    return {
        "paper_trade_count": paper_kpis.get("trade_count"),
        "master_trade_count": master_kpis.get("trade_count"),
        "trade_count_delta": _delta(
            paper_kpis.get("trade_count"), master_kpis.get("trade_count")
        ),
        "paper_net_pnl": paper_kpis.get("net_pnl"),
        "master_net_pnl": master_kpis.get("net_pnl"),
        "net_pnl_delta": _delta(paper_kpis.get("net_pnl"), master_kpis.get("net_pnl")),
        "paper_win_rate_pct": paper_kpis.get("win_rate_pct"),
        "master_win_rate_pct": master_kpis.get("win_rate_pct"),
        "win_rate_pct_delta": _delta(
            paper_kpis.get("win_rate_pct"), master_kpis.get("win_rate_pct")
        ),
        "paper_profit_factor": paper_kpis.get("profit_factor"),
        "master_profit_factor": master_kpis.get("profit_factor"),
        "profit_factor_delta": _delta(
            paper_kpis.get("profit_factor"), master_kpis.get("profit_factor")
        ),
        "paper_expectancy": paper_kpis.get("expectancy"),
        "master_expectancy": master_kpis.get("expectancy"),
        "expectancy_delta": _delta(
            paper_kpis.get("expectancy"), master_kpis.get("expectancy")
        ),
        "comparison_scope": "aggregate_kpis_only_no_temporal_alignment",
        "grants_operational_authority": False,
    }


def validate_daily_paper_master_kpi_pack(payload: Mapping[str, Any]) -> list[str]:
    """Validate the KPI pack payload."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_PAPER_MASTER_KPI_PACK_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "kpi_pack_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("kpi_scope"))
    for key, expected in KPI_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"kpi_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    for section in ("paper_kpis", "master_kpis", "kpi_comparison"):
        if not isinstance(payload.get(section), Mapping):
            errors.append(f"{section}_must_be_object")
    return errors


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return (count / total) * 100.0


def _safe_divide(numerator: float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _profit_factor(gross_profit: float, gross_loss_abs: float) -> float | str | None:
    if gross_loss_abs > 0:
        return gross_profit / gross_loss_abs
    if gross_profit > 0:
        return "inf"
    return None


def _profit_factor_reason(gross_profit: float, gross_loss_abs: float) -> str:
    if gross_loss_abs > 0:
        return "finite_profit_factor"
    if gross_profit > 0:
        return "no_losses_with_positive_profit"
    return "no_losses_or_profit"


def _max_drawdown(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _counter(trades: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for trade in trades:
        value = trade.get(key)
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def _delta(paper_value: Any, master_value: Any) -> float | int | None:
    paper = _to_float(paper_value)
    master = _to_float(master_value)
    if paper is None or master is None:
        return None
    delta = paper - master
    return int(delta) if delta.is_integer() else delta


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
