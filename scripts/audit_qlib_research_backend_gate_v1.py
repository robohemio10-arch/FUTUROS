#!/usr/bin/env python3
"""Audit Qlib research backend dependency availability without side effects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.qlib_backend_gate import build_qlib_research_backend_gate_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--report-json", default=None, help="Optional report JSON output path.")
    parser.add_argument("--report-markdown", default=None, help="Optional report Markdown output path.")
    parser.add_argument("--write", action="store_true", help="Write report files under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_qlib_research_backend_gate_report(
        project_root=args.project_root,
        write=args.write,
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
