from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json

from smartcrypto.analysis.paper_financial_performance import (
    DEFAULT_REPORT_PATH,
    DEFAULT_SOURCE_CANDIDATES,
    INVALID_SCHEMA,
    blocked_payload,
    run_paper_financial_performance_metrics_from_paths,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate read-only paper financial performance metrics.")
    parser.add_argument("--source", default=None)
    parser.add_argument("--source-candidate", action="append", default=None)
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--pnl-column", default=None)
    parser.add_argument("--timestamp-column", default=None)
    parser.add_argument("--symbol-column", default=None)
    parser.add_argument("--side-column", default=None)
    parser.add_argument("--regime-column", default=None)
    parser.add_argument("--strategy-column", default=None)
    parser.add_argument("--minimum-recommended-trades", type=int, default=30)
    parser.add_argument("--require-timestamp", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    candidates = args.source_candidate if args.source_candidate is not None else list(DEFAULT_SOURCE_CANDIDATES)
    try:
        payload = run_paper_financial_performance_metrics_from_paths(
            source_path=args.source,
            source_candidates=candidates,
            report_path=args.report,
            pnl_column=args.pnl_column,
            timestamp_column=args.timestamp_column,
            symbol_column=args.symbol_column,
            side_column=args.side_column,
            regime_column=args.regime_column,
            strategy_column=args.strategy_column,
            minimum_recommended_trades=args.minimum_recommended_trades,
            require_timestamp=bool(args.require_timestamp),
        )
    except Exception as exc:
        payload = blocked_payload(
            INVALID_SCHEMA,
            f"unexpected_error:{exc}",
            source_path=args.source,
            report_path=args.report,
            minimum_recommended_trades=args.minimum_recommended_trades,
        )
        write_json(args.report, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
