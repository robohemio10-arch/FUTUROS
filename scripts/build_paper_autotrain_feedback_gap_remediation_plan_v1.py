#!/usr/bin/env python3
"""Build the research-only paper autotrain feedback-gap remediation plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_feedback_gap_remediation_plan import (  # noqa: E402
    build_paper_autotrain_feedback_gap_remediation_plan_v1,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument(
        "--diagnostics-report",
        default=None,
        help="Input feedback-gap diagnostics JSON. Defaults to the canonical data/reports path.",
    )
    parser.add_argument("--write-report", action="store_true", help="Write only JSON/Markdown under data/reports.")
    parser.add_argument("--output-json", default=None, help="Optional JSON output path under data/reports.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown output path under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_autotrain_feedback_gap_remediation_plan_v1(
        project_root=args.project_root,
        diagnostics_report_path=args.diagnostics_report,
        write_report=args.write_report,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    indent = None if args.json else 2
    print(json.dumps(report, indent=indent, sort_keys=True, ensure_ascii=False, default=str))
    return 0 if report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
