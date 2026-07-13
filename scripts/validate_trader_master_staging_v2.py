#!/usr/bin/env python3
"""Validate Trader Master staging with fingerprint_spec_v2, read-only by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.staging_runner import (  # noqa: E402
    DEFAULT_JSON_REPORT,
    DEFAULT_KILL_SWITCH,
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_STAGING_PATH,
    build_trader_master_staging_validation_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter import (  # noqa: E402
    build_freqtrade_paper_closed_trades_adapter_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--staging-file", default=None)
    parser.add_argument(
        "--source-profile",
        default=None,
        help="Versioned JSON source profile. Enables the in-memory source adapter.",
    )
    parser.add_argument(
        "--account-scope-hash",
        default=None,
        help="Explicit SHA-256 account-scope hash; never derived from filenames.",
    )
    parser.add_argument(
        "--authoritative-sqlite",
        "--authoritative-sqlite-path",
        dest="authoritative_sqlite",
        default=None,
        help="Optional authoritative paper snapshot override; opened only via a query-only temp copy.",
    )
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_REPORT))
    parser.add_argument("--output-markdown", default=str(DEFAULT_MARKDOWN_REPORT))
    parser.add_argument("--kill-switch-path", default=str(DEFAULT_KILL_SWITCH))
    parser.add_argument("--batch-size", type=int, default=1_000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-report", action="store_true")
    mode.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--write-to-master",
        action="store_true",
        help="Forbidden compatibility probe; always returns blocked without writing.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.source_profile:
        report = build_freqtrade_paper_closed_trades_adapter_report(
            project_root=args.project_root,
            source_profile_path=args.source_profile,
            account_scope_hash=args.account_scope_hash,
            staging_file=args.staging_file,
            authoritative_sqlite_path=args.authoritative_sqlite,
            write_report=args.write_report,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
            write_to_master_requested=args.write_to_master,
        )
    else:
        report = build_trader_master_staging_validation_report(
            project_root=args.project_root,
            staging_file=args.staging_file or DEFAULT_STAGING_PATH,
            write_report=args.write_report,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
            kill_switch_path=args.kill_switch_path,
            batch_size=args.batch_size,
            write_to_master_requested=args.write_to_master,
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
