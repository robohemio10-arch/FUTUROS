from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartcrypto.ml.ai_shadow_financial_evaluation import (
    DEFAULT_INPUT_PATH,
    DEFAULT_REPORT_PATH,
    MINIMUM_RECOMMENDED_SAMPLES,
    evaluate_ai_shadow_financial_thresholds,
    parse_thresholds,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate IA Shadow financial thresholds.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--thresholds", default=None)
    parser.add_argument("--min-samples", type=int, default=MINIMUM_RECOMMENDED_SAMPLES)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = evaluate_ai_shadow_financial_thresholds(
        input_path=args.input,
        report_path=args.report,
        thresholds=parse_thresholds(args.thresholds),
        min_samples=args.min_samples,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "insufficient_data"} else 1


if __name__ == "__main__":
    sys.exit(main())
