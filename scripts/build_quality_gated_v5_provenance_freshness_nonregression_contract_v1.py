#!/usr/bin/env python3
"""Build the research-only V5 quality-gated provenance/freshness projection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.quality_gated_v5_contract import (  # noqa: E402
    build_quality_gated_v5_contract_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--trade-enriched", default=None)
    parser.add_argument("--market-features", default=None)
    parser.add_argument("--official-quality-gated", default=None)
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--max-age-1m-seconds", type=int, default=120)
    parser.add_argument("--max-age-5m-seconds", type=int, default=600)
    parser.add_argument(
        "--timestamp-semantics",
        default="candle_open",
        choices=("candle_open", "unknown"),
    )
    parser.add_argument("--expected-v5-rows", type=int, default=504)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Force no-write mode. No-write is already the default.",
    )
    parser.add_argument("--report-json", default=None)
    parser.add_argument("--report-rows-jsonl", default=None)
    parser.add_argument("--report-markdown", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_quality_gated_v5_contract_report(
        project_root=args.project_root,
        trade_enriched_path=args.trade_enriched,
        market_features_path=args.market_features,
        official_quality_gated_path=args.official_quality_gated,
        model_path=args.model_path,
        max_age_1m_seconds=args.max_age_1m_seconds,
        max_age_5m_seconds=args.max_age_5m_seconds,
        timestamp_semantics=args.timestamp_semantics,
        expected_v5_rows=args.expected_v5_rows,
        write_report=bool(args.write_report and not args.no_write),
        report_json_path=args.report_json,
        report_rows_jsonl_path=args.report_rows_jsonl,
        report_markdown_path=args.report_markdown,
    )
    printable = {key: value for key, value in report.items() if key != "row_records"}
    if args.json:
        print(json.dumps(printable, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(printable, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0 if report["status"] in {"ok", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
