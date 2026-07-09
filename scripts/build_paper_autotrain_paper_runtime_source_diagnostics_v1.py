#!/usr/bin/env python3
"""Build the paper autotrain paper runtime source diagnostics report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics import (  # noqa: E402
    build_paper_autotrain_paper_runtime_source_diagnostics_v1,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON/Markdown reports under data/reports.")
    parser.add_argument("--output-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument(
        "--allow-paper-db-read",
        action="store_true",
        help="Explicitly allow read-only inspection of the local paper SQLite DB.",
    )
    parser.add_argument("--paper-db-path", default=None, help="Optional explicit paper SQLite DB path.")
    parser.add_argument(
        "--fail-on-missing-paper-db",
        action="store_true",
        help="Keep a missing/unreadable paper DB as an explicit blocker.",
    )
    parser.add_argument(
        "--fail-on-new-db-trades",
        action="store_true",
        help="Add an explicit blocker when read-only paper DB inspection finds new records.",
    )
    parser.add_argument(
        "--fail-on-source-divergence",
        action="store_true",
        help="Add an explicit blocker when paper/research sources diverge.",
    )
    parser.add_argument(
        "--fail-on-missing-watermark",
        action="store_true",
        help="Add an explicit blocker when the watermark state is missing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=args.project_root,
        paper_db_path=args.paper_db_path,
        allow_paper_db_read=args.allow_paper_db_read,
        write_report=args.write_report,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        fail_on_missing_paper_db=args.fail_on_missing_paper_db,
        fail_on_new_db_trades=args.fail_on_new_db_trades,
        fail_on_source_divergence=args.fail_on_source_divergence,
        fail_on_missing_watermark=args.fail_on_missing_watermark,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
