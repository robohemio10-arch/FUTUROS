#!/usr/bin/env python3
"""Build the research-only Paper Edge Foundation V1 report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_edge_foundation import (  # noqa: E402
    build_paper_edge_foundation_v1,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--paper-db", required=True, help="Authoritative paper SQLite snapshot.")
    parser.add_argument("--score-source", default=None, help="Optional lineage-verifiable score source.")
    parser.add_argument("--regime-source", default=None, help="Optional point-in-time regime source.")
    parser.add_argument("--certified-cut", default="2026-07-17T00:00:00Z")
    parser.add_argument("--embargo-seconds", type=int, default=86_400)
    parser.add_argument("--minimum-regime-sample", type=int, default=20)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode; this is the default.")
    parser.add_argument("--output-report", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_edge_foundation_v1(
        project_root=args.project_root,
        paper_db=args.paper_db,
        score_source=args.score_source,
        regime_source=args.regime_source,
        write_report=bool(args.write_report and not args.no_write),
        output_report=args.output_report,
        certified_cut=args.certified_cut,
        embargo_seconds=args.embargo_seconds,
        minimum_regime_sample=args.minimum_regime_sample,
    )
    indent = None if args.json else 2
    print(json.dumps(report, indent=indent, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
