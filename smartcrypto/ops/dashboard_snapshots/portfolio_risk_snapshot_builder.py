from __future__ import annotations

from itertools import accumulate
from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    all_source_payloads,
    bool_value,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    load_page_sources,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import calculate_cvar, safe_div, safe_mean, safe_quantile, safe_std, safe_sum


REQUIRED_SECTIONS = (
    "capital_summary",
    "allocation",
    "pnl",
    "drawdown_risk",
    "tail_risk",
    "financial_truth",
    "risk_events",
    "audit",
)


def calculate_capital_summary(
    *,
    cash_available: float,
    cash_locked: float,
    inventory_value: float,
    unrealized_pnl: float,
    max_capital_global: float,
    capital_reserved: float,
    capital_deployed: float,
) -> dict[str, float]:
    estimated_equity = cash_available + cash_locked + inventory_value + unrealized_pnl
    return {
        "inventory_value": inventory_value,
        "capital_reserved": capital_reserved,
        "capital_deployed": capital_deployed,
        "estimated_equity": estimated_equity,
        "free_capital_for_entries": max_capital_global - capital_reserved - capital_deployed,
    }


def calculate_drawdown_series(initial_equity: float, pnl_values: list[float]) -> dict[str, Any]:
    equities = [initial_equity + value for value in accumulate(pnl_values)]
    if not equities:
        equities = [initial_equity]
    high_water = initial_equity
    drawdowns: list[float] = []
    for equity in equities:
        high_water = max(high_water, equity)
        drawdowns.append((safe_div(equity, high_water) - 1.0) * 100.0)
    return {
        "equity_curve": equities,
        "drawdown_series_pct": drawdowns,
        "max_drawdown_pct": min(drawdowns, default=0.0),
    }


def calculate_tail_risk(returns: list[float], equity: float) -> dict[str, float]:
    mean = safe_mean(returns)
    std = safe_std(returns)
    return {
        "parametric_var_95": -(mean + (-1.645 * std)) * equity,
        "parametric_var_99": -(mean + (-2.326 * std)) * equity,
        "historical_var_95": -safe_quantile(returns, 0.05) * equity,
        "historical_var_99": -safe_quantile(returns, 0.01) * equity,
        "cvar_95": calculate_cvar(returns, 0.05, equity),
        "cvar_99": calculate_cvar(returns, 0.01, equity),
    }


def build_portfolio_risk_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.portfolio_risk)
    data = all_source_payloads(sources)
    ledger = first_payload(sources, "order_intent_capital_ledger_audit_report")
    reconciliation = first_payload(sources, "state_reconciliation_audit_report")
    performance = first_payload(sources, "paper_financial_performance_metrics_report")
    closed_rows = records(first_payload(sources, "freqtrade_paper_closed_smartcrypto"))
    pnl_values = _pnl_values(closed_rows) or _number_list(first_value(performance, ("returns", "pnl_series"), []))
    returns = _number_list(first_value(performance, ("returns", "return_series", "daily_returns"), []))
    cash_available = _number(first_value(data, ("cash_available", "available_capital", "free_capital")))
    cash_locked = _number(first_value(data, ("cash_locked", "locked_capital")))
    inventory_value = _number(first_value(data, ("inventory_value", "inventory_value_usdt")))
    unrealized_pnl = _number(first_value(data, ("unrealized_pnl", "unrealized_pnl_usdt")))
    max_capital = _number(first_value(data, ("max_capital_global", "total_capital")), 0.0)
    reserved = _number(first_value(ledger, ("capital_reserved", "reserved_notional", "reserved_capital")))
    deployed = _number(first_value(data, ("capital_deployed", "position_notional", "deployed_capital")))
    capital = calculate_capital_summary(
        cash_available=cash_available,
        cash_locked=cash_locked,
        inventory_value=inventory_value,
        unrealized_pnl=unrealized_pnl,
        max_capital_global=max_capital,
        capital_reserved=reserved,
        capital_deployed=deployed,
    )
    initial_equity = _number(first_value(data, ("initial_equity", "starting_equity")), capital["estimated_equity"])
    drawdown = calculate_drawdown_series(initial_equity, pnl_values)
    reconciliation_status = str(first_value(reconciliation, ("status", "reconciliation_status"), "unknown")).upper()
    reconciliation_block = reconciliation_status not in {"OK", "PASS", "PASSED", "VALID"}
    kill_switch = first_payload(sources, "kill_switch")
    kill_active = bool_value(first_value(kill_switch, ("active", "enabled")), False)
    gross_pnl = safe_sum(pnl_values)
    fee_cost = _number(first_value(data, ("fee_cost", "fees", "total_fees")))
    spread_cost = _number(first_value(data, ("spread_cost",)))
    slippage_cost = _number(first_value(data, ("slippage_cost",)))
    net_pnl = gross_pnl - fee_cost - spread_cost - slippage_cost

    sections = {
        "capital_summary": section(DashboardSectionStatus.OK, **capital),
        "allocation": section(
            DashboardSectionStatus.OK,
            asset_allocation=first_value(data, ("asset_allocation", "allocation"), []),
            allocated_pct=safe_div(capital["capital_deployed"], capital["estimated_equity"]) * 100.0,
        ),
        "pnl": section(DashboardSectionStatus.OK, gross_pnl=gross_pnl, net_pnl=net_pnl, unrealized_pnl=unrealized_pnl),
        "drawdown_risk": section(DashboardSectionStatus.OK, **drawdown),
        "tail_risk": section(DashboardSectionStatus.OK, **calculate_tail_risk(returns, capital["estimated_equity"])),
        "financial_truth": section(
            DashboardSectionStatus.BLOCKED if reconciliation_block else DashboardSectionStatus.OK,
            reconciliation_status=reconciliation_status,
            reconciliation_block=reconciliation_block,
            new_entries_blocked=reconciliation_block or kill_active,
        ),
        "risk_events": section(DashboardSectionStatus.OK, kill_switch_active=kill_active),
        "audit": section(DashboardSectionStatus.OK, financial_source="authorized_snapshots_only"),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.portfolio_risk,
        schema_version=DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )


def _number(value: Any, default: float = 0.0) -> float:
    return finite_float(value, default) or 0.0


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    return [number for item in value if (number := finite_float(item)) is not None]


def _pnl_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = first_value(row, ("net_pnl", "pnl_usdt", "pnl_fechado", "profit_abs"))
        number = finite_float(value)
        if number is not None:
            values.append(number)
    return values
