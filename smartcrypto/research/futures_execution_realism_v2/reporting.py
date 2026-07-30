"""Pure report rendering for the futures execution realism engine."""

from __future__ import annotations

from typing import Any, Mapping


def render_execution_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact research report without changing decision semantics."""

    metrics = _mapping(report.get("metrics"))
    cost_summary = _mapping(report.get("cost_summary"))
    portfolio = _mapping(report.get("portfolio_exposure"))
    lines = [
        "# Futures Execution Realism Engine V2",
        "",
        "## Classification",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Research only: `{_flag(report, 'research_only')}`",
        f"- Authoritative result: `{report.get('authoritative_result')}`",
        f"- Fixture only: `{report.get('fixture_only')}`",
        "",
        "## Execution",
        "",
        f"- Orders: `{report.get('order_count', 0)}`",
        f"- Fills: `{report.get('fill_count', 0)}`",
        f"- Requested quantity: `{metrics.get('requested_quantity')}`",
        f"- Filled quantity: `{metrics.get('filled_quantity')}`",
        f"- Fill ratio: `{metrics.get('fill_ratio')}`",
        f"- VWAP: `{metrics.get('vwap')}`",
        "",
        "## Costs And Margin",
        "",
        f"- Trading fees: `{cost_summary.get('trading_fees')}`",
        f"- Funding fees: `{cost_summary.get('funding_fees')}`",
        f"- Total cost: `{cost_summary.get('total_cost')}`",
        f"- Net PnL: `{cost_summary.get('net_pnl')}`",
        f"- Reconciliation residual: `{cost_summary.get('reconciliation_residual')}`",
        f"- Liquidations: `{metrics.get('liquidation_count')}`",
        "",
        "## Portfolio",
        "",
        f"- Status: `{portfolio.get('status')}`",
        f"- Gross exposure: `{portfolio.get('gross_exposure')}`",
        f"- Net exposure: `{portfolio.get('net_exposure')}`",
        f"- Correlated exposure: `{portfolio.get('correlated_exposure')}`",
        "",
        "## Boundaries",
        "",
        "- No exchange, Freqtrade, RiskManager, signal, model, or runtime authority.",
        "- Synthetic fixtures remain non-authoritative.",
        "- Quarantined B02 inputs cannot produce authoritative metrics.",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.extend(f"- `{item}`" for item in blockers)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _flag(report: Mapping[str, Any], name: str) -> object:
    return _mapping(report.get("safety_flags")).get(name)
