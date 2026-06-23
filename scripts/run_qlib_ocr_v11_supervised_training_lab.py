from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.qlib_ocr_v11_supervised_training import (  # noqa: E402
    SupervisedTrainingConfig,
    resolve_paths,
    run_supervised_training_lab,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OCR V1.1 supervised training lab for Qlib research."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--research-dataset-path", default=None)
    parser.add_argument("--trade-outcomes-path", default=None)
    parser.add_argument("--walkforward-report-path", default=None)
    parser.add_argument("--prediction-output-path", default=None)
    parser.add_argument("--model-output-path", default=None)
    parser.add_argument("--report-path", default=None)
    parser.add_argument("--executive-report-path", default=None)
    parser.add_argument("--summary-path", default=None)
    parser.add_argument("--min-rows", type=int, default=600)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--embargo-seconds", type=int, default=3600)
    parser.add_argument("--selector-quantile", type=float, default=0.70)
    parser.add_argument("--min-selected-rows", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-ram-gb", type=float, default=16.0)
    parser.add_argument("--model-family", choices=["lightgbm", "random_forest"], default="lightgbm")
    parser.add_argument("--json", action="store_true")

    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument("--write", dest="write", action="store_true")
    write_group.add_argument("--no-write", dest="write", action="store_false")
    parser.set_defaults(write=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    paths = resolve_paths(
        args.project_root,
        research_dataset_path=args.research_dataset_path,
        trade_outcomes_path=args.trade_outcomes_path,
        walkforward_report_path=args.walkforward_report_path,
        prediction_output_path=args.prediction_output_path,
        model_output_path=args.model_output_path,
        report_path=args.report_path,
        executive_report_path=args.executive_report_path,
        summary_path=args.summary_path,
    )
    config = SupervisedTrainingConfig(
        min_rows=args.min_rows,
        folds=args.folds,
        embargo_seconds=args.embargo_seconds,
        selector_quantile=args.selector_quantile,
        min_selected_rows=args.min_selected_rows,
        seed=args.seed,
        workers=args.workers,
        max_ram_gb=args.max_ram_gb,
        model_family=args.model_family,
    )
    result = run_supervised_training_lab(paths, config, write=args.write)
    payload = result.report

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))

    return 0 if payload.get("status") in {"ok", "warning", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
