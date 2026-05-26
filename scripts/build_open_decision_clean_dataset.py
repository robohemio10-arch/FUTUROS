from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from smartcrypto.ml.anti_leakage_audit import BLOCKED, audit_feature_leakage


DEFAULT_INPUT = Path("data/features/training_dataset.parquet")
DEFAULT_OUTPUT = Path("data/features/training_dataset_open_decision_clean.parquet")
DEFAULT_REPORT = Path("data/reports/open_decision_clean_dataset_report.json")

REQUIRED_METADATA_COLUMNS = ("trade_id", "symbol", "open_1m_ts", "open_5m_ts")
OUTCOME_COLUMNS = {"return_pct", "mfe_pct", "mae_pct"}
POST_EVENT_EXACT_COLUMNS = {
    "pnl",
    "pnl_pct",
    "profit",
    "realized_pnl",
    "closed_pnl",
    "duration_seconds",
}


class OpenDecisionCleanDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class CleanDatasetManifest:
    input_path: str
    output_path: str
    rows: int
    columns: int
    target_column: str
    decision_mode: str
    metadata_columns: list[str]
    label_columns: list[str]
    outcome_columns: list[str]
    feature_columns: list[str]
    removed_columns: list[str]
    suspicious_columns: list[str]
    leakage_status_before: str
    leakage_status_after: str
    status: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_open_decision_clean_dataset(
    *,
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
    target_column: str = "target_win",
    decision_mode: str = "open",
    allow_path_candles: bool = False,
) -> CleanDatasetManifest:
    frame = read_dataset(input_path)
    if target_column not in frame.columns:
        raise OpenDecisionCleanDatasetError(f"target_column_missing:{target_column}")

    before_report = audit_feature_leakage(
        frame,
        target_column=target_column,
        decision_mode=decision_mode,
    )
    selected = classify_columns(
        frame,
        target_column=target_column,
        decision_mode=decision_mode,
        allow_path_candles=allow_path_candles,
    )
    output_columns = [
        *selected["metadata_columns"],
        *selected["label_columns"],
        *selected["feature_columns"],
    ]
    cleaned = frame.loc[:, output_columns].copy()

    after_report = audit_feature_leakage(
        cleaned,
        target_column=target_column,
        feature_columns=selected["feature_columns"],
        metadata_columns=selected["metadata_columns"],
        decision_mode=decision_mode,
    )
    if after_report.status == BLOCKED:
        raise OpenDecisionCleanDatasetError(
            "clean_dataset_failed_anti_leakage_audit:"
            + ",".join(after_report.forbidden_features)
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(output_path, index=False)

    manifest = CleanDatasetManifest(
        input_path=str(input_path),
        output_path=str(output_path),
        rows=int(len(cleaned)),
        columns=int(len(cleaned.columns)),
        target_column=target_column,
        decision_mode=decision_mode,
        metadata_columns=selected["metadata_columns"],
        label_columns=selected["label_columns"],
        outcome_columns=selected["outcome_columns"],
        feature_columns=selected["feature_columns"],
        removed_columns=selected["removed_columns"],
        suspicious_columns=selected["suspicious_columns"],
        leakage_status_before=before_report.status,
        leakage_status_after=after_report.status,
        status=after_report.status,
    )
    write_json(report_path, manifest.to_dict())
    return manifest


def classify_columns(
    frame: pd.DataFrame,
    *,
    target_column: str,
    decision_mode: str,
    allow_path_candles: bool,
) -> dict[str, list[str]]:
    metadata_columns = [column for column in REQUIRED_METADATA_COLUMNS if column in frame.columns]
    label_columns = [target_column]
    feature_columns: list[str] = []
    outcome_columns: list[str] = []
    removed_columns: list[str] = []
    suspicious_columns: list[str] = []

    for column in map(str, frame.columns):
        if column in metadata_columns or column in label_columns:
            continue
        reason = removal_reason(
            column,
            target_column=target_column,
            decision_mode=decision_mode,
            allow_path_candles=allow_path_candles,
        )
        if column in OUTCOME_COLUMNS:
            outcome_columns.append(column)
        if reason:
            removed_columns.append(column)
            if reason == "suspicious_not_known_at_open":
                suspicious_columns.append(column)
            continue
        if is_open_decision_feature(column):
            feature_columns.append(column)
        else:
            removed_columns.append(column)

    return {
        "metadata_columns": metadata_columns,
        "label_columns": label_columns,
        "outcome_columns": outcome_columns,
        "feature_columns": feature_columns,
        "removed_columns": sorted(set(removed_columns), key=removed_columns.index),
        "suspicious_columns": suspicious_columns,
    }


def removal_reason(
    column: str,
    *,
    target_column: str,
    decision_mode: str,
    allow_path_candles: bool,
) -> str | None:
    lower = column.lower()
    if lower == "path_candles" and not allow_path_candles:
        return "path_candles_disabled"
    if lower == "path_candles" and allow_path_candles:
        return None
    if column in OUTCOME_COLUMNS:
        return "outcome_not_feature"
    if lower in POST_EVENT_EXACT_COLUMNS:
        return "suspicious_not_known_at_open"
    if lower.startswith("future_ret_"):
        return "future_return_leakage"
    if lower.startswith("target_") and column != target_column:
        return "non_primary_target_leakage"
    if lower == "pnl" or lower.startswith("pnl_"):
        return "post_event_pnl"
    if decision_mode.lower() == "open" and (
        lower.startswith("close_1m_") or lower.startswith("close_5m_")
    ):
        return "close_feature_for_open_decision"
    return None


def is_open_decision_feature(column: str) -> bool:
    lower = column.lower()
    if lower == "path_candles":
        return True
    if lower.startswith("open_1m_") and not lower.endswith("_ts"):
        return True
    if lower.startswith("open_5m_") and not lower.endswith("_ts"):
        return True
    return False


def read_dataset(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"input_dataset_missing:{path}")
    return pd.read_parquet(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an anti-leakage open-decision research dataset.",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--decision-mode", default="open")
    parser.add_argument("--allow-path-candles", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = build_open_decision_clean_dataset(
            input_path=Path(args.input),
            output_path=Path(args.output),
            report_path=Path(args.report),
            target_column=args.target_column,
            decision_mode=args.decision_mode,
            allow_path_candles=bool(args.allow_path_candles),
        )
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
        write_json(Path(args.report), failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
