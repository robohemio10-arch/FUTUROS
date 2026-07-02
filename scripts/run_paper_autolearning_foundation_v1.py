#!/usr/bin/env python3
"""Run paper/shadow auto-learning foundation loop."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autolearning import (  # noqa: E402
    build_paper_autolearning_foundation_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--source", default=None, help="Optional closed trades CSV/parquet/json source.")
    parser.add_argument("--feedback-store", default=None, help="Optional feedback store output path.")
    parser.add_argument("--outcome-events", default=None, help="Optional outcome events output path.")
    parser.add_argument("--microbatch-dir", default=None, help="Optional training microbatch output directory.")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown report path.")
    parser.add_argument("--write-feedback", action="store_true", help="Write only data/feedback and data/reports artifacts.")
    parser.add_argument("--train-smoke", action="store_true", help="Run advisory challenger training smoke checks.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write audit mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autolearning_foundation_report(
        project_root=args.project_root,
        source_path=args.source,
        feedback_store_path=args.feedback_store,
        outcome_events_path=args.outcome_events,
        microbatch_dir=args.microbatch_dir,
        report_path=args.report,
        markdown_report_path=args.markdown_report,
        write_feedback=bool(args.write_feedback and not args.no_write),
        train_smoke=bool(args.train_smoke),
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
