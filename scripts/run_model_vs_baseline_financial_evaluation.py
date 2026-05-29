from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.model_vs_baseline_financial_evaluation import (
    ModelVsBaselineFinancialEvaluationError,
    read_parquet,
    run_model_vs_baseline_financial_evaluation,
    utc_now,
    write_json,
)


DEFAULT_FEATURES = Path("data/features/training_dataset_open_decision_clean.parquet")
DEFAULT_SIDECAR = Path("data/features/training_normalized_return_sidecar.parquet")
DEFAULT_SIDECAR_REPORT = Path("data/reports/normalized_return_sidecar_report.json")
DEFAULT_OUTPUT_REPORT = Path("data/reports/model_vs_baseline_financial_evaluation_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run model-vs-baseline finance-grade evaluation offline.")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES))
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR))
    parser.add_argument("--sidecar-report", default=str(DEFAULT_SIDECAR_REPORT))
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--return-column", default="net_return_pct")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-train-rows", type=int, default=200)
    parser.add_argument("--min-test-rows", type=int, default=100)
    parser.add_argument("--probability-thresholds", default="0.50,0.55,0.60,0.65,0.70")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        features = read_parquet(args.features)
        sidecar = read_parquet(args.sidecar)
        report = run_model_vs_baseline_financial_evaluation(
            features,
            sidecar,
            features_path=args.features,
            sidecar_path=args.sidecar,
            sidecar_report=args.sidecar_report,
            id_column=args.id_column,
            target_column=args.target_column,
            return_column=args.return_column,
            time_column=args.time_column,
            folds=args.folds,
            embargo_minutes=args.embargo_minutes,
            seed=args.seed,
            min_train_rows=args.min_train_rows,
            min_test_rows=args.min_test_rows,
            probability_thresholds=args.probability_thresholds,
        )
        write_json(args.output_report, report)
    except ModelVsBaselineFinancialEvaluationError as exc:
        blocked = {
            "status": "BLOCKED",
            "error": str(exc),
            "runtime_mode": "research",
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "recommended_next_action": "block_model_financial_evaluation_until_inputs_are_repaired",
            "created_at": utc_now(),
        }
        write_json(args.output_report, blocked)
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        return 0
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
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
