from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

REQUIRED_FINANCE_GRADE_COLUMNS = (
    "trade_id",
    "symbol",
    "open_1m_ts",
    "target_win",
    "entry_price_repaired",
    "exit_price_repaired",
    "side_repaired",
    "volume_repaired",
    "leverage_resolved",
    "raw_return_resolved",
    "pnl_resolved",
    "price_return_pct",
    "leveraged_price_return_pct",
    "final_quality_status",
    "final_quality_flags",
)

BLOCKING_FLAGS = frozenset(
    {
        "entry_price_invalid",
        "exit_price_invalid",
        "side_unknown",
        "volume_invalid",
        "leverage_missing",
        "leverage_zero",
        "leverage_above_max",
        "price_return_extreme",
        "net_return_extreme",
    }
)


class FinanceGradeFilterError(ValueError):
    pass


@dataclass(frozen=True)
class FinanceGradeFilterReport:
    status: str
    rows_input: int
    rows_accepted: int
    rows_rejected: int
    acceptance_ratio: float
    output_path: str
    rejected_output_path: str
    accepted_status_counts: dict[str, int]
    rejected_status_counts: dict[str, int]
    rejected_flag_counts: dict[str, int]
    accepted_return_stats: dict[str, Any]
    accepted_leverage_stats: dict[str, Any]
    sample_accepted_rows: list[dict[str, Any]]
    sample_rejected_rows: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_finance_grade_sidecar_input(
    frame: pd.DataFrame,
    *,
    output_path: str | Path,
    rejected_output_path: str | Path,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    target_column: str = "target_win",
    time_column: str = "open_1m_ts",
    quality_status_column: str = "final_quality_status",
    allowed_status: str = OK,
    sample_rows: int = 50,
    warning_acceptance_ratio: float = 0.50,
) -> tuple[pd.DataFrame, pd.DataFrame, FinanceGradeFilterReport]:
    if not isinstance(frame, pd.DataFrame):
        raise FinanceGradeFilterError("finance_grade_input_must_be_dataframe")

    column_map = {
        "trade_id": id_column,
        "symbol": symbol_column,
        "open_1m_ts": time_column,
        "target_win": target_column,
        "final_quality_status": quality_status_column,
    }
    required_source_columns = set(REQUIRED_FINANCE_GRADE_COLUMNS)
    required_source_columns.remove("trade_id")
    required_source_columns.remove("symbol")
    required_source_columns.remove("open_1m_ts")
    required_source_columns.remove("target_win")
    required_source_columns.remove("final_quality_status")
    required_source_columns.update(column_map.values())
    for column in required_source_columns:
        if column not in frame.columns:
            raise FinanceGradeFilterError(f"required_column_missing:{column}")
    if frame[id_column].isna().any():
        raise FinanceGradeFilterError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise FinanceGradeFilterError("id_column_contains_duplicates")

    working = frame.copy(deep=True)
    working = working.rename(columns={source: target for target, source in column_map.items() if source != target})
    for column in REQUIRED_FINANCE_GRADE_COLUMNS:
        if column not in working.columns:
            raise FinanceGradeFilterError(f"required_output_column_missing:{column}")

    allowed_statuses = parse_allowed_statuses(allowed_status)
    status_allowed = working["final_quality_status"].astype(str).str.upper().isin(allowed_statuses)
    blocking_flag_mask = working["final_quality_flags"].apply(has_blocking_flag)
    accepted_mask = status_allowed & ~blocking_flag_mask

    accepted = working.loc[accepted_mask, list(REQUIRED_FINANCE_GRADE_COLUMNS)].copy().reset_index(drop=True)
    rejected = working.loc[~accepted_mask, list(REQUIRED_FINANCE_GRADE_COLUMNS)].copy().reset_index(drop=True)

    accepted_blocking_rows = accepted["final_quality_flags"].apply(has_blocking_flag) if not accepted.empty else pd.Series(dtype=bool)
    report_status = classify_report_status(
        rows_accepted=len(accepted),
        rows_input=len(working),
        accepted_contains_blocking=bool(accepted_blocking_rows.any()) if not accepted.empty else False,
        warning_acceptance_ratio=warning_acceptance_ratio,
    )
    acceptance_ratio = safe_ratio(len(accepted), len(working))
    rejected_flag_counts = count_flags(rejected["final_quality_flags"]) if not rejected.empty else {}
    report = FinanceGradeFilterReport(
        status=report_status,
        rows_input=int(len(working)),
        rows_accepted=int(len(accepted)),
        rows_rejected=int(len(rejected)),
        acceptance_ratio=acceptance_ratio,
        output_path=str(output_path),
        rejected_output_path=str(rejected_output_path),
        accepted_status_counts=count_values(accepted["final_quality_status"]) if not accepted.empty else {},
        rejected_status_counts=count_values(rejected["final_quality_status"]) if not rejected.empty else {},
        rejected_flag_counts=rejected_flag_counts,
        accepted_return_stats=numeric_stats(accepted["leveraged_price_return_pct"]) if not accepted.empty else empty_stats(),
        accepted_leverage_stats=numeric_stats(accepted["leverage_resolved"]) if not accepted.empty else empty_stats(),
        sample_accepted_rows=sample_rows_for_report(accepted, sample_rows),
        sample_rejected_rows=sample_rows_for_report(rejected, sample_rows),
        recommended_next_action=recommend_next_action(report_status, len(accepted), len(rejected)),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return accepted, rejected, report


def parse_allowed_statuses(value: str) -> set[str]:
    statuses = {item.strip().upper() for item in str(value).split(",") if item.strip()}
    if not statuses:
        raise FinanceGradeFilterError("allowed_status_empty")
    return statuses


def has_blocking_flag(value: Any) -> bool:
    flags = {item.strip() for item in str(value).split(";") if item.strip() and item.strip() != OK}
    return bool(flags & BLOCKING_FLAGS)


def classify_report_status(
    *,
    rows_accepted: int,
    rows_input: int,
    accepted_contains_blocking: bool,
    warning_acceptance_ratio: float,
) -> str:
    if rows_accepted <= 0 or accepted_contains_blocking:
        return BLOCKED
    if safe_ratio(rows_accepted, rows_input) < warning_acceptance_ratio:
        return WARNING
    return OK


def recommend_next_action(status: str, accepted: int, rejected: int) -> str:
    if status == BLOCKED:
        return "block_financial_evaluation_until_finance_grade_rows_are_available"
    if rejected:
        return "use_finance_grade_rows_for_research_only_and_review_rejected_rows"
    if accepted:
        return "build_normalized_sidecar_from_finance_grade_rows_for_offline_research_only"
    return "review_final_quality_resolution_before_financial_evaluation"


def count_values(series: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.astype(str).value_counts(dropna=False).sort_index().items()}


def count_flags(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.astype(str):
        for flag in value.split(";"):
            flag = flag.strip()
            if not flag or flag == OK:
                continue
            counts[flag] = counts.get(flag, 0) + 1
    return dict(sorted(counts.items()))


def numeric_stats(series: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    values = numeric.dropna()
    stats: dict[str, Any] = {"count": int(len(series)), "null_count": int(numeric.isna().sum())}
    if values.empty:
        stats.update({key: None for key in ("min", "max", "mean", "median", "std", "p95", "p99", "abs_max")})
        return stats
    stats.update(
        {
            "min": safe_float(values.min()),
            "max": safe_float(values.max()),
            "mean": safe_float(values.mean()),
            "median": safe_float(values.median()),
            "std": safe_float(values.std(ddof=0)),
            "p95": safe_float(values.quantile(0.95)),
            "p99": safe_float(values.quantile(0.99)),
            "abs_max": safe_float(values.abs().max()),
        }
    )
    return stats


def empty_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {"count": 0, "null_count": 0}
    stats.update({key: None for key in ("min", "max", "mean", "median", "std", "p95", "p99", "abs_max")})
    return stats


def sample_rows_for_report(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    columns = [
        column
        for column in (
            "trade_id",
            "symbol",
            "final_quality_status",
            "final_quality_flags",
            "leverage_resolved",
            "leveraged_price_return_pct",
        )
        if column in frame.columns
    ]
    samples: list[dict[str, Any]] = []
    for idx, row in frame.loc[:, columns].head(max(0, int(limit))).iterrows():
        samples.append({"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in columns}})
    return samples


def safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def normalize_json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, np.floating)):
        return safe_float(value)
    return value


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"dataset_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_outputs(
    *,
    accepted: pd.DataFrame,
    rejected: pd.DataFrame,
    report: FinanceGradeFilterReport,
    output_path: str | Path,
    rejected_output_path: str | Path,
    report_path: str | Path,
) -> None:
    accepted_path = Path(output_path)
    rejected_path = Path(rejected_output_path)
    accepted_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(accepted, accepted_path)
    write_parquet_atomic(rejected, rejected_path)
    write_json(report_path, report.to_dict())


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    frame.to_parquet(tmp_path, index=False)
    tmp_path.replace(path)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
