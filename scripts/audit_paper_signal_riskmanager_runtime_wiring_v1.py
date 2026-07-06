#!/usr/bin/env python3
"""Audit RiskManager runtime wiring on the paper signal path (read-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ops.paper_signal_riskmanager_runtime_wiring_audit import (  # noqa: E402
    build_audit_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--output-report", default=None, help="Optional JSON report path under data/reports.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown report path under data/reports.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON and Markdown reports under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode (default).")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_audit_report(
        project_root=args.project_root,
        output_report=args.output_report,
        markdown_report=args.markdown_report,
        write=args.write_report,
        no_write=args.no_write or not args.write_report,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
