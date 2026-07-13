#!/usr/bin/env python3
"""Profile legacy Trader Master lineage against Fingerprint V2 without mutation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.legacy_lineage_profile import (  # noqa: E402
    DEFAULT_JSON_REPORT,
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_MASTER,
    build_trader_master_legacy_lineage_profile_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--trader-master", default=str(DEFAULT_MASTER))
    parser.add_argument(
        "--source-profile",
        default="config/freqtrade_paper_closed_trades_source_profile_v2.json",
    )
    parser.add_argument("--account-scope-hash", default=None)
    parser.add_argument("--authoritative-sqlite", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true")
    mode.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_REPORT))
    parser.add_argument("--output-markdown", default=str(DEFAULT_MARKDOWN_REPORT))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_trader_master_legacy_lineage_profile_report(
        project_root=args.project_root,
        trader_master_path=args.trader_master,
        source_profile_path=args.source_profile,
        account_scope_hash=args.account_scope_hash,
        authoritative_sqlite_path=args.authoritative_sqlite,
        write_report=args.write_report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
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
