"""Diagnose local date-shift patterns in trade and candle datasets."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="diagnose_date_shift_patterns",
    purpose="Inspect local datasets for date-shift diagnostic inputs.",
    default_inputs=("data/trades", "data/binance"),
    risks=("timezone and locale assumptions require analyst review",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
