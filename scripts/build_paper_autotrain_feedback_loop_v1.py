#!/usr/bin/env python3
"""Build the research-only paper auto-train feedback loop report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_feedback_loop import build_paper_autotrain_feedback_loop_v1  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write", action="store_true", help="Alias for --write-report.")
    parser.add_argument("--write-report", action="store_true", help="Write consolidated reports under data/reports.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Acknowledge explicit runtime data reads when trainers are invoked.")
    parser.add_argument("--run-qlib-train", action="store_true", help="Run existing Qlib challenger trainer in research-only mode.")
    parser.add_argument("--run-ai-shadow-train", action="store_true", help="Run existing IA Shadow quality veto trainer in research-only mode.")
    parser.add_argument("--report-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--report-markdown", default=None, help="Optional Markdown report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autotrain_feedback_loop_v1(
        project_root=args.project_root,
        write_report=bool(args.write or args.write_report),
        allow_runtime_read=args.allow_runtime_read,
        run_qlib_train=args.run_qlib_train,
        run_ai_shadow_train=args.run_ai_shadow_train,
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
