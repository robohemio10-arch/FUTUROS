from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smartcrypto.ml.feature_contract import DEFAULT_OUTPUT_PATH
from smartcrypto.ml.inference_guard import (
    DEFAULT_REPORT_PATH,
    report_to_exit_code,
    validate_ai_shadow_inference_input,
)


DEFAULT_INPUT_PATH = Path("data/features/incremental_training_microbatch.parquet")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate IA Shadow inference input.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_ai_shadow_inference_input(
        input_path=args.input,
        contract_path=args.contract,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report_to_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
