#!/usr/bin/env python3
"""Build the research-only event-driven backtest execution cost gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.event_driven_backtest_execution_cost_gate import (  # noqa: E402
    build_event_driven_backtest_execution_cost_gate_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON/Markdown under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode. This is the default.")
    parser.add_argument("--report-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--report-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument("--maker-fee-bps", type=float, default=2.0)
    parser.add_argument("--taker-fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--spread-bps", type=float, default=1.0)
    parser.add_argument("--funding-bps-per-position", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_event_driven_backtest_execution_cost_gate_v1(
        project_root=args.project_root,
        write_report=bool(args.write_report and not args.no_write),
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        funding_bps_per_position=args.funding_bps_per_position,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
