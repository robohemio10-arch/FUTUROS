from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.event_driven_backtest import (
    DEFAULT_PRICE_COLUMN,
    DEFAULT_REPORT_PATH,
    DEFAULT_SIDE_COLUMN,
    DEFAULT_SYMBOL_COLUMN,
    DEFAULT_TIMESTAMP_COLUMN,
    run_event_driven_backtest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run event-driven execution backtest.")
    parser.add_argument("--signals", required=True, type=Path)
    parser.add_argument("--candles", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--timestamp-column", default=DEFAULT_TIMESTAMP_COLUMN)
    parser.add_argument("--symbol-column", default=DEFAULT_SYMBOL_COLUMN)
    parser.add_argument("--side-column", default=DEFAULT_SIDE_COLUMN)
    parser.add_argument("--price-column", default=DEFAULT_PRICE_COLUMN)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--latency-seconds", type=float, default=0.0)
    parser.add_argument("--liquidity-cap", type=float, default=1_000_000.0)
    parser.add_argument("--partial-fill-ratio", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_event_driven_backtest(
        signals_path=args.signals,
        candles_path=args.candles,
        report_path=args.report,
        timestamp_column=args.timestamp_column,
        symbol_column=args.symbol_column,
        side_column=args.side_column,
        price_column=args.price_column,
        fee_bps=args.fee_bps,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        latency_seconds=args.latency_seconds,
        liquidity_cap=args.liquidity_cap,
        partial_fill_ratio=args.partial_fill_ratio,
        seed=args.seed,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "warning", "insufficient_data"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
