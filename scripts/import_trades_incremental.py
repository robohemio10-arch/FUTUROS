#!/usr/bin/env python3
"""Fail-closed compatibility CLI for the disabled legacy Master import."""

from __future__ import annotations

import argparse
import json
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox-dir", default="data/trades/inbox")
    parser.add_argument("--master-xlsx", default="data/trades/trades_master.xlsx")
    parser.add_argument("--master-parquet", default="data/trades/trades_master.parquet")
    parser.add_argument("--compatibility-xlsx", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--processed-dir", default="data/trades/processed")
    parser.add_argument("--report", default="data/reports/phase5_import_report.json")
    parser.add_argument("--no-archive", action="store_true")
    return parser.parse_args(argv)


def build_disabled_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "legacy_master_import_disabled",
        "decision": "LEGACY_MASTER_IMPORT_FORBIDDEN",
        "requested_inputs": {
            "inbox_dir": args.inbox_dir,
            "master_xlsx": args.master_xlsx,
            "master_parquet": args.master_parquet,
            "compatibility_xlsx": args.compatibility_xlsx,
            "processed_dir": args.processed_dir,
            "report": args.report,
            "archive_requested": not args.no_archive,
        },
        "import_authorized": False,
        "write_authorized": False,
        "write_performed": False,
        "writes_trader_master": False,
        "writes_parquet": False,
        "writes_xlsx": False,
        "writes_csv": False,
        "writes_sqlite": False,
        "writes_runtime": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "operational_authority": False,
    }


def main(argv: list[str] | None = None) -> int:
    report = build_disabled_report(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
