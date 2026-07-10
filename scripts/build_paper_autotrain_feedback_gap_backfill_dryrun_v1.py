#!/usr/bin/env python3
"""Build the no-write paper autotrain feedback-gap backfill dry-run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_feedback_gap_backfill_dryrun import (  # noqa: E402
    DEFAULT_EXPECTED_PLAN_HASH,
    build_paper_autotrain_feedback_gap_backfill_dryrun_v1,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--plan-report", default=None, help="Input remediation-plan JSON path.")
    parser.add_argument("--feedback-events-path", default=None, help="Current feedback JSONL path.")
    parser.add_argument(
        "--expected-plan-hash",
        default=DEFAULT_EXPECTED_PLAN_HASH,
        help="Required semantic plan hash. Comparison is case-insensitive.",
    )
    parser.add_argument("--write-report", action="store_true", help="Write only JSON/Markdown under data/reports.")
    parser.add_argument("--output-json", default=None, help="Optional JSON output path under data/reports.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown output path under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_autotrain_feedback_gap_backfill_dryrun_v1(
        project_root=args.project_root,
        plan_report_path=args.plan_report,
        feedback_events_path=args.feedback_events_path,
        expected_plan_hash=args.expected_plan_hash,
        write_report=args.write_report,
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
    return 0 if report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
