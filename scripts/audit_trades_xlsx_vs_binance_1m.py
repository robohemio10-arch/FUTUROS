"""Audit local trade spreadsheets against local Binance 1m candles."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="audit_trades_xlsx_vs_binance_1m",
    purpose="Validate local trade XLSX timestamps and prices against local Binance 1m data.",
    default_inputs=("data/trades", "data/binance"),
    risks=("spreadsheet parsing depends on local files", "timezone assumptions can shift rows"),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
