from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.market.market_data_health import (  # noqa: E402
    DEFAULT_REPORT_PATH,
    MarketDataHealthLimits,
    run_market_data_health_audit,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita saúde de dados de mercado em modo paper/shadow read-only."
    )
    parser.add_argument("--candles")
    parser.add_argument("--runtime-candles")
    parser.add_argument("--ticker")
    parser.add_argument("--order-book")
    parser.add_argument("--trades")
    parser.add_argument("--ws-heartbeat")
    parser.add_argument("--rest-snapshot")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--max-candle-age-seconds", type=int, default=300)
    parser.add_argument("--max-ticker-age-seconds", type=int, default=60)
    parser.add_argument("--max-order-book-age-seconds", type=int, default=30)
    parser.add_argument("--max-ws-heartbeat-age-seconds", type=int, default=30)
    parser.add_argument("--max-spread-bps", type=float, default=25.0)
    parser.add_argument("--min-top-depth", type=float, default=10_000.0)
    parser.add_argument("--max-slippage-bps", type=float, default=15.0)
    parser.add_argument("--max-latency-ms", type=float, default=1_000.0)
    parser.add_argument("--max-ws-rest-delta-seconds", type=int, default=10)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_market_data_health_audit(
        candles_path=args.candles,
        runtime_candles_path=args.runtime_candles,
        ticker_path=args.ticker,
        order_book_path=args.order_book,
        trades_path=args.trades,
        ws_heartbeat_path=args.ws_heartbeat,
        rest_snapshot_path=args.rest_snapshot,
        report_path=args.report,
        symbol_column=args.symbol_column,
        timestamp_column=args.timestamp_column,
        limits=MarketDataHealthLimits(
            max_candle_age_seconds=args.max_candle_age_seconds,
            max_ticker_age_seconds=args.max_ticker_age_seconds,
            max_order_book_age_seconds=args.max_order_book_age_seconds,
            max_ws_heartbeat_age_seconds=args.max_ws_heartbeat_age_seconds,
            max_spread_bps=args.max_spread_bps,
            min_top_depth=args.min_top_depth,
            max_slippage_bps=args.max_slippage_bps,
            max_latency_ms=args.max_latency_ms,
            max_ws_rest_delta_seconds=args.max_ws_rest_delta_seconds,
        ),
        strict=args.strict,
    )
    print(
        json.dumps(report, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 1 if report.get("status") == "blocked" else 0


def terminate_without_runtime_finalizers(exit_code: int) -> NoReturn:
    """Terminate the standalone CLI after explicitly flushing its streams.

    The audit can load native Arrow resources through ``pandas.read_parquet``.
    Some Linux/Python/native-library combinations abort during interpreter
    teardown after the audit and report have already completed. This boundary
    is restricted to direct CLI execution; imports and in-process callers keep
    normal Python finalization semantics through ``main``.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(exit_code)


if __name__ == "__main__":
    cli_exit_code = main()
    terminate_without_runtime_finalizers(cli_exit_code)
