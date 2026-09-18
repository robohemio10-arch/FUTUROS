#!/usr/bin/env python3
"""Build the SMART x AIBOT 3991 economic benchmark in read-only research mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.economic_benchmark import (  # noqa: E402
    EconomicBenchmarkError,
    build_economic_benchmark,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-path", required=True)
    parser.add_argument("--paper-path", required=True)
    parser.add_argument("--sqlite-path", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-enforce-frozen-baseline",
        action="store_true",
        help="Synthetic/dev fixtures only; do not use for Branch 08 acceptance.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_economic_benchmark(
            master_path=args.master_path,
            paper_path=args.paper_path,
            sqlite_path=args.sqlite_path,
            enforce_master_hash=not args.no_enforce_frozen_baseline,
            enforce_frozen_baseline=not args.no_enforce_frozen_baseline,
        )
    except EconomicBenchmarkError as exc:
        payload = {
            "status": "blocked",
            "reason": str(exc),
            "operational_authority": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
        }
        print(
            json.dumps(
                payload,
                indent=None if args.json else 2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 2

    print(
        json.dumps(
            report,
            indent=None if args.json else 2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
