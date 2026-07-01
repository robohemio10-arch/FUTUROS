#!/usr/bin/env python3
"""Run paper-only candidate strategy AB test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_only_candidate_strategy_ab_test import (  # noqa: E402
    build_paper_only_candidate_strategy_ab_test_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Allow explicit local report reads.")
    parser.add_argument("--paper-attribution-report", default=None, help="Path to paper attribution JSON report.")
    parser.add_argument("--impact-report", default=None, help="Path to daily impact JSON report.")
    parser.add_argument("--remediation-report", default=None, help="Path to remediation JSON report.")
    parser.add_argument("--output-report", default=None, help="Optional JSON report path under data/reports.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown report path under data/reports.")
    parser.add_argument("--write", action="store_true", help="Write paper-only candidate report under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_only_candidate_strategy_ab_test_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        paper_attribution_report=args.paper_attribution_report,
        impact_report=args.impact_report,
        remediation_report=args.remediation_report,
        output_report=args.output_report,
        markdown_report=args.markdown_report,
        write=args.write,
        no_write=args.no_write or not args.write,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
