"""Run point-in-time 5m rematerialization and research-only training rounds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.market_features_first_training_runs import (  # noqa: E402
    PipelineConfig,
    resolve_paths,
    run_market_features_first_training_pipeline,
)
from smartcrypto.research.market_features_first_training_runs.reporting import (  # noqa: E402
    json_safe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rematerialize closed 5m point-in-time features and run ephemeral "
            "research-only challenger evaluations."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--allow-paper-read", action="store_true")
    parser.add_argument("--rematerialize-features", action="store_true")
    parser.add_argument("--run-baselines", action="store_true")
    parser.add_argument("--run-supervised-training", action="store_true")
    parser.add_argument("--run-qlib-training", action="store_true")
    parser.add_argument("--run-walkforward", action="store_true")
    parser.add_argument("--run-backtest", action="store_true")
    parser.add_argument("--run-monte-carlo", action="store_true")
    parser.add_argument("--evaluate-paper-holdout", action="store_true")
    parser.add_argument("--json", action="store_true")
    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument(
        "--write-research-artifacts",
        dest="write_research_artifacts",
        action="store_true",
    )
    write_group.add_argument(
        "--no-write",
        dest="write_research_artifacts",
        action="store_false",
    )
    parser.set_defaults(write_research_artifacts=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = PipelineConfig(
        allow_paper_read=args.allow_paper_read,
        rematerialize_features=args.rematerialize_features,
        run_baselines=args.run_baselines,
        run_supervised_training=args.run_supervised_training,
        run_qlib_training=args.run_qlib_training,
        run_walkforward=args.run_walkforward,
        run_backtest=args.run_backtest,
        run_monte_carlo=args.run_monte_carlo,
        evaluate_paper_holdout=args.evaluate_paper_holdout,
        write_research_artifacts=args.write_research_artifacts,
    )
    result = run_market_features_first_training_pipeline(
        resolve_paths(args.project_root),
        config,
    )
    payload = json_safe(result.report)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"ok", "warning", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
