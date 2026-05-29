from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.sidecar_financial_evaluation import (
    read_parquet,
    run_sidecar_financial_evaluation,
    utc_now,
    write_json,
)


DEFAULT_FEATURES = Path("data/features/training_dataset_open_decision_clean.parquet")
DEFAULT_SIDECAR = Path("data/features/training_outcome_sidecar.parquet")
DEFAULT_REPORT = Path("data/reports/sidecar_financial_evaluation_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run offline financial baseline evaluation using an outcome sidecar.",
    )
    parser.add_argument("--features", default=str(DEFAULT_FEATURES))
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR))
    parser.add_argument("--output-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--return-column", default="return_pct")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        features = read_parquet(args.features)
        sidecar = read_parquet(args.sidecar)
        report = run_sidecar_financial_evaluation(
            features,
            sidecar,
            features_path=args.features,
            sidecar_path=args.sidecar,
            id_column=args.id_column,
            target_column=args.target_column,
            return_column=args.return_column,
            folds=args.folds,
            embargo_minutes=args.embargo_minutes,
            seed=args.seed,
        )
        write_json(args.output_report, report.to_dict())
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "runtime_mode": "research",
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "created_at": utc_now(),
        }
        write_json(args.output_report, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
