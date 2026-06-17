from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.research.binance_futures_15s_redownload import (  # noqa: E402
    DownloadConfig,
    normalize_source_mode,
    normalize_symbols,
    parse_iso_date,
    run_download,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download public Binance USD-M Futures aggregate trades and resample them into canonical 15s candles."
    )
    parser.add_argument("--project-root", default=".", help="Project root directory.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT", help="Comma-separated symbols.")
    parser.add_argument("--from-date", required=True, help="Inclusive UTC start date, e.g. 2026-01-05.")
    parser.add_argument("--to-date", default=None, help="Inclusive UTC end date. Defaults to yesterday UTC.")
    parser.add_argument("--output-dir", default="data/raw/binance_futures_klines_15s", help="Relative or absolute output directory.")
    parser.add_argument("--source", default="archive_then_rest", choices=["archive", "rest", "archive_then_rest"], help="Public data source strategy.")
    parser.add_argument("--limit", type=int, default=1000, help="Binance REST aggTrades page limit. Max 1000.")
    parser.add_argument("--sleep", type=float, default=0.12, help="Sleep between requests in seconds.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retries per request.")
    parser.add_argument("--no-write", action="store_true", help="Only report planned downloads.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    from_date = parse_iso_date(args.from_date)
    if args.to_date:
        to_date = parse_iso_date(args.to_date)
    else:
        to_date = datetime.now(UTC).date() - timedelta(days=1)
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    config = DownloadConfig(
        project_root=project_root,
        symbols=normalize_symbols(args.symbols.split(",")),
        from_date=from_date,
        to_date=to_date,
        output_dir=output_dir,
        limit=args.limit,
        request_sleep_seconds=args.sleep,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        no_write=args.no_write,
        source_mode=normalize_source_mode(args.source),
    )
    try:
        result = run_download(config)
    except (OSError, ValueError, RuntimeError) as exc:
        result = {
            "schema_version": "binance_futures_aggtrades_to_15s_redownload_v3",
            "status": "blocked",
            "reason": f"{type(exc).__name__}:{exc}",
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "runtime_mode": "paper",
            "live_trading_enabled": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_training_dataset": False,
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"ok", "warning"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
