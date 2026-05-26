"""Audit local MDY-formatted trade spreadsheets against local Binance 1m candles."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="audit_trades_xlsx_vs_binance_1m_mdy",
    purpose="Check MDY date parsing for local trade XLSX files against Binance 1m candles.",
    default_inputs=("data/trades", "data/binance"),
    risks=("MDY/DDM ambiguity can alter trade alignment",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
