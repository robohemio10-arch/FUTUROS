from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.risk.monte_carlo_risk_budget_policy import (  # noqa: E402
    DEFAULT_MONTE_CARLO_REPORT,
    DEFAULT_POLICY_REPORT,
    build_monte_carlo_risk_budget_policy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build paper/shadow Monte Carlo risk budget and position sizing policy."
    )
    parser.add_argument("--monte-carlo-report", type=Path, default=DEFAULT_MONTE_CARLO_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_POLICY_REPORT)
    parser.add_argument("--risk-of-ruin-cap", type=float, default=0.05)
    parser.add_argument("--max-drawdown-cap-pct", type=float, default=40.0)
    parser.add_argument("--min-profit-factor", type=float, default=1.1)
    parser.add_argument("--min-expectancy", type=float, default=0.0)
    parser.add_argument("--initial-capital", type=float)
    parser.add_argument("--current-stake", type=float)
    parser.add_argument("--current-leverage", type=float)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_monte_carlo_risk_budget_policy(
        monte_carlo_report=args.monte_carlo_report,
        output=args.output,
        risk_of_ruin_cap=args.risk_of_ruin_cap,
        max_drawdown_cap_pct=args.max_drawdown_cap_pct,
        min_profit_factor=args.min_profit_factor,
        min_expectancy=args.min_expectancy,
        initial_capital=args.initial_capital,
        current_stake=args.current_stake,
        current_leverage=args.current_leverage,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 1 if report.get("status") == "blocked" and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
