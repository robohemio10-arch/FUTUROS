from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.model_registry import (  # noqa: E402
    DEFAULT_GATE_REPORT_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_TRAINER_REPORT_PATH,
    register_ai_shadow_challenger_model,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register AI Shadow challenger model with champion/challenger promotion gate.")
    parser.add_argument("--trainer-report", default=str(DEFAULT_TRAINER_REPORT_PATH))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--report", default=str(DEFAULT_GATE_REPORT_PATH))
    parser.add_argument("--min-rows", type=int, default=100)
    parser.add_argument("--min-accuracy", type=float, default=0.50)
    parser.add_argument("--min-f1", type=float, default=0.50)
    parser.add_argument("--min-roc-auc", type=float, default=None)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = register_ai_shadow_challenger_model(
        trainer_report_path=Path(args.trainer_report),
        registry_path=Path(args.registry),
        report_path=Path(args.report),
        min_rows=args.min_rows,
        min_accuracy=args.min_accuracy,
        min_f1=args.min_f1,
        min_roc_auc=args.min_roc_auc,
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
