#!/usr/bin/env python3
"""Preview or explicitly write Phase14 feedback lineage reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autolearning.lineage_reconciliation import (  # noqa: E402
    reconcile_feedback_lineage_files,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source", default=None)
    parser.add_argument("--outcome-events", default=None)
    parser.add_argument("--feedback-store", default=None)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist reconciliation only under data/feedback. Default is preview-only.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = reconcile_feedback_lineage_files(
        project_root=args.project_root,
        source_path=args.source,
        outcome_events_path=args.outcome_events,
        feedback_store_path=args.feedback_store,
        write=args.write,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
