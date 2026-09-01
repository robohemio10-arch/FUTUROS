#!/usr/bin/env python3
"""Build the research-only AIBOT behavioral fingerprint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity import (  # noqa: E402
    SOURCE_INVESTMENT_ID,
    build_aibot_benchmark,
    build_cli_payload,
)
from smartcrypto.research.aibot_parity.trader_master_loader import (  # noqa: E402
    DEFAULT_TRADER_MASTER_SOURCE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_TRADER_MASTER_SOURCE),
        help="Trader Master Parquet, XLSX, or CSV inside the project root.",
    )
    parser.add_argument(
        "--source-investment-id",
        default=SOURCE_INVESTMENT_ID,
    )
    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument("--write-report", action="store_true")
    write_mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_aibot_benchmark(
        project_root=args.project_root,
        trader_master_path=args.source,
        source_investment_id=args.source_investment_id,
        write_reports=args.write_report,
    )
    payload = build_cli_payload(report, "fingerprint")
    print(
        json.dumps(
            payload,
            indent=None if args.json else 2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
