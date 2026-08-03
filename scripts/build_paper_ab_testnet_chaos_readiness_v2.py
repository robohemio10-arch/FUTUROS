#!/usr/bin/env python3
"""Build the B06 paper A/B, testnet, chaos, capacity and soak-readiness report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_ab_testnet_chaos_readiness import (  # noqa: E402
    build_paper_ab_testnet_chaos_readiness_v2,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--evidence", default=None, help="B06 evidence JSON. Required for a readiness decision.")
    parser.add_argument("--config", default=None, help="Optional B06 configuration JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write advisory JSON/Markdown under data/reports.")
    parser.add_argument("--output-json", default=None, help="Optional JSON report path under data/reports.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown report path under data/reports.")
    parser.add_argument("--fail-on-blocked", action="store_true", help="Return exit code 2 when readiness is blocked.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=args.project_root,
        evidence_path=args.evidence,
        config_path=args.config,
        write_report=args.write_report,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 2 if args.fail_on_blocked and report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
