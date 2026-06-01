from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.qlib_engine.paper_refresh_supervisor import (  # noqa: E402
    DEFAULT_NEXT_RUN_SECONDS,
    DEFAULT_REPORT_PATH,
    PaperRefreshSupervisorConfig,
    run_paper_refresh_supervisor,
)


def parse_symbols(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise Qlib paper/shadow refresh cycle.")
    parser.add_argument("--once", action="store_true", help="Run a single cycle. This is the default when interval is omitted.")
    parser.add_argument("--interval-seconds", type=int, default=None, help="Run continuously with this interval unless --once is set.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--market-source", default="data/raw/futures_ohlcv_60d.parquet")
    parser.add_argument("--market-features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--market-report", default="data/reports/qlib_market_features_refresh_report.json")
    parser.add_argument("--model", default="data/models/qlib_market_model.joblib")
    parser.add_argument("--model-config", default="config/qlib_model.yml")
    parser.add_argument("--predictions", default="data/predictions/latest_qlib_predictions.parquet")
    parser.add_argument("--predictions-report", default="data/reports/qlib_fresh_prediction_runner_report.json")
    parser.add_argument("--signal-config", default="config/signal_producer.yml")
    parser.add_argument("--pinned-signals", default="data/runtime/active_freqtrade_signals.json")
    parser.add_argument("--max-prediction-age-minutes", type=int, default=90)
    parser.add_argument("--max-input-data-age-minutes", type=int, default=15)
    parser.add_argument("--phase13-validity-minutes", type=int, default=45)
    parser.add_argument("--next-recommended-run-seconds", type=int, default=DEFAULT_NEXT_RUN_SECONDS)
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--disable-public-download", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> PaperRefreshSupervisorConfig:
    return PaperRefreshSupervisorConfig(
        report_path=args.report,
        market_source_path=args.market_source,
        existing_market_features_path=args.market_features,
        market_features_output_path=args.market_features,
        market_features_report_path=args.market_report,
        qlib_model_path=args.model,
        qlib_model_config_path=args.model_config,
        predictions_output_path=args.predictions,
        predictions_report_path=args.predictions_report,
        signal_config_path=args.signal_config,
        pinned_signals_path=args.pinned_signals,
        max_prediction_age_minutes=args.max_prediction_age_minutes,
        max_input_data_age_minutes=args.max_input_data_age_minutes,
        phase13_validity_minutes=args.phase13_validity_minutes,
        next_recommended_run_seconds=args.next_recommended_run_seconds,
        public_download_enabled=not bool(args.disable_public_download),
        symbols=parse_symbols(args.symbols),
        timeframe=args.timeframe,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = config_from_args(args)
    run_once = bool(args.once) or args.interval_seconds is None

    if run_once:
        report = run_paper_refresh_supervisor(cfg)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if report.get("status") == "ok" else 1

    interval = max(1, int(args.interval_seconds))
    while True:
        report = run_paper_refresh_supervisor(cfg)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
