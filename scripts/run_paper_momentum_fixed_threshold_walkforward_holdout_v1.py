#!/usr/bin/env python
"""Run fixed-threshold paper momentum walk-forward validation read-only."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_momentum_fixed_threshold_walkforward_holdout import (  # noqa: E402
    run_fixed_threshold_momentum_validation,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate only the two frozen paper momentum filters against control "
            "with temporal walk-forward and a chronologically isolated replay holdout."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-profile")
    parser.add_argument("--paper-db")
    parser.add_argument("--paper-snapshot-db")
    parser.add_argument("--candle-root")
    parser.add_argument("--allow-runtime-read", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_fixed_threshold_momentum_validation(
        args.project_root,
        source_profile=args.source_profile,
        paper_db=args.paper_db,
        paper_snapshot_db=args.paper_snapshot_db,
        candle_root=args.candle_root,
        allow_runtime_read=bool(args.allow_runtime_read),
    )
    print(
        json.dumps(
            result.report,
            indent=None if args.json else 2,
            sort_keys=True,
            default=str,
        )
    )
    return 0 if result.report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
