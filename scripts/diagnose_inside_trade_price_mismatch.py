"""Diagnose local inside-trade price mismatches without mutating source data."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="diagnose_inside_trade_price_mismatch",
    purpose="Inspect local trade files for inputs used in price mismatch diagnostics.",
    default_inputs=("data/trades", "data/binance"),
    risks=("diagnostics are read-only and do not repair prices automatically",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
