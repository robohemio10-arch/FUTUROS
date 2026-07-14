#!/usr/bin/env python3
"""Build OCR Master + Candle positive-rule OOS validation report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_master_candle_positive_rule_oos_validation import (  # noqa: E402
    build_positive_rule_oos_validation_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Explicitly allow read-only runtime/data source reads.")
    parser.add_argument("--legacy-trade-dataset", default=None, help="Explicit research-only legacy trade dataset path.")
    parser.add_argument("--candle-root", action="append", default=[], help="Root(s) used to discover canonical BTC/ETH candle files.")
    parser.add_argument("--min-trade-count", type=int, default=30, help="Minimum in-sample trades required for a candidate slice.")
    parser.add_argument("--max-day-concentration", type=float, default=0.35, help="Maximum single-day concentration allowed.")
    parser.add_argument("--min-oos-trade-count", type=int, default=8, help="Minimum trades required in aggregate OOS evaluation.")
    parser.add_argument("--min-oos-pass-ratio", type=float, default=0.60, help="Minimum fraction of evaluated OOS folds that must pass.")
    parser.add_argument("--min-oos-folds", type=int, default=3, help="Minimum evaluated OOS folds required for a survivor.")
    parser.add_argument("--alignment-tolerance-seconds", type=int, default=300, help="Maximum lag for nearest-prior candle alignment.")
    parser.add_argument("--write", action="store_true", help="Write JSON report to data/reports. Disabled by --no-write.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_positive_rule_oos_validation_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        legacy_trade_dataset=args.legacy_trade_dataset,
        candle_roots=args.candle_root or None,
        min_trade_count=args.min_trade_count,
        max_day_concentration=args.max_day_concentration,
        min_oos_trade_count=args.min_oos_trade_count,
        min_oos_pass_ratio=args.min_oos_pass_ratio,
        min_oos_folds=args.min_oos_folds,
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
