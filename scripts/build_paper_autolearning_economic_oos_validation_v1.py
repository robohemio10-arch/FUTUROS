from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_oos_validation import (
    build_paper_autolearning_economic_oos_validation_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Paper selector economics on a chronological OOS holdout.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--outcome-path", default=None)
    parser.add_argument("--selector-field", default="paper_candidate_filter_decision")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=Path(args.project_root),
        outcome_path=args.outcome_path,
        selector_field=args.selector_field,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"status={report['status']} decision={report['decision']} reason={report['reason']}")
        print(
            "oos_trade_count="
            f"{report['oos_trade_count']} oos_selected_trade_count={report['oos_selected_trade_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
