"""Build a local trade quality gate report using available Binance 1m data."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="build_trade_quality_gate_binance_1m",
    purpose="Inspect local inputs needed for a Binance 1m trade quality gate.",
    default_inputs=("data/trades", "data/binance"),
    risks=("quality gate output is advisory and must not submit orders",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
