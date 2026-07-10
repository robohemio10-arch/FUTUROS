#!/usr/bin/env python3
"""Scan or build a sanitized, allowlisted evidence bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.security.evidence_bundle_redaction import (  # noqa: E402
    build_sanitized_evidence_bundle_v1,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source", default=None, help="Explicit file, directory, or ZIP source.")
    parser.add_argument(
        "--allow-file",
        action="append",
        default=[],
        help="Exact POSIX-style relative path permitted in a directory/ZIP; repeat as needed.",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", default=None)
    parser.add_argument("--build-sanitized-bundle", action="store_true")
    parser.add_argument("--output-dir", default=None, help="Explicit directory under data/reports/evidence_bundles.")
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000)
    parser.add_argument(
        "--compose-output-mode",
        choices=("not-compose", "interpolated", "no-interpolate"),
        default="not-compose",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_sanitized_evidence_bundle_v1(
        project_root=args.project_root,
        source=args.source,
        allowed_files=args.allow_file,
        write_report=args.write_report,
        report_path=args.report_path,
        build_sanitized_bundle=args.build_sanitized_bundle,
        output_dir=args.output_dir,
        max_file_bytes=max(1, args.max_file_bytes),
        compose_output_mode=args.compose_output_mode,
    )
    print(json.dumps(report, indent=None if args.json else 2, sort_keys=True, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
