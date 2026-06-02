from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.outcome_tracker import (  # noqa: E402
    DEFAULT_DECISIONS_PATH,
    DEFAULT_FEEDBACK_PATH,
    DEFAULT_MICROBATCH_PATH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REPORT_PATH,
    track_ai_shadow_outcomes,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track AI Shadow model decision outcomes from closed paper feedback.")
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS_PATH))
    parser.add_argument("--feedback", default=str(DEFAULT_FEEDBACK_PATH))
    parser.add_argument("--microbatch", default=str(DEFAULT_MICROBATCH_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = track_ai_shadow_outcomes(
        decisions_path=Path(args.decisions),
        feedback_path=Path(args.feedback),
        microbatch_path=Path(args.microbatch),
        output_path=Path(args.output),
        report_path=Path(args.report),
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "no_matches"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
