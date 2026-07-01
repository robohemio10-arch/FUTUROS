#!/usr/bin/env python3
"""Build OCR Shadow paper replay/attribution source diagnostics report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_shadow_paper_replay_attribution_source_diagnostics import (  # noqa: E402
    build_ocr_shadow_paper_replay_attribution_source_diagnostics_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--allow-runtime-read", action="store_true", help="Allow explicit local report reads.")
    parser.add_argument("--oos-validation-report", default=None, help="Path to OOS validation JSON report.")
    parser.add_argument("--shadow-observation-design-report", default=None, help="Path to design JSON report.")
    parser.add_argument("--shadow-observation-replay-report", default=None, help="Path to replay JSON report.")
    parser.add_argument("--paper-closed-trades-attribution-report", default=None, help="Path to attribution JSON report.")
    parser.add_argument("--readiness-gate-report", default=None, help="Path to readiness gate JSON report.")
    parser.add_argument("--closeout-report", default=None, help="Path to closeout JSON report.")
    parser.add_argument("--evidence-pack-report", default=None, help="Path to evidence pack JSON report.")
    parser.add_argument("--output-report", default=None, help="Optional JSON diagnostics path under data/reports.")
    parser.add_argument("--markdown-report", default=None, help="Optional Markdown diagnostics path under data/reports.")
    parser.add_argument("--write", action="store_true", help="Write research-only JSON and Markdown to data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_paths = {
        "oos_validation": args.oos_validation_report,
        "observation_design": args.shadow_observation_design_report,
        "observation_replay": args.shadow_observation_replay_report,
        "paper_attribution": args.paper_closed_trades_attribution_report,
        "readiness_gate": args.readiness_gate_report,
        "closeout": args.closeout_report,
        "evidence_pack": args.evidence_pack_report,
    }
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        report_paths={key: value for key, value in report_paths.items() if value},
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
