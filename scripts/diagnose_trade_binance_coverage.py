"""Diagnose local Binance candle coverage for known trade ranges."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="diagnose_trade_binance_coverage",
    purpose="Inspect local trades and Binance data for coverage diagnostics.",
    default_inputs=("data/trades", "data/binance"),
    risks=("missing coverage can invalidate downstream research results",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
