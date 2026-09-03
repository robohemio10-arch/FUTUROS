from __future__ import annotations

import math
from itertools import accumulate
from typing import Any

from smartcrypto.ops.dashboard_snapshots.aibot_parity_integration import (
    build_aibot_parity_dashboard_section,
)
from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
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
    DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import calculate_cvar, safe_div, safe_mean, safe_quantile, safe_std, safe_sum


REQUIRED_SECTIONS = (
    "periods",
    "performance",
    "risk_adjusted_metrics",
    "operational_metrics",
    "tca",
    "regime_comparison",
    "asset_comparison",
    "soak_gap_accounting",
    "exports",
    "institutional_score",
    "aibot_parity",
    "audit",
)


def calculate_tca(
    *,
    gross_pnl: float,
    fees: float,
    spread_cost: float,
    slippage_cost: float,
    latency_cost: float,
    gross_alpha: float | None = None,
) -> dict[str, float]:
    total_cost = fees + spread_cost + slippage_cost + latency_cost
    return {
        "gross_pnl": gross_pnl,
        "total_fees": fees,
        "spread_cost": spread_cost,
        "slippage_cost": slippage_cost,
        "latency_cost": latency_cost,
        "total_tca_cost": total_cost,
        "net_pnl": gross_pnl - total_cost,
        "cost_to_alpha_ratio": safe_div(total_cost, gross_alpha if gross_alpha is not None else gross_pnl),
        "profit_consumed_by_costs_pct": safe_div(total_cost, gross_pnl) * 100.0,
    }


def calculate_equity_drawdown(initial_equity: float, net_pnl_values: list[float]) -> dict[str, Any]:
    cumulative = list(accumulate(net_pnl_values))
    equity_curve = [initial_equity + value for value in cumulative] or [initial_equity]
    high_water = initial_equity
    drawdowns: list[float] = []
    drawdown_usdt: list[float] = []
    for equity in equity_curve:
        high_water = max(high_water, equity)
        drawdowns.append(safe_div(equity, high_water) - 1.0)
        drawdown_usdt.append(equity - high_water)
    return {
        "equity_curve": equity_curve,
        "drawdown_series": drawdowns,
        "max_drawdown": min(drawdowns, default=0.0),
        "max_drawdown_usdt": min(drawdown_usdt, default=0.0),
    }


def calculate_performance_metrics(
    returns: list[float],
    net_pnl_values: list[float],
    *,
    capital_base: float,
    annualized_return: float = 0.0,
) -> dict[str, float]:
    wins = [value for value in net_pnl_values if value > 0]
    losses = [abs(value) for value in net_pnl_values if value < 0]
    avg_win = safe_mean(wins)
    avg_loss = safe_mean(losses)
    win_rate_decimal = safe_div(len(wins), len(net_pnl_values))
    downside = [value for value in returns if value < 0]
    downside_std = safe_std(downside)
    total_return = math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0
    total_net_pnl = safe_sum(net_pnl_values)
    return {
        "return_net_pct": safe_div(total_net_pnl, capital_base) * 100.0,
        "cumulative_return": total_return,
        "sharpe": safe_div(safe_mean(returns), safe_std(returns)) * math.sqrt(365.0),
        "sortino": safe_div(safe_mean(returns), downside_std) * math.sqrt(365.0),
        "downside_std": downside_std,
        "volatility_annualized": safe_std(returns) * math.sqrt(365.0),
        "win_rate": win_rate_decimal * 100.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": safe_div(avg_win, avg_loss),
        "profit_factor": safe_div(safe_sum(wins), safe_sum(losses)),
        "expectancy_net": (win_rate_decimal * avg_win) - ((1.0 - win_rate_decimal) * avg_loss),
        "annualized_return": annualized_return,
    }


def calculate_institutional_score(
    robustness_score: float,
    risk_score: float,
    tca_score: float,
    recovery_score: float,
    consistency_score: float,
    winrate_score: float,
) -> float:
    return (
        robustness_score * 0.25
        + risk_score * 0.25
        + tca_score * 0.20
        + recovery_score * 0.15
        + consistency_score * 0.10
        + winrate_score * 0.05
    )


def build_quantitative_reports_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.quantitative_reports)
    data = all_source_payloads(sources)
    performance_report = first_payload(sources, "paper_financial_performance_metrics_report")
    trades = records(first_payload(sources, "freqtrade_paper_closed_smartcrypto"))
    if not trades:
        trades = records(first_payload(sources, "trade_enriched"))
    pnl_values = _row_numbers(trades, ("net_pnl", "pnl_usdt", "pnl_fechado", "profit_abs"))
    returns = _row_numbers(trades, ("target_return", "return_pct", "net_return_pct", "profit_ratio"))
    if returns and max((abs(value) for value in returns), default=0.0) > 2.0:
        returns = [value / 100.0 for value in returns]
    if not pnl_values:
        pnl_values = _number_list(first_value(performance_report, ("pnl_series", "returns"), []))
    capital_base = _number(first_value(data, ("capital_base", "initial_equity", "starting_equity")), 1.0)
    gross_pnl = _number(first_value(data, ("gross_pnl", "gross_profit")), safe_sum(pnl_values))
    fees = _number(first_value(data, ("fees", "fee_usdt", "total_fees")))
    spread_cost = _number(first_value(data, ("spread_cost",)))
    slippage_cost = _number(first_value(data, ("slippage_cost",)))
    latency_cost = _number(first_value(data, ("latency_cost",)))
    tca = calculate_tca(gross_pnl=gross_pnl, fees=fees, spread_cost=spread_cost, slippage_cost=slippage_cost, latency_cost=latency_cost, gross_alpha=_number(first_value(data, ("gross_alpha",)), gross_pnl))
    metrics = calculate_performance_metrics(returns, pnl_values, capital_base=capital_base, annualized_return=_number(first_value(data, ("annualized_return",))))
    drawdown = calculate_equity_drawdown(capital_base, pnl_values)
    metrics.update(
        {
            "calmar": safe_div(metrics["annualized_return"], abs(drawdown["max_drawdown"])),
            "recovery_factor": safe_div(tca["net_pnl"], abs(drawdown["max_drawdown_usdt"])),
            "historical_var_95": -safe_quantile(returns, 0.05) * capital_base,
            "historical_var_99": -safe_quantile(returns, 0.01) * capital_base,
            "cvar_95": calculate_cvar(returns, 0.05, capital_base),
        }
    )
    score_inputs = {name: _number(first_value(data, (name,)), 0.0) for name in ("robustness_score", "risk_score", "tca_score", "recovery_score", "consistency_score", "winrate_score")}
    institutional = calculate_institutional_score(**score_inputs)
    gap_payload = first_payload(sources, "paper_shadow_soak_gap_accounting_report")
    if not gap_payload:
        gap_payload = first_payload(sources, "readiness_snapshot_v2")
    gap_status = str(first_value(gap_payload, ("status",), "unknown")).lower()
    critical_gaps = int(_number(first_value(gap_payload, ("critical_gap_count",), 0)))
    gap_section_status = (
        DashboardSectionStatus.BLOCKED
        if gap_status in {"blocked", "critical", "failed"} or critical_gaps > 0
        else DashboardSectionStatus.OK if gap_payload else DashboardSectionStatus.UNKNOWN
    )
    sections = {
        "periods": section(DashboardSectionStatus.OK, available_periods=first_value(data, ("periods", "available_periods"), [])),
        "performance": section(DashboardSectionStatus.OK if pnl_values else DashboardSectionStatus.UNKNOWN, **metrics, **drawdown, net_pnl=tca["net_pnl"]),
        "risk_adjusted_metrics": section(DashboardSectionStatus.OK if returns else DashboardSectionStatus.UNKNOWN, sharpe=metrics["sharpe"], sortino=metrics["sortino"], calmar=metrics["calmar"], historical_var_95=metrics["historical_var_95"], historical_var_99=metrics["historical_var_99"], cvar_95=metrics["cvar_95"]),
        "operational_metrics": section(DashboardSectionStatus.OK, turnover=_number(first_value(data, ("turnover",))), capital_utilization_pct=safe_div(_number(first_value(data, ("capital_deployed",))), _number(first_value(data, ("total_capital",)), capital_base)) * 100.0),
        "tca": section(DashboardSectionStatus.OK, **tca),
        "regime_comparison": section(DashboardSectionStatus.UNKNOWN, regimes=first_value(data, ("regime_summary", "regime_comparison"), {})),
        "asset_comparison": section(DashboardSectionStatus.UNKNOWN, assets=first_value(data, ("symbol_summary", "asset_comparison"), {})),
        "soak_gap_accounting": section(
            gap_section_status,
            "gap_accounting_blocks_readiness" if gap_section_status is DashboardSectionStatus.BLOCKED else "gap_accounting_readonly",
            continuous_valid_soak_days=_number(first_value(gap_payload, ("continuous_valid_soak_days",), 0.0)),
            observed_calendar_days=_number(first_value(gap_payload, ("observed_calendar_days",), 0.0)),
            critical_gap_count=critical_gaps,
            warning_gap_count=int(_number(first_value(gap_payload, ("warning_gap_count",), 0))),
            max_gap_minutes=_number(first_value(gap_payload, ("max_gap_minutes",), 0.0)),
            seven_day_diagnostic_status=first_value(gap_payload, ("seven_day_diagnostic_status",), "unknown"),
            thirty_day_readiness_status=first_value(gap_payload, ("thirty_day_readiness_status",), "blocked"),
            readiness_gap_free=first_value(gap_payload, ("readiness_gap_free",), False) is True and critical_gaps == 0,
            canary_release_allowed=False,
            live_release_allowed=False,
        ),
        "exports": section(DashboardSectionStatus.OK, readonly=True, writes_training_dataset=False, writes_trades_master=False),
        "institutional_score": section(DashboardSectionStatus.OK, score=institutional, weights={"robustness": 0.25, "risk": 0.25, "tca": 0.20, "recovery": 0.15, "consistency": 0.10, "winrate": 0.05}),
        "aibot_parity": build_aibot_parity_dashboard_section(
            sources, "quantitative_reports"
        ),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.quantitative_reports,
        schema_version=DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )


def _number(value: Any, default: float = 0.0) -> float:
    return finite_float(value, default) or 0.0


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    return [number for item in value if (number := finite_float(item)) is not None]


def _row_numbers(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[float]:
    output: list[float] = []
    for row in rows:
        number = finite_float(first_value(row, keys))
        if number is not None:
            output.append(number)
    return output
