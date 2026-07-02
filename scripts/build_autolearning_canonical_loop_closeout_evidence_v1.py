"""CLI for the canonical paper/shadow auto-learning closeout evidence pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.learning.autolearning_closeout import build_closeout_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only auto-learning canonical loop closeout evidence.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-json", default=None)
    parser.add_argument("--report-markdown", default=None)
    parser.add_argument("--lineage-matrix", default=None)
    parser.add_argument("--safety-matrix", default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_closeout_report(
        project_root=args.project_root,
        write=args.write,
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
        lineage_matrix_path=args.lineage_matrix,
        safety_matrix_path=args.safety_matrix,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        print(f"CANONICAL_LOOP_DECISION={report['canonical_loop_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
