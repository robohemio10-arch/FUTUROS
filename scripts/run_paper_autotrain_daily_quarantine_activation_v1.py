#!/usr/bin/env python3
"""Run the daily paper auto-training quarantine activation flow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (  # noqa: E402
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    build_paper_autotrain_daily_quarantine_activation_v1,
    render_markdown,
    resolve,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--once", action="store_true", help="Execute one quarantine research cycle.")
    parser.add_argument("--write-feedback", action="store_true", help="Write quarantine feedback events under data/feedback.")
    parser.add_argument("--train-challenger", action="store_true", help="Train quarantine challenger models if inputs/backends are available.")
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
    root = Path(args.project_root).resolve()
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=root,
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
    if args.write_report and not args.dry_run:
        output_json = resolve(root, args.output_json, DEFAULT_REPORT_JSON)
        output_markdown = resolve(root, args.output_markdown, DEFAULT_REPORT_MD)
        report["output_paths"]["report_json"] = str(output_json)
        report["output_paths"]["report_markdown"] = str(output_markdown)
        report["write_performed"] = True
        write_json(output_json, report)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
