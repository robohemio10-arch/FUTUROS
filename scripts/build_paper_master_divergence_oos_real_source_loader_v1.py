#!/usr/bin/env python3
"""Build Paper/Master divergence OOS real source loader report.

Standalone-safe CLI. It can run as:
    python scripts/build_paper_master_divergence_oos_real_source_loader_v1.py --no-write --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_master_divergence_oos_real_source_loader import (  # noqa: E402
    build_paper_master_divergence_oos_real_source_loader_report,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a research-only/read-only real source loader report for Paper/Master OOS divergence.",
    )
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--paper-source", default=None, help="Optional Paper trade source path. Runtime read is opt-in.")
    parser.add_argument("--master-source", default=None, help="Optional Master trade source path. Runtime read is opt-in.")
    parser.add_argument(
        "--allow-runtime-read",
        action="store_true",
        help="Explicitly allow read-only loading of the provided source files.",
    )
    parser.add_argument("--include-loaded-rows", action="store_true", help="Include normalized rows in JSON output.")
    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument("--write", action="store_true", help="Write report to data/reports.")
    write_group.add_argument("--no-write", action="store_true", help="Do not write report. This is the default.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    write_requested = bool(args.write and not args.no_write)
    report = build_paper_master_divergence_oos_real_source_loader_report(
        project_root=args.project_root,
        paper_source=args.paper_source,
        master_source=args.master_source,
        allow_runtime_read=bool(args.allow_runtime_read),
        write=write_requested,
        include_loaded_rows=bool(args.include_loaded_rows),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
