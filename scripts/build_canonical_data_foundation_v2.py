"""Build the research-only Canonical Data Foundation V2 evidence report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.canonical_data_foundation_v2.pipeline import (  # noqa: E402
    build_canonical_data_foundation_report,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument(
        "--trader-master",
        default="data/trades/trades_master.parquet",
    )
    value.add_argument(
        "--blocked-trades",
        default="data/research/ocr_v11_trade_research_dataset.parquet",
    )
    value.add_argument("--write-report", action="store_true")
    value.add_argument(
        "--output-json",
        default="data/reports/canonical_data_foundation_v2.json",
    )
    value.add_argument(
        "--output-markdown",
        default="data/reports/canonical_data_foundation_v2.md",
    )
    value.add_argument(
        "--manifest-output-root",
        default="data/reports/canonical_data_foundation_v2/manifests",
    )
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_canonical_data_foundation_report(
        project_root=args.project_root,
        trader_master_path=args.trader_master,
        blocked_trades_path=args.blocked_trades,
        write_report=args.write_report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        manifest_output_root=args.manifest_output_root,
        command="scripts/build_canonical_data_foundation_v2.py",
        arguments=sys.argv[1:] if argv is None else argv,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        print(f"GATE_B02={report['gate_b02']}")
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
