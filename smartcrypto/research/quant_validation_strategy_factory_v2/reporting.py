"""Markdown reporting for the B04 research-only validation pipeline."""

from __future__ import annotations

from typing import Any, Mapping


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# SMART FUTUROS — B04 Quantitative Validation & Strategy Factory V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Protocol: `{report.get('protocol_hash')}`",
        f"- Dataset authority: `{report.get('dataset_authority')}`",
        f"- Authoritative result: `{report.get('authoritative_result')}`",
        f"- Candidate count: `{report.get('candidate_count', 0)}`",
        f"- Research challengers: `{report.get('research_challenger_count', 0)}`",
        f"- Rejected candidates: `{report.get('rejected_candidate_count', 0)}`",
        "",
        "## Safety boundary",
        "",
        "This report is paper/shadow/research-only. It does not promote a strategy,",
        "write an active registry, update Freqtrade/RiskManager/Qlib/AI Shadow runtime,",
        "access a private exchange endpoint, or submit orders.",
        "",
        "## Candidate scorecards",
        "",
        "| Candidate | Family | Decision | OOS trades | OOS net PnL | Expectancy | PF | PBO | Ruin | Stability |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.get("candidate_scorecards", []):
        lines.append(
            "| {candidate_id} | {candidate_family} | {final_decision} | {total_trades} | "
            "{oos_net_pnl} | {oos_expectancy} | {oos_profit_factor} | {pbo} | "
            "{risk_of_ruin} | {parameter_stability} |".format(
                **{
                    **item,
                    "parameter_stability": item.get("parameter_stability", {}).get("parameter_stability"),
                }
            )
        )
    lines.extend(
        [
            "",
            "## Global robustness",
            "",
            f"- CPCV/PBO: `{_compact(report.get('cpcv_pbo'))}`",
            f"- White Reality Check: `{_compact(report.get('white_reality_check'))}`",
            f"- Material negative segments: `{report.get('material_negative_segments', [])}`",
            "",
            "## Governance",
            "",
            "- `promotion_allowed=false`",
            "- `operational_authority=false`",
            "- `automatic_promotion_allowed=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "",
        ]
    )
    return "\n".join(lines)


def _compact(value: Any) -> str:
    if not isinstance(value, Mapping):
        return str(value)
    keys = ("status", "reason", "pbo", "pvalue", "path_count", "simulation_count")
    return ", ".join(f"{key}={value.get(key)}" for key in keys if key in value)
