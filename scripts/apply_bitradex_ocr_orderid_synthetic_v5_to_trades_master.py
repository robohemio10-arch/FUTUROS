#!/usr/bin/env python3
"""Fail-closed compatibility CLI for a retired official-dataset apply path."""

from __future__ import annotations

import argparse
import json
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_disabled_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "legacy_official_dataset_apply_disabled",
        "decision": "LEGACY_DATASET_APPLY_FORBIDDEN",
        "package_dir": args.package_dir,
        "project_root": args.project_root,
        "no_write": True,
        "import_authorized": False,
        "write_authorized": False,
        "write_performed": False,
        "backup_created": False,
        "writes_official_dataset": False,
        "writes_parquet": False,
        "writes_xlsx": False,
        "writes_csv": False,
        "writes_sqlite": False,
        "writes_runtime": False,
        "changes_training_dataset": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "operational_authority": False,
    }


def main(argv: list[str] | None = None) -> int:
    report = build_disabled_report(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
