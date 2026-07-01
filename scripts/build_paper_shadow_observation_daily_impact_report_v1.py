#!/usr/bin/env python3
"""Build paper shadow observation daily impact report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_shadow_observation_daily_impact_report import (  # noqa: E402
    build_paper_shadow_observation_daily_impact_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Allow explicit local report reads.")
    parser.add_argument("--paper-attribution-report", default=None, help="Path to paper attribution JSON report.")
    parser.add_argument("--shadow-replay-report", default=None, help="Path to shadow replay JSON report.")
    parser.add_argument("--closed-trades-source-contract", default=None, help="Optional closed trades source contract JSON.")
    parser.add_argument("--output-report", default=None, help="Optional JSON report path under data/reports.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown report path under data/reports.")
    parser.add_argument("--write", action="store_true", help="Write research-only JSON and Markdown reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_shadow_observation_daily_impact_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        paper_attribution_report=args.paper_attribution_report,
        shadow_replay_report=args.shadow_replay_report,
        closed_trades_source_contract=args.closed_trades_source_contract,
        output_report=args.output_report,
        markdown_report=args.markdown_report,
        write=args.write,
        no_write=args.no_write or not args.write,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
