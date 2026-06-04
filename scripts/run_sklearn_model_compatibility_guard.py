from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ml.sklearn_compatibility_guard import (  # noqa: E402
    DEFAULT_REPORT_PATH,
    run_sklearn_model_compatibility_guard,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the IA Shadow sklearn model compatibility guard.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--metadata", default=None)
    parser.add_argument("--registry", default=None)
    parser.add_argument("--trainer-report", default=None)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_sklearn_model_compatibility_guard(
        model_path=args.model,
        metadata_path=args.metadata,
        registry_path=args.registry,
        trainer_report_path=args.trainer_report,
        logs_path=args.logs,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
