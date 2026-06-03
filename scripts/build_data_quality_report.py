from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.data.data_quality_report import (  # noqa: E402
    DEFAULT_REPORT_PATH,
    build_data_quality_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a paper/shadow-only data quality report for SmartCrypto datasets."
    )
    parser.add_argument("--trades-master")
    parser.add_argument("--trade-enriched")
    parser.add_argument("--training-dataset")
    parser.add_argument("--market-features")
    parser.add_argument("--microbatch")
    parser.add_argument("--decisions")
    parser.add_argument("--outcomes")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    datasets = {
        "trades_master": args.trades_master,
        "trade_enriched": args.trade_enriched,
        "training_dataset": args.training_dataset,
        "market_features": args.market_features,
        "microbatch": args.microbatch,
        "decisions": args.decisions,
        "outcomes": args.outcomes,
    }
    report = build_data_quality_report(
        datasets=datasets,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "missing_input"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
