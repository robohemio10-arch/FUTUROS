#!/usr/bin/env python3
"""Run daily paper auto-training with the profit-first financial objective."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_financial_objective import (  # noqa: E402
    build_profit_aware_daily_autotrain,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--once", action="store_true", help="Execute one quarantine research cycle.")
    parser.add_argument("--write-feedback", action="store_true", help="Write quarantine feedback events under data/feedback.")
    parser.add_argument("--train-challenger", action="store_true", help="Train financially weighted quarantine challengers when eligible.")
    parser.add_argument("--write-quarantine-artifacts", action="store_true", help="Write quarantine research/model/registry artifacts.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON/Markdown report under data/reports.")
    parser.add_argument("--dry-run", action="store_true", help="Plan the cycle without writes.")
    parser.add_argument("--scheduler-check", action="store_true", help="Check scheduler readiness without registering a scheduler.")
    parser.add_argument("--fail-on-operational-write", action="store_true", help="Block if a computed write path is outside allowed quarantine roots.")
    parser.add_argument("--output-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_profit_aware_daily_autotrain(
        Path(args.project_root).resolve(),
        once=args.once,
        write_feedback=args.write_feedback and not args.dry_run,
        train_challenger=args.train_challenger,
        write_quarantine_artifacts=args.write_quarantine_artifacts and not args.dry_run,
        write_report=args.write_report and not args.dry_run,
        dry_run=args.dry_run,
        scheduler_check=args.scheduler_check,
        fail_on_operational_write=args.fail_on_operational_write,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(
        json.dumps(
            report,
            indent=None if args.json else 2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
