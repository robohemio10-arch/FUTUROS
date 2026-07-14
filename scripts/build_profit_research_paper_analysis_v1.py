#!/usr/bin/env python
"""Build the read-only paper profit research report and analytical dataset."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smartcrypto.research.profit_research import (
    build_profit_research,
    resolve_profit_research_paths,
)
from smartcrypto.research.profit_research.paper_analysis import stable_json


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze authoritative paper trades and point-in-time candles without "
            "changing trading runtime."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-profile")
    parser.add_argument("--snapshot-db")
    parser.add_argument("--runtime-db")
    parser.add_argument("--closed-trades-csv")
    parser.add_argument("--feedback")
    parser.add_argument("--microbatch")
    parser.add_argument("--candles")
    parser.add_argument("--trader-master")
    parser.add_argument("--new-trades-source")
    parser.add_argument("--ocr-handoff")
    parser.add_argument("--output-dataset")
    parser.add_argument("--report-json")
    parser.add_argument("--report-markdown")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true", help="Analyze in memory (default).")
    mode.add_argument(
        "--write",
        action="store_true",
        help="Write only research dataset and reports under the configured paths.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_profit_research_paths(
        args.project_root,
        source_profile=args.source_profile,
        snapshot_db=args.snapshot_db,
        runtime_db=args.runtime_db,
        closed_trades_csv=args.closed_trades_csv,
        feedback=args.feedback,
        microbatch=args.microbatch,
        candles=args.candles,
        trader_master=args.trader_master,
        new_trades_source=args.new_trades_source,
        ocr_handoff=args.ocr_handoff,
        output_dataset=args.output_dataset,
        report_json=args.report_json,
        report_markdown=args.report_markdown,
    )
    result = build_profit_research(paths, write=bool(args.write))
    print(stable_json(result.report, pretty=not args.json))
    return {"ok": 0, "blocked": 1, "failed": 2}.get(str(result.report.get("status")), 2)


if __name__ == "__main__":
    raise SystemExit(main())
