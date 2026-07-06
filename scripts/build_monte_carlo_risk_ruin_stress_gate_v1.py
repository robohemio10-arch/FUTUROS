#!/usr/bin/env python3
"""Build the research-only Monte Carlo risk-of-ruin stress gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.risk.monte_carlo_risk_ruin_stress_gate import (  # noqa: E402
    build_monte_carlo_risk_ruin_stress_gate_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write", action="store_true", help="Write JSON/Markdown under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode. This is the default.")
    parser.add_argument("--report-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--report-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--simulation-count", type=int, default=1000)
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--capital-floor", type=float, default=70.0)
    parser.add_argument("--ruin-floor", type=float, default=50.0)
    parser.add_argument("--cost-per-trade", type=float, default=0.02)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=args.project_root,
        write=bool(args.write and not args.no_write),
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
        seed=args.seed,
        simulation_count=args.simulation_count,
        sample_size=args.sample_size,
        initial_capital=args.initial_capital,
        capital_floor=args.capital_floor,
        ruin_floor=args.ruin_floor,
        cost_per_trade=args.cost_per_trade,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
