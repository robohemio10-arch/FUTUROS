from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.ai_shadow_threshold_input_builder import (  # noqa: E402
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REPORT_PATH,
    build_ai_shadow_threshold_evaluation_input,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build IA Shadow financial threshold evaluation input.")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--microbatch", type=Path)
    parser.add_argument("--paper-feedback", type=Path)
    parser.add_argument("--sqlite-decisions", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--max-time-delta-minutes", type=float, default=60.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_ai_shadow_threshold_evaluation_input(
        decisions=args.decisions,
        outcomes=args.outcomes,
        microbatch=args.microbatch,
        paper_feedback=args.paper_feedback,
        sqlite_decisions=args.sqlite_decisions,
        output=args.output,
        report=args.report,
        max_time_delta_minutes=args.max_time_delta_minutes,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
