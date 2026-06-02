from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.model_decision_logger import (  # noqa: E402
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_TRAINER_REPORT_PATH,
    log_ai_shadow_model_decisions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append AI Shadow model decisions to paper/shadow-only JSONL audit log.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--trainer-report", default=str(DEFAULT_TRAINER_REPORT_PATH))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = log_ai_shadow_model_decisions(
        registry_path=Path(args.registry),
        trainer_report_path=Path(args.trainer_report),
        input_path=Path(args.input),
        output_path=Path(args.output),
        report_path=Path(args.report),
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
