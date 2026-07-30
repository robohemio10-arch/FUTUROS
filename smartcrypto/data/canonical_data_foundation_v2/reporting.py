"""Compact institutional reporting for Canonical Data Foundation V2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def render_foundation_markdown(report: Mapping[str, Any]) -> str:
    lineage = _mapping(report.get("trader_master_lineage"))
    candles = _mapping(report.get("candle_recovery"))
    datasets = _mapping(report.get("dataset_foundation"))
    manifest = _mapping(report.get("manifest_contract"))
    safety = _mapping(report.get("safety_flags"))
    lines = [
        "# Canonical Data Foundation V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Generated at UTC: `{report.get('generated_at_utc')}`",
        "",
        "## Trader Master lineage",
        "",
        f"- Total rows: {lineage.get('total_rows', 0)}",
        f"- Verified: {lineage.get('verified_rows', 0)}",
        f"- Permanent quarantine: {lineage.get('quarantined_rows', 0)}",
        f"- Unresolved: {lineage.get('unresolved_rows', 0)}",
        f"- Source conflicts: {lineage.get('source_conflict_rows', 0)}",
        "",
        "Observed legacy values remain research evidence. Missing identities, fees, "
        "funding, fills, quantities, or namespaces are never fabricated.",
        "",
        "## Candle recovery",
        "",
        f"- Blocked input rows: {candles.get('candle_blocked_input_rows', 0)}",
        f"- Recovered and verified: {candles.get('candle_recovered_verified_rows', 0)}",
        (
            "- Permanent quarantine: "
            f"{candles.get('candle_permanent_quarantine_rows', 0)}"
        ),
        f"- Forward fill used: `{candles.get('forward_fill_used', False)}`",
        f"- Gaps preserved: `{candles.get('gaps_preserved', False)}`",
        "",
        "## Dataset boundaries",
        "",
        f"- Contract count: {datasets.get('dataset_contract_count', 0)}",
        f"- Independent authorities: `{datasets.get('authorities_independent', False)}`",
        f"- Independent writers: `{datasets.get('writers_independent', False)}`",
        f"- Cross-write guards: `{datasets.get('cross_write_guards_active', False)}`",
        "",
        "## Execution manifest",
        "",
        f"- Schema: `{manifest.get('schema_version')}`",
        f"- Hash reproducible: `{manifest.get('hash_reproducible', False)}`",
        f"- Atomic writer: `{manifest.get('atomic_writer')}`",
        f"- Release eligible in this worktree: `{manifest.get('release_eligible', False)}`",
        "",
        "## Safety",
        "",
        f"- Paper only: `{safety.get('paper_only')}`",
        f"- Shadow only: `{safety.get('shadow_only')}`",
        f"- Research only: `{safety.get('research_only')}`",
        f"- Sends orders: `{safety.get('sends_orders')}`",
        f"- Exchange private access: `{safety.get('exchange_private_access')}`",
        f"- Operational authority: `{safety.get('operational_authority')}`",
        "",
        "This report is research evidence only. It does not authorize runtime, risk, "
        "signals, model promotion, live trading, or order submission.",
        "",
    ]
    return "\n".join(lines)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
