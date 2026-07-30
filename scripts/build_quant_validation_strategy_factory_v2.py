#!/usr/bin/env python3
"""Build the B04 quantitative validation and Strategy Factory report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.research.quant_validation_strategy_factory_v2 import (  # noqa: E402
    build_quant_validation_strategy_factory_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run B04 institutional quantitative validation in research-only mode."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input", dest="input_path")
    parser.add_argument("--config")
    parser.add_argument("--candidate-family")
    parser.add_argument("--candidate-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output-json", default="data/reports/quant_validation_strategy_factory_v2.json")
    parser.add_argument("--output-markdown", default="data/reports/quant_validation_strategy_factory_v2.md")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_write and args.write_report:
        payload = {
            "status": "blocked",
            "reason": "conflicting_write_flags",
            "write_performed": False,
            "promotion_allowed": False,
            "operational_authority": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 2
    try:
        report = build_quant_validation_strategy_factory_report(
            project_root=args.project_root,
            input_path=args.input_path,
            config_path=args.config,
            candidate_family=args.candidate_family,
            candidate_id=args.candidate_id,
            seed=args.seed,
            write_report=bool(args.write_report and not args.no_write),
            output_json=args.output_json,
            output_markdown=args.output_markdown,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        report = {
            "status": "blocked",
            "reason": "quant_validation_cli_error",
            "error_type": type(exc).__name__,
            "write_performed": False,
            "promotion_allowed": False,
            "operational_authority": False,
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
