#!/usr/bin/env python3
"""Audit the locked Bitradex OCR batch through Trader Master V2 contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.bitradex_ocr_adapter import (  # noqa: E402
    DEFAULT_JSON_REPORT,
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_MASTER,
    DEFAULT_PROFILE,
    build_bitradex_ocr_readonly_adapter_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--account-scope-hash", default=None)
    parser.add_argument("--package-v4", default=None)
    parser.add_argument("--package-v5", default=None)
    parser.add_argument("--trader-master", default=str(DEFAULT_MASTER))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true")
    mode.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_REPORT))
    parser.add_argument("--output-markdown", default=str(DEFAULT_MARKDOWN_REPORT))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_bitradex_ocr_readonly_adapter_report(
        project_root=args.project_root,
        source_profile_path=args.source_profile,
        account_scope_hash=args.account_scope_hash,
        package_v4=args.package_v4,
        package_v5=args.package_v5,
        trader_master_path=args.trader_master,
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
