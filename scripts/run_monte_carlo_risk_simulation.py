from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.monte_carlo_risk_simulation import (
    DEFAULT_INPUT_PATH,
    DEFAULT_REPORT_PATH,
    MINIMUM_TRADES,
    run_monte_carlo_risk_simulation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run paper/shadow Monte Carlo risk simulation.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--initial-capital", type=float, default=1000.0)
    parser.add_argument("--stake", type=float, default=100.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--stress-multiplier", type=float, default=1.0)
    parser.add_argument("--simulations", type=int, default=1000)
    parser.add_argument("--horizon-trades", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ruin-threshold-pct", type=float, default=30.0)
    parser.add_argument("--max-acceptable-drawdown-pct", type=float, default=40.0)
    parser.add_argument("--min-trades", type=int, default=MINIMUM_TRADES)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_monte_carlo_risk_simulation(
        input_path=args.input,
        report_path=args.report,
        initial_capital=args.initial_capital,
        stake=args.stake,
        leverage=args.leverage,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        stress_multiplier=args.stress_multiplier,
        simulations=args.simulations,
        horizon_trades=args.horizon_trades,
        seed=args.seed,
        ruin_threshold_pct=args.ruin_threshold_pct,
        max_acceptable_drawdown_pct=args.max_acceptable_drawdown_pct,
        min_trades=args.min_trades,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "warning", "insufficient_data"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
