from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.manual_go_no_go_governance import (
    DEFAULT_DECISION_PATH,
    DEFAULT_MAX_DECISION_AGE_HOURS,
    DEFAULT_OUTPUT_PATH,
    build_manual_go_no_go_live_canary_governance,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera relatório read-only de governança manual go/no-go.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--decision-path", default=str(DEFAULT_DECISION_PATH))
    parser.add_argument("--max-decision-age-hours", type=int, default=DEFAULT_MAX_DECISION_AGE_HOURS)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_manual_go_no_go_live_canary_governance(
        project_root=Path(args.project_root),
        output=Path(args.output),
        decision_path=Path(args.decision_path),
        max_decision_age_hours=args.max_decision_age_hours,
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(result.report, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "status": result.report["status"],
            "output": str(result.output_path),
            "write_performed": result.write_performed,
            "manual_decision": result.report["manual_decision"],
            "manual_decision_status": result.report["manual_decision_status"],
            "release_allowed": result.report["release_allowed"],
            "auto_promotion_allowed": result.report["auto_promotion_allowed"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
