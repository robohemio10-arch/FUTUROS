#!/usr/bin/env python
"""Build deterministic paper trade and candle research evidence."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)
from smartcrypto.research.profit_research_dataset.report import stable_json


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a research-only paper trade and candle-aligned dataset."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-profile")
    parser.add_argument("--paper-db")
    parser.add_argument("--paper-snapshot-db")
    parser.add_argument("--candle-root")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--allow-runtime-read", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-dataset", action="store_true")
    parser.add_argument("--output-root")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_build_paths(
        args.project_root,
        source_profile=args.source_profile,
        paper_db=args.paper_db,
        paper_snapshot_db=args.paper_snapshot_db,
        candle_root=args.candle_root,
        output_root=args.output_root,
    )
    result = build_profit_research_dataset(
        paths,
        timeframe=args.timeframe,
        allow_runtime_read=bool(args.allow_runtime_read),
        write_report=bool(args.write_report),
        write_dataset=bool(args.write_dataset),
    )
    print(stable_json(result.report, pretty=not args.json))
    return 0 if result.report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
