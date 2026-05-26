"""Preflight a public Binance 1m download request without fetching by default."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="download_binance_1m_for_trades_range",
    purpose="Create a safe local preflight report for Binance 1m download requirements.",
    default_inputs=("data/trades",),
    risks=("public network download is disabled in this institutional wrapper",),
    network_policy="public_network_disabled_by_default",
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
