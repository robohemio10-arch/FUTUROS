from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.qlib_long_economic_policy_challenger_v2 import (
    build_qlib_long_economic_policy_challenger_v2,
)
from smartcrypto.learning.paper_autolearning.qlib_market_context_economic_challenger import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train and evaluate the research-only Qlib V2 challenger using absolute "
            "stressed Net PnL with explicit long-only treatment eligibility."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--outcome-path", default=None)
    parser.add_argument("--market-features-path", default=None)
    parser.add_argument(
        "--additional-execution-stress-bps",
        type=float,
        default=DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_qlib_long_economic_policy_challenger_v2(
        project_root=Path(args.project_root),
        outcome_path=args.outcome_path,
        market_features_path=args.market_features_path,
        additional_execution_stress_bps=args.additional_execution_stress_bps,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    else:
        evaluation = report.get("economic_evaluation") or {}
        treatment = evaluation.get("aggregate_treatment_metrics") or {}
        print(
            f"status={report['status']} decision={report['decision']} "
            f"reason={report['reason']}"
        )
        print(
            f"native_qlib_used={report['native_qlib_used']} "
            f"oos_selected={report.get('oos_selected_trade_count', 0)} "
            f"stressed_net_pnl={treatment.get('stressed_net_pnl_total')} "
            f"stressed_pf={treatment.get('stressed_profit_factor')}"
        )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
