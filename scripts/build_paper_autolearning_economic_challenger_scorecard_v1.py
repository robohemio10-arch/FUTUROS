from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_challenger_scorecard import (
    DEFAULT_OUTCOME_PATH,
    DEFAULT_SELECTOR_FIELD,
    build_paper_autolearning_economic_challenger_scorecard_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build research-only Paper economic challenger scorecard.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--outcomes", default=str(DEFAULT_OUTCOME_PATH))
    parser.add_argument("--selector-field", default=DEFAULT_SELECTOR_FIELD)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_paper_autolearning_economic_challenger_scorecard_v1(
        project_root=Path(args.project_root),
        outcome_path=args.outcomes,
        selector_field=args.selector_field,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(
            f"status={report['status']} decision={report['decision']} "
            f"selected={report['selected_trade_count']} "
            f"pf={report['selected_metrics']['profit_factor']} "
            f"net_pnl={report['selected_metrics']['net_pnl_total']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
