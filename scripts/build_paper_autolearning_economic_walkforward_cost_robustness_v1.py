from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_walkforward_cost_robustness import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    build_paper_autolearning_economic_walkforward_cost_robustness_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Paper selector economics across chronological walk-forward folds "
            "with incremental execution-cost stress."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--outcome-path", default=None)
    parser.add_argument("--selector-field", default="paper_candidate_filter_decision")
    parser.add_argument(
        "--additional-execution-stress-bps",
        type=float,
        default=DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=Path(args.project_root),
        outcome_path=args.outcome_path,
        selector_field=args.selector_field,
        additional_execution_stress_bps=args.additional_execution_stress_bps,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"status={report['status']} decision={report['decision']} "
            f"reason={report['reason']}"
        )
        print(
            "aggregate_delta_stressed_net_pnl="
            f"{report['aggregate_delta_stressed_net_pnl']} "
            "positive_delta_net_pnl_fold_count="
            f"{report['positive_delta_net_pnl_fold_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
