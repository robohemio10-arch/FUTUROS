from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.walkforward_montecarlo import (  # noqa: E402
    WalkForwardMonteCarloConfig,
    resolve_paths,
    run_walkforward_montecarlo,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OCR V1.1 walk-forward and Monte Carlo research pack."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--trade-outcomes-path", default=None)
    parser.add_argument("--walkforward-output-path", default=None)
    parser.add_argument("--monte-carlo-output-path", default=None)
    parser.add_argument("--report-path", default=None)
    parser.add_argument("--executive-report-path", default=None)
    parser.add_argument("--summary-path", default=None)
    parser.add_argument("--min-train-rows", type=int, default=600)
    parser.add_argument("--test-rows", type=int, default=200)
    parser.add_argument("--embargo-rows", type=int, default=10)
    parser.add_argument("--max-folds", type=int, default=12)
    parser.add_argument("--monte-carlo-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--block-size", type=int, default=20)
    parser.add_argument("--ruin-level-usdt", type=float, default=0.0)
    parser.add_argument("--max-allowed-risk-of-ruin", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-ram-gb", type=float, default=16.0)
    parser.add_argument("--json", action="store_true")

    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument("--write", dest="write", action="store_true")
    write_group.add_argument("--no-write", dest="write", action="store_false")
    parser.set_defaults(write=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    paths = resolve_paths(
        args.project_root,
        trade_outcomes_path=args.trade_outcomes_path,
        walkforward_output_path=args.walkforward_output_path,
        monte_carlo_output_path=args.monte_carlo_output_path,
        report_path=args.report_path,
        executive_report_path=args.executive_report_path,
        summary_path=args.summary_path,
    )
    config = WalkForwardMonteCarloConfig(
        min_train_rows=args.min_train_rows,
        test_rows=args.test_rows,
        embargo_rows=args.embargo_rows,
        max_folds=args.max_folds,
        monte_carlo_iterations=args.monte_carlo_iterations,
        seed=args.seed,
        block_size=args.block_size,
        ruin_level_usdt=args.ruin_level_usdt,
        max_allowed_risk_of_ruin=args.max_allowed_risk_of_ruin,
        workers=args.workers,
        max_ram_gb=args.max_ram_gb,
    )
    result = run_walkforward_montecarlo(paths, config, write=args.write)

    payload = result.report
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))

    return 0 if payload.get("status") in {"ok", "warning", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
