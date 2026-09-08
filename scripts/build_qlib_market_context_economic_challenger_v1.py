from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.qlib_market_context_economic_challenger import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    build_qlib_market_context_economic_challenger_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train and economically evaluate the native Qlib market-context Paper "
            "challenger with point-in-time 5m features, causal outcome availability, "
            "and three-fold cost-stressed walk-forward evidence."
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
    report = build_qlib_market_context_economic_challenger_v1(
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
            f"pit_coverage={(report.get('point_in_time_alignment') or {}).get('coverage')} "
            f"oos_selected={report.get('oos_selected_trade_count', 0)} "
            f"stressed_net_pnl={treatment.get('stressed_net_pnl_total')}"
        )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
