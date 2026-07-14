#!/usr/bin/env python3
"""Audit the Qlib 24/7 integration ADR contract without operational writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.qlib_backend_environment_lock.integration_mode import (  # noqa: E402
    build_qlib_24x7_integration_mode_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root.")
    parser.add_argument(
        "--contract",
        default=None,
        help="Optional contract path; defaults to config/qlib_integration_mode_v1.json.",
    )
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_qlib_24x7_integration_mode_report(
        project_root=args.project_root,
        contract_path=args.contract,
    )
    indent = None if args.json else 2
    print(json.dumps(report, indent=indent, sort_keys=True, ensure_ascii=False))
    return 0 if report["contract_valid"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
