from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from smartcrypto.ml.walkforward_anti_leakage_audit import (
    DEFAULT_DATASET_PATH,
    DEFAULT_REPORT_PATH,
    audit_walkforward_anti_leakage,
    read_table,
    utc_timestamp,
)


DEFAULT_FEATURE_AUDIT = Path("data/reports/phase23_feature_audit.json")
DEFAULT_WALKFORWARD = Path("data/reports/phase23_walkforward_clean_report.json")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def read_dataset(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"dataset_missing:{path}")
    frame = read_table(path)
    if max_rows is not None and max_rows > 0 and len(frame) > max_rows:
        frame = frame.tail(max_rows).reset_index(drop=True)
    return frame


def run_phase23_audit(
    *,
    dataset: Path = DEFAULT_DATASET_PATH,
    walkforward_report: Path | None = None,
    target_column: str = "target_win",
    time_column: str = "open_ts",
    timestamp_column: str | None = None,
    decision_time_column: str | None = None,
    feature_prefix: str = "",
    decision_mode: str = "open",
    folds: int = 5,
    embargo_minutes: int = 60,
    seed: int = 42,
    max_rows: int | None = None,
    min_train_rows: int = 10,
    min_test_rows: int = 5,
    require_embargo: bool = False,
    strict: bool = False,
    output_report: Path = DEFAULT_REPORT_PATH,
    output_feature_audit: Path = DEFAULT_FEATURE_AUDIT,
    output_walkforward: Path = DEFAULT_WALKFORWARD,
) -> dict[str, Any]:
    del decision_mode, folds, seed
    effective_time_column = timestamp_column or time_column
    frame = read_dataset(dataset, max_rows=max_rows)
    report = audit_walkforward_anti_leakage(
        frame=frame,
        dataset_path=dataset,
        walkforward_report_path=walkforward_report,
        report_path=output_report,
        timestamp_column=effective_time_column,
        target_column=target_column,
        decision_time_column=decision_time_column,
        feature_prefix=feature_prefix,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
        require_embargo=require_embargo,
        embargo_minutes=embargo_minutes,
        strict=strict,
    )
    legacy_status = "OK" if report["status"] in {"ok", "warning"} else "BLOCKED"
    legacy_report = {
        "phase": "phase23_walkforward_anti_leakage_audit",
        **report,
        "phase23_status": report["status"],
        "status": legacy_status,
    }
    write_json(output_report, legacy_report)

    # Backward-compatible sidecar reports for the historical Phase 23 runner tests.
    write_json(
        output_feature_audit,
        {
            "status": "OK" if report["status"] in {"ok", "warning"} else "BLOCKED",
            "feature_columns": report.get("feature_columns", []),
            "prohibited_feature_columns": report.get("prohibited_feature_columns", []),
            "leakage_findings": report.get("leakage_findings", []),
        },
    )
    write_json(
        output_walkforward,
        {
            "status": "OK" if report.get("temporal_split_valid") else "BLOCKED",
            "time_column": effective_time_column,
            "train_rows": report.get("train_rows", 0),
            "test_rows": report.get("test_rows", 0),
            "embargo_minutes": embargo_minutes,
        },
    )
    return legacy_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Phase 23 walk-forward anti-leakage audit.",
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--walkforward-report", default=None)
    parser.add_argument("--report", "--output-report", dest="report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--timestamp-column", default=None)
    parser.add_argument("--time-column", default="open_ts")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--decision-time-column", default=None)
    parser.add_argument("--feature-prefix", default="")
    parser.add_argument("--min-train-rows", type=int, default=10)
    parser.add_argument("--min-test-rows", type=int, default=5)
    parser.add_argument("--require-embargo", action="store_true")
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--output-feature-audit", default=str(DEFAULT_FEATURE_AUDIT))
    parser.add_argument("--output-walkforward", default=str(DEFAULT_WALKFORWARD))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        timestamp_column = args.timestamp_column or args.time_column
        frame = read_dataset(Path(args.dataset), max_rows=args.max_rows)
        report = audit_walkforward_anti_leakage(
            frame=frame,
            dataset_path=Path(args.dataset),
            walkforward_report_path=Path(args.walkforward_report) if args.walkforward_report else None,
            report_path=Path(args.report),
            timestamp_column=timestamp_column,
            target_column=args.target_column,
            decision_time_column=args.decision_time_column,
            feature_prefix=args.feature_prefix,
            min_train_rows=args.min_train_rows,
            min_test_rows=args.min_test_rows,
            require_embargo=args.require_embargo,
            embargo_minutes=args.embargo_minutes,
            strict=args.strict,
        )
    except Exception as exc:
        failure = {
            "status": "blocked",
            "reason": str(exc),
            "phase": "phase23_walkforward_anti_leakage_audit",
            "traceback": traceback.format_exc(),
            "audited_at_utc": utc_timestamp(),
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        }
        write_json(Path(args.report), failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
