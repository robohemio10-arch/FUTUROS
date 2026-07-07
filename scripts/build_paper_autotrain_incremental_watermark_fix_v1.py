#!/usr/bin/env python3
"""Build the paper autotrain incremental watermark fix diagnostic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (  # noqa: E402
    build_paper_autotrain_incremental_watermark_fix_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON/Markdown reports under data/reports.")
    parser.add_argument(
        "--write-watermark-state",
        action="store_true",
        help="Write the research-only quarantine watermark under data/research.",
    )
    parser.add_argument("--output-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument("--watermark-path", default=None, help="Optional research watermark path.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Block when no records are new after watermark.")
    parser.add_argument(
        "--fail-on-watermark-corruption",
        action="store_true",
        help="Block if an existing watermark state is invalid.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=args.project_root,
        write_report=args.write_report,
        write_watermark_state_requested=args.write_watermark_state,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        watermark_path=args.watermark_path,
        fail_on_stale=args.fail_on_stale,
        fail_on_watermark_corruption=args.fail_on_watermark_corruption,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
