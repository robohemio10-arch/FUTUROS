#!/usr/bin/env python3
"""Analyze five paper-trade quarantines from an authoritative SQLite snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.quarantine_forensics import (  # noqa: E402
    DEFAULT_SOURCE_PROFILE,
    build_targeted_quarantine_forensics_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-profile", default=str(DEFAULT_SOURCE_PROFILE))
    parser.add_argument(
        "--authoritative-sqlite",
        default=None,
        help="Optional snapshot override; runtime SQLite paths remain forbidden by the profile.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_targeted_quarantine_forensics_report(
        project_root=args.project_root,
        source_profile_path=args.source_profile,
        authoritative_sqlite_path=args.authoritative_sqlite,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            indent=None if args.json else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
