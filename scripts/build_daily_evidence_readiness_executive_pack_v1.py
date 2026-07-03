#!/usr/bin/env python3
"""Build the SMART FUTUROS daily evidence/readiness executive pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ops.daily_evidence_readiness_executive_pack import (  # noqa: E402
    build_daily_evidence_readiness_executive_pack_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON/Markdown/HTML under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode. This is the default.")
    parser.add_argument("--report-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--report-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument("--report-html", default=None, help="Optional HTML report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=args.project_root,
        write_report=bool(args.write_report and not args.no_write),
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
        report_html_path=args.report_html,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
