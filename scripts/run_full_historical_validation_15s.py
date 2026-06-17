#!/usr/bin/env python3
"""Run full paper/shadow historical validation over available local datasets."""

from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.research.historical_validation_15s import print_json, run_full_historical_validation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run research-only 15s full historical validation.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--timeframe", default="15s")
    parser.add_argument("--min-trades", type=int, default=3000)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON output. This script always emits JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_full_historical_validation(
        project_root=Path(args.project_root),
        from_date=str(args.from_date),
        timeframe=str(args.timeframe),
        no_write=bool(args.no_write),
        min_trades=int(args.min_trades),
        iterations=int(args.iterations),
    )
    print_json(report)
    return 0 if report.get("status") in {"ok", "warning", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
