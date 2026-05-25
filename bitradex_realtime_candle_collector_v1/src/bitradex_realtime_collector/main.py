from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .config import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES, RuntimeConfig
from .logging_utils import configure_logging
from .processor import DataProcessor
from .scraper import ScraperHandler
from .endpoint_probe import EndpointProbe

LOGGER = logging.getLogger("bitradex.collector.main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bitradex-realtime-candle-collector",
        description="Collect public Bitradex futures OHLCV candles by intercepting chart network traffic.",
    )
    parser.add_argument("--mode", choices=("daemon", "capture", "probe", "export", "stats"), default="daemon")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS), help="Symbols to collect. Default: BTCUSDT ETHUSDT")
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES), help="Timeframes to export. Default: 1m 5m 15m")
    parser.add_argument("--capture-seconds", type=int, default=0, help="0 means run until interrupted. Used by daemon/capture modes.")
    parser.add_argument("--export-every-seconds", type=int, default=60)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--scroll-rounds", type=int, default=30)
    parser.add_argument("--scroll-pause-seconds", type=float, default=0.35)
    parser.add_argument("--replay-backfill-rounds", type=int, default=0, help="Optional hidden endpoint replay rounds discovered at runtime.")
    parser.add_argument("--headful", action="store_true", help="Run Chromium visible for debugging.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-dir", default="data/output")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--runtime-dir", default="data/runtime")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--sqlite-path", default="data/output/bitradex_live_candles.sqlite")
    parser.add_argument("--mirror-phase22-dir", default=None, help="Optional FUTUROS data/raw/bitradex_candles mirror dir. Writes *_live files only.")
    parser.add_argument("--disable-raw-payload-audit", action="store_true")
    parser.add_argument("--disable-endpoint-replay", action="store_true")
    parser.add_argument("--disable-route-validation", action="store_true", help="Emergency diagnostic only: accept redirected pages even if futures route is not confirmed.")
    parser.add_argument("--disable-ticker-aggregation", action="store_true", help="Disable fallback aggregation of live public ticker WebSocket messages into OHLC candles.")
    parser.add_argument("--audit-all-network", action="store_true", help="Diagnostic mode: save all public XHR/fetch previews and WS previews to data/raw/network_audit.jsonl.")
    parser.add_argument("--probe-days", type=int, default=3, help="Direct public endpoint probe lookback window in days.")
    parser.add_argument("--probe-limit", type=int, default=1500, help="Limit parameter used by direct public endpoint probe.")
    parser.add_argument("--probe-concurrency", type=int, default=6, help="Concurrent direct endpoint probe requests.")
    parser.add_argument("--disable-process-all-ws", action="store_true", help="Only process WebSocket frames that contain explicit kline/candle hints.")
    parser.add_argument("--disable-ws-frame-audit", action="store_true", help="Disable raw public WebSocket frame audit JSONL.")
    return parser


def build_config(args: argparse.Namespace) -> RuntimeConfig:
    output_dir = Path(args.output_dir)
    raw_dir = Path(args.raw_dir)
    runtime_dir = Path(args.runtime_dir)
    log_dir = Path(args.log_dir)
    symbols = RuntimeConfig.normalize_symbols(args.symbols)
    timeframes = RuntimeConfig.normalize_timeframes(args.timeframes)
    mirror = Path(args.mirror_phase22_dir) if args.mirror_phase22_dir else None
    return RuntimeConfig(
        symbols=symbols,
        timeframes=timeframes,
        output_dir=output_dir,
        raw_dir=raw_dir,
        runtime_dir=runtime_dir,
        log_dir=log_dir,
        sqlite_path=Path(args.sqlite_path),
        discovered_endpoints_path=runtime_dir / "discovered_endpoints.json",
        raw_payload_jsonl=raw_dir / "captured_payloads.jsonl",
        capture_seconds=int(args.capture_seconds),
        export_every_seconds=max(10, int(args.export_every_seconds)),
        heartbeat_seconds=max(10, int(args.heartbeat_seconds)),
        scroll_rounds=max(0, int(args.scroll_rounds)),
        scroll_pause_seconds=max(0.05, float(args.scroll_pause_seconds)),
        replay_backfill_rounds=max(0, int(args.replay_backfill_rounds)),
        headless=not bool(args.headful),
        mirror_phase22_dir=mirror,
        enable_raw_payload_audit=not bool(args.disable_raw_payload_audit),
        enable_endpoint_replay=not bool(args.disable_endpoint_replay),
        route_validation_required=not bool(args.disable_route_validation),
        enable_ticker_aggregation=not bool(args.disable_ticker_aggregation),
        audit_all_network=bool(args.audit_all_network),
        network_audit_path=raw_dir / "network_audit.jsonl",
        direct_probe_days=max(1, int(args.probe_days)),
        direct_probe_limit=max(100, int(args.probe_limit)),
        direct_probe_concurrency=max(1, int(args.probe_concurrency)),
        process_all_public_ws_frames=not bool(args.disable_process_all_ws),
        save_ws_frame_audit=not bool(args.disable_ws_frame_audit),
        ws_frame_audit_path=raw_dir / "captured_ws_frames.jsonl",
    )


async def run_async(args: argparse.Namespace) -> dict:
    config = build_config(args)
    configure_logging(config.log_dir, args.verbose)
    processor = DataProcessor(config)
    try:
        if args.mode in {"daemon", "capture"}:
            LOGGER.info("collector_start mode=%s config=%s", args.mode, config)
            scraper = ScraperHandler(config, processor)
            await scraper.run()
            return {"status": "ok", "mode": args.mode, "stats": processor.stats(), "export": processor.export_all()}
        if args.mode == "probe":
            LOGGER.info("collector_probe_start config=%s", config)
            probe = EndpointProbe(config, processor)
            return await probe.run()
        if args.mode == "export":
            return {"status": "ok", "mode": "export", "export": processor.export_all(), "stats": processor.stats()}
        if args.mode == "stats":
            return {"status": "ok", "mode": "stats", "stats": processor.stats()}
        raise ValueError(f"Unsupported mode: {args.mode}")
    finally:
        processor.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = asyncio.run(run_async(args))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
