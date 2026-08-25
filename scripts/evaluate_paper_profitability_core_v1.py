#!/usr/bin/env python3
"""Evaluate Paper profitability candidates without changing runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--paper-db")
    parser.add_argument("--market-features")
    parser.add_argument("--model")
    parser.add_argument("--output")
    parser.add_argument("--signal-config", default="config/signal_producer.yml")
    parser.add_argument(
        "--ledger-config",
        default="config/decision_ledger_paper_observability.yml",
    )
    parser.add_argument("--profile-preflight-only", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.write_report and args.no_write:
        raise SystemExit("--write-report and --no-write are mutually exclusive")
    from smartcrypto.research.paper_profitability_core import (
        evaluate_paper_candidate_profile_preflight,
        evaluate_paper_profitability_core,
    )

    if args.profile_preflight_only:
        if args.write_report:
            raise SystemExit("--profile-preflight-only does not write reports")
        report = evaluate_paper_candidate_profile_preflight(
            project_root=args.project_root,
            signal_config_path=args.signal_config,
            ledger_config_path=args.ledger_config,
        )
    else:
        report = evaluate_paper_profitability_core(
            project_root=args.project_root,
            paper_db_path=args.paper_db,
            market_features_path=args.market_features,
            model_path=args.model,
            output_path=args.output,
            write_report=args.write_report,
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        if "decision" in report:
            print(f"DECISION={report['decision']}")
            print(f"CANDIDATE_ELIGIBLE={str(report['candidate_eligible']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
