from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features


def _parse_symbols(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _bool_arg(value: str) -> bool:
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Qlib market features from public paper/shadow-safe sources.")
    parser.add_argument("--source", default="data/raw/futures_ohlcv_60d.parquet")
    parser.add_argument("--existing-features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--output", default="data/features/market_features_60d.parquet")
    parser.add_argument("--report", default="data/reports/qlib_market_features_refresh_report.json")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--max-source-age-minutes", type=float, default=15)
    parser.add_argument("--public-download-enabled", type=_bool_arg, default=True)
    parser.add_argument("--public-download-lookback-candles", type=int, default=1500)
    parser.add_argument("--raw-recent-output", default="data/raw/qlib_market_features_refresh_recent.parquet")
    parser.add_argument("--base-url", default="https://fapi.binance.com")
    parser.add_argument("--endpoint", default="/fapi/v1/klines")
    parser.add_argument("--request-sleep-seconds", type=float, default=0.15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = refresh_qlib_market_features(
        source_path=args.source,
        existing_features_path=args.existing_features,
        output_path=args.output,
        report_path=args.report,
        symbols=_parse_symbols(args.symbols),
        timeframe=args.timeframe,
        max_source_age_minutes=args.max_source_age_minutes,
        public_download_enabled=args.public_download_enabled,
        public_download_lookback_candles=args.public_download_lookback_candles,
        raw_recent_output_path=args.raw_recent_output,
        base_url=args.base_url,
        endpoint=args.endpoint,
        request_sleep_seconds=args.request_sleep_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
