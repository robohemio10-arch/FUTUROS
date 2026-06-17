#!/usr/bin/env python3
"""Audit local 15s candle coverage for SMART FUTUROS research validation."""

from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.research.historical_validation_15s import ValidationInputs, audit_15s_candle_coverage, print_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit local 15s candle coverage without touching runtime state.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--timeframe", default="15s")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--json", action="store_true", help="Print JSON output. This script always emits JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    symbols = tuple(item.strip().upper().replace("/", "") for item in args.symbols.split(",") if item.strip())
    report = audit_15s_candle_coverage(
        ValidationInputs(
            project_root=Path(args.project_root),
            from_date=str(args.from_date),
            timeframe=str(args.timeframe),
            required_symbols=symbols,
        )
    )
    print_json(report)
    return 0 if report.get("status") in {"ok", "warning", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
