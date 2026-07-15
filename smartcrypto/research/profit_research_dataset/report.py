"""Report rendering for the research dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def stable_json(payload: Mapping[str, Any], *, pretty: bool = True) -> str:
    return json.dumps(
        payload,
        indent=2 if pretty else None,
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
    ) + ("\n" if pretty else "")


def render_markdown(report: Mapping[str, Any]) -> str:
    btc = report.get("btc_block_hypothesis", {})
    return "\n".join(
        [
            "# Profit Research Dataset Snapshot V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Paper rows: `{report.get('paper_row_count')}`",
            f"- Eligible trades: `{report.get('eligible_trade_count')}`",
            f"- Rejected trades: `{report.get('rejected_trade_count')}`",
            f"- Candle coverage: `{report.get('candle_coverage_ratio')}`",
            f"- Net PnL: `{report.get('net_pnl')}`",
            f"- Profit factor: `{report.get('profit_factor')}`",
            f"- Maximum drawdown: `{report.get('max_drawdown')}`",
            f"- Winner-to-loser conversions: `{report.get('winner_to_loser_count')}`",
            f"- BTC block hypothesis: `{btc.get('conclusion')}`",
            "",
            "Research-only evidence. It has no signal, risk, model or execution authority.",
            "",
        ]
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"not_json_serializable:{type(value).__name__}")
