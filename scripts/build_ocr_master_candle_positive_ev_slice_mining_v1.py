#!/usr/bin/env python3
"""Build OCR Master + Candle positive-EV slice mining report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_master_candle_positive_ev_slice_mining import (  # noqa: E402
    build_positive_ev_slice_mining_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Explicitly allow read-only runtime/data source reads.")
    parser.add_argument("--trades-master", default=None, help="Path to trades_master source. Defaults to data/trades/trades_master.xlsx when runtime reads are allowed.")
    parser.add_argument("--candle-root", action="append", default=[], help="Root(s) used to discover canonical BTC/ETH candle files.")
    parser.add_argument("--min-trade-count", type=int, default=30, help="Minimum trades required for a candidate slice.")
    parser.add_argument("--max-day-concentration", type=float, default=0.35, help="Maximum single-day concentration allowed for a candidate slice.")
    parser.add_argument("--alignment-tolerance-seconds", type=int, default=300, help="Maximum lag for nearest-prior candle alignment.")
    parser.add_argument("--write", action="store_true", help="Write JSON report to data/reports. Disabled by --no-write.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_positive_ev_slice_mining_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        trades_master=args.trades_master,
        candle_roots=args.candle_root or None,
        min_trade_count=args.min_trade_count,
        max_day_concentration=args.max_day_concentration,
        alignment_tolerance_seconds=args.alignment_tolerance_seconds,
        write=args.write,
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
