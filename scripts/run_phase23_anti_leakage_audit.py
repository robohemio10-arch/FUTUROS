from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from smartcrypto.ml.anti_leakage_audit import BLOCKED, audit_feature_leakage
from smartcrypto.ml.baseline_evaluation import evaluate_baselines
from smartcrypto.ml.walkforward_split import create_walkforward_splits


DEFAULT_DATASET = Path("data/features/training_dataset.parquet")
DEFAULT_REPORT = Path("data/reports/phase23_anti_leakage_report.json")
DEFAULT_FEATURE_AUDIT = Path("data/reports/phase23_feature_audit.json")
DEFAULT_WALKFORWARD = Path("data/reports/phase23_walkforward_clean_report.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def read_dataset(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"dataset_missing:{path}")
    frame = pd.read_parquet(path)
    if max_rows is not None and max_rows > 0 and len(frame) > max_rows:
        frame = frame.tail(max_rows).reset_index(drop=True)
    return frame


def run_phase23_audit(
    *,
    dataset: Path = DEFAULT_DATASET,
    target_column: str = "target_win",
    time_column: str = "open_ts",
    decision_mode: str = "open",
    folds: int = 5,
    embargo_minutes: int = 60,
    seed: int = 42,
    max_rows: int | None = None,
    output_report: Path = DEFAULT_REPORT,
    output_feature_audit: Path = DEFAULT_FEATURE_AUDIT,
    output_walkforward: Path = DEFAULT_WALKFORWARD,
) -> dict[str, Any]:
    frame = read_dataset(dataset, max_rows=max_rows)
    feature_report = audit_feature_leakage(
        frame,
        target_column=target_column,
        decision_mode=decision_mode,
    )
    write_json(output_feature_audit, feature_report.to_dict())

    walkforward_payload: dict[str, Any]
    baseline_payload = evaluate_baselines(
        frame,
        target_column=target_column,
        seed=seed,
    ).to_dict()
    status = feature_report.status

    if feature_report.status == BLOCKED:
        walkforward_payload = {
            "status": BLOCKED,
            "reason": "feature_leakage_detected",
            "folds": [],
        }
    else:
        split_result = create_walkforward_splits(
            frame,
            time_column=time_column,
            folds=folds,
            embargo_seconds=int(embargo_minutes) * 60,
        )
        walkforward_payload = split_result.to_dict()
        status = "OK"

    write_json(output_walkforward, walkforward_payload)
    report = {
        "phase": "phase23_anti_leakage_audit",
        "runtime_mode": "research",
        "live_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "dataset": str(dataset),
        "target_column": target_column,
        "time_column": time_column,
        "decision_mode": decision_mode,
        "status": status,
        "feature_audit": feature_report.to_dict(),
        "walkforward": walkforward_payload,
        "baseline_evaluation": baseline_payload,
        "created_at": utc_now(),
    }
    write_json(output_report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Phase 23 offline anti-leakage audit for research datasets.",
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--time-column", default="open_ts")
    parser.add_argument("--decision-mode", default="open")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--output-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--output-feature-audit", default=str(DEFAULT_FEATURE_AUDIT))
    parser.add_argument("--output-walkforward", default=str(DEFAULT_WALKFORWARD))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_phase23_audit(
            dataset=Path(args.dataset),
            target_column=args.target_column,
            time_column=args.time_column,
            decision_mode=args.decision_mode,
            folds=args.folds,
            embargo_minutes=args.embargo_minutes,
            seed=args.seed,
            max_rows=args.max_rows,
            output_report=Path(args.output_report),
            output_feature_audit=Path(args.output_feature_audit),
            output_walkforward=Path(args.output_walkforward),
        )
    except Exception as exc:
        failure = {
            "phase": "phase23_anti_leakage_audit",
            "runtime_mode": "research",
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "created_at": utc_now(),
        }
        write_json(Path(args.output_report), failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
