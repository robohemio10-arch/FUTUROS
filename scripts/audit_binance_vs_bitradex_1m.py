"""Audit local Binance 1m candles against local Bitradex 1m candles."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="audit_binance_vs_bitradex_1m",
    purpose="Compare local Binance 1m candle files with local Bitradex 1m candle files.",
    default_inputs=("data/binance", "data/bitradex"),
    risks=("local market data may be stale", "large candle files may be slow to inspect"),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
