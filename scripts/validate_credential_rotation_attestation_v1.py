#!/usr/bin/env python3
"""Validate a sanitized credential-rotation attestation without provider access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.security.credential_rotation_attestation import (  # noqa: E402
    validate_credential_rotation_attestation_v1,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--required-inventory", default=None)
    parser.add_argument("--attestation", default=None)
    parser.add_argument("--max-attestation-age-days", type=int, default=30)
    parser.add_argument("--future-tolerance-minutes", type=int, default=5)
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_credential_rotation_attestation_v1(
        project_root=args.project_root,
        required_inventory_path=args.required_inventory,
        attestation_path=args.attestation,
        max_attestation_age_days=max(1, args.max_attestation_age_days),
        future_tolerance_minutes=max(0, args.future_tolerance_minutes),
        max_file_bytes=max(1, args.max_file_bytes),
        write_report=args.write_report,
        report_path=args.report_path,
    )
    print(json.dumps(report, indent=None if args.json else 2, sort_keys=True, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
