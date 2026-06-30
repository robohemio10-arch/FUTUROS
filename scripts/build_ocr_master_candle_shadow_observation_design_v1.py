#!/usr/bin/env python3
"""Build OCR Master + candle shadow observation design report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_master_candle_shadow_observation_design import (  # noqa: E402
    build_shadow_observation_design_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument(
        "--allow-runtime-read",
        action="store_true",
        help="Explicitly allow read-only consumption of the previous local OOS report.",
    )
    parser.add_argument(
        "--oos-report",
        default=None,
        help="Path to previous positive-rule OOS validation report. Defaults to data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json when runtime reads are allowed.",
    )
    parser.add_argument("--write", action="store_true", help="Write research-only JSON report to data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_shadow_observation_design_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        oos_report=args.oos_report,
        write=args.write,
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
