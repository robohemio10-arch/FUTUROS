#!/usr/bin/env python3
"""Build OCR Shadow Research explicit evidence pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_shadow_research_explicit_evidence_pack import (  # noqa: E402
    build_ocr_shadow_research_explicit_evidence_pack_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Allow explicit local report/runtime reads.")
    parser.add_argument("--execute-builders", action="store_true", help="Execute fixed allowlisted evidence builders.")
    parser.add_argument("--stage", action="append", default=None, help="Optional allowlisted stage id. May be repeated.")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Timeout per stage in seconds.")
    parser.add_argument("--output-report", default=None, help="Optional JSON pack path under data/reports.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown pack path under data/reports.")
    parser.add_argument("--write", action="store_true", help="Write research-only JSON and Markdown pack to data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        execute_builders=args.execute_builders,
        selected_stage_ids=args.stage,
        output_report=args.output_report,
        markdown_report=args.markdown_report,
        timeout_seconds=args.timeout_seconds,
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
