#!/usr/bin/env python3
"""Build B05 AI Shadow/Qlib/autotrain research evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.ai_shadow_qlib_autotrain_v2 import (  # noqa: E402
    build_ai_shadow_qlib_autotrain_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a paper/shadow/research-only B05 evidence report without "
            "runtime activation, orders or automatic model promotion."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input")
    parser.add_argument("--config")
    parser.add_argument("--baseline-calibration")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write_report and args.no_write:
        raise SystemExit("--write-report and --no-write are mutually exclusive")
    report = build_ai_shadow_qlib_autotrain_v2(
        project_root=args.project_root,
        input_path=args.input,
        config_path=args.config,
        baseline_calibration_path=args.baseline_calibration,
        write_report=bool(args.write_report),
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    else:
        print(
            "B05 status={status} reason={reason} decisions={decisions} "
            "training_eligible={eligible} write_performed={write}".format(
                status=report.get("status"),
                reason=report.get("reason"),
                decisions=report.get("counterfactual_harness", {}).get("decision_count", 0),
                eligible=report.get("training_governance", {}).get(
                    "research_training_eligible"
                ),
                write=report.get("write_performed"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
