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
SCALE_FACTORS = (0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)


class PriceScaleOcrRepairError(ValueError):
    pass


@dataclass(frozen=True)
class PriceScaleOcrRepairReport:
    status: str
    rows: int
    output_path: str
    repair_status_counts: dict[str, int]
    repair_flag_counts: dict[str, int]
    scale_factor_counts_entry: dict[str, int]
    scale_factor_counts_exit: dict[str, int]
    distance_stats_before_after: dict[str, dict[str, Any]]
    corrected_price_return_stats: dict[str, Any]
    blocked_summary: dict[str, int]
    warning_summary: dict[str, int]
    sample_blocked_rows: list[dict[str, Any]]
    sample_warning_rows: list[dict[str, Any]]
    sample_corrected_rows: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriceRepairDecision:
    repaired_price: float | None
    reference_price: float | None
    scale_factor: float | None
    distance_before_pct: float | None
    distance_after_pct: float | None
    status: str
    flags: tuple[str, ...]


def repair_price_scale_ocr_anomalies(
    frame: pd.DataFrame,
    *,
    output_path: str | Path,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    time_column: str = "open_1m_ts",
    entry_price_column: str = "entry_price",
    exit_price_column: str = "exit_price",
    open_reference_column: str = "open_1m_close",
    close_reference_column: str = "close_1m_close",
    alt_open_reference_column: str = "open_5m_close",
    alt_close_reference_column: str = "close_5m_close",
    max_reference_distance_pct: float = 5.0,
    max_corrected_price_return_pct: float = 20.0,
    sample_rows: int = 50,
) -> tuple[pd.DataFrame, PriceScaleOcrRepairReport]:
    if not isinstance(frame, pd.DataFrame):
        raise PriceScaleOcrRepairError("price_scale_input_must_be_dataframe")
    for required in (id_column, entry_price_column, exit_price_column):
        if required not in frame.columns:
            raise PriceScaleOcrRepairError(f"required_column_missing:{required}")
    if frame[id_column].isna().any():
        raise PriceScaleOcrRepairError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise PriceScaleOcrRepairError("id_column_contains_duplicates")

    repaired = frame.copy(deep=True).reset_index(drop=True)
    source = frame.reset_index(drop=True)
    entry_original = numeric_series(source[entry_price_column])
    exit_original = numeric_series(source[exit_price_column])

    entry_decisions: list[PriceRepairDecision] = []
    exit_decisions: list[PriceRepairDecision] = []
    for idx, row in source.iterrows():
        entry_decisions.append(
            repair_one_price(
                original_price=entry_original.iloc[idx],
                references=reference_values(row, open_reference_column, alt_open_reference_column),
                max_reference_distance_pct=max_reference_distance_pct,
            )
        )
        exit_decisions.append(
            repair_one_price(
                original_price=exit_original.iloc[idx],
                references=reference_values(row, close_reference_column, alt_close_reference_column),
                max_reference_distance_pct=max_reference_distance_pct,
            )
        )

    repaired["trade_id"] = source[id_column]
    repaired["symbol"] = source[symbol_column] if symbol_column in source.columns else None
    repaired["open_1m_ts"] = source[time_column] if time_column in source.columns else pd.NaT
    repaired["entry_price_original"] = source[entry_price_column]
    repaired["exit_price_original"] = source[exit_price_column]
    repaired["entry_price_repaired"] = [decision.repaired_price for decision in entry_decisions]
    repaired["exit_price_repaired"] = [decision.repaired_price for decision in exit_decisions]
    repaired["entry_price_reference"] = [decision.reference_price for decision in entry_decisions]
    repaired["exit_price_reference"] = [decision.reference_price for decision in exit_decisions]
    repaired["entry_price_scale_factor"] = [decision.scale_factor for decision in entry_decisions]
    repaired["exit_price_scale_factor"] = [decision.scale_factor for decision in exit_decisions]
    repaired["entry_price_distance_pct_before"] = [decision.distance_before_pct for decision in entry_decisions]
    repaired["entry_price_distance_pct_after"] = [decision.distance_after_pct for decision in entry_decisions]
    repaired["exit_price_distance_pct_before"] = [decision.distance_before_pct for decision in exit_decisions]
    repaired["exit_price_distance_pct_after"] = [decision.distance_after_pct for decision in exit_decisions]

    price_return = calculate_abs_price_return(repaired["entry_price_repaired"], repaired["exit_price_repaired"])
    row_flags = merge_flags(entry_decisions, exit_decisions)
    add_row_flag(row_flags, price_return.abs().gt(max_corrected_price_return_pct), "corrected_price_return_extreme")
    repaired["corrected_price_return_pct"] = price_return
    repaired["price_scale_repair_flags"] = [";".join(flags) if flags else "OK" for flags in row_flags]
    repaired["price_scale_repair_status"] = classify_rows(row_flags)

    status_counts = count_values(repaired["price_scale_repair_status"])
    flag_counts = count_flags(repaired["price_scale_repair_flags"])
    blocked_mask = repaired["price_scale_repair_status"].eq(BLOCKED)
    warning_mask = repaired["price_scale_repair_status"].eq(WARNING)
    corrected_mask = (
        repaired["entry_price_scale_factor"].fillna(1.0).ne(1.0)
        | repaired["exit_price_scale_factor"].fillna(1.0).ne(1.0)
    )
    report_status = classify_report_status(len(repaired), status_counts)
    report = PriceScaleOcrRepairReport(
        status=report_status,
        rows=int(len(repaired)),
        output_path=str(output_path),
        repair_status_counts=status_counts,
        repair_flag_counts=flag_counts,
        scale_factor_counts_entry=count_factor_values(repaired["entry_price_scale_factor"]),
        scale_factor_counts_exit=count_factor_values(repaired["exit_price_scale_factor"]),
        distance_stats_before_after={
            "entry_before": numeric_stats(repaired["entry_price_distance_pct_before"]),
            "entry_after": numeric_stats(repaired["entry_price_distance_pct_after"]),
            "exit_before": numeric_stats(repaired["exit_price_distance_pct_before"]),
            "exit_after": numeric_stats(repaired["exit_price_distance_pct_after"]),
        },
        corrected_price_return_stats=numeric_stats(repaired["corrected_price_return_pct"]),
        blocked_summary=summarize_flags(repaired.loc[blocked_mask, "price_scale_repair_flags"]),
        warning_summary=summarize_flags(repaired.loc[warning_mask, "price_scale_repair_flags"]),
        sample_blocked_rows=sample_rows_for_report(repaired, blocked_mask, sample_rows),
        sample_warning_rows=sample_rows_for_report(repaired, warning_mask, sample_rows),
        sample_corrected_rows=sample_rows_for_report(repaired, corrected_mask, sample_rows),
        recommended_next_action=recommend_next_action(report_status),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return repaired, report


def repair_one_price(
    *,
    original_price: Any,
    references: list[float],
    max_reference_distance_pct: float,
) -> PriceRepairDecision:
    original = safe_float(original_price)
    if original is None or original <= 0:
        return PriceRepairDecision(None, None, None, None, None, BLOCKED, ("price_invalid",))
    if not references:
        return PriceRepairDecision(original, None, 1.0, None, None, BLOCKED, ("reference_missing",))
    if references_are_ambiguous(references, max_reference_distance_pct):
        reference = float(np.median(references))
        before = distance_pct(original, reference)
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("reference_ambiguous",))

    reference = float(np.median(references))
    before = distance_pct(original, reference)
    if before is None:
        return PriceRepairDecision(original, reference, 1.0, None, None, BLOCKED, ("reference_invalid",))
    if before <= max_reference_distance_pct:
        return PriceRepairDecision(original, reference, 1.0, before, before, OK, ())

    candidates = sorted(
        (
            (factor, original * factor, distance_pct(original * factor, reference))
            for factor in SCALE_FACTORS
        ),
        key=lambda item: float("inf") if item[2] is None else item[2],
    )
    best_factor, best_price, best_distance = candidates[0]
    second_distance = candidates[1][2] if len(candidates) > 1 else None
    if best_distance is None:
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("correction_not_confident",))
    if second_distance is not None and abs(second_distance - best_distance) <= 0.01:
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("correction_ambiguous",))
    if best_factor == 1.0:
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("correction_not_confident",))
    if best_distance > max_reference_distance_pct:
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("correction_outside_reference_band",))
    if not clearly_improves(before, best_distance, max_reference_distance_pct):
        return PriceRepairDecision(original, reference, 1.0, before, before, BLOCKED, ("correction_not_confident",))
    return PriceRepairDecision(
        safe_float(best_price),
        reference,
        safe_float(best_factor),
        before,
        best_distance,
        OK,
        ("price_scale_corrected",),
    )


def reference_values(row: pd.Series, primary_column: str, alternate_column: str) -> list[float]:
    values: list[float] = []
    for column in (primary_column, alternate_column):
        if column not in row.index:
            continue
        value = safe_float(row[column])
        if value is not None and value > 0:
            values.append(value)
    return values


def references_are_ambiguous(references: list[float], max_reference_distance_pct: float) -> bool:
    if len(references) < 2:
        return False
    low = min(references)
    high = max(references)
    midpoint = float(np.median(references))
    spread = distance_pct(high, midpoint)
    return bool(spread is not None and spread > max_reference_distance_pct and distance_pct(low, midpoint) > max_reference_distance_pct)


def clearly_improves(before: float, after: float, max_reference_distance_pct: float) -> bool:
    return after <= max_reference_distance_pct and before > max_reference_distance_pct and after <= before * 0.25


def distance_pct(value: Any, reference: Any) -> float | None:
    numeric = safe_float(value)
    ref = safe_float(reference)
    if numeric is None or ref is None or ref <= 0:
        return None
    return abs(numeric - ref) / ref * 100.0


def calculate_abs_price_return(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    entry_numeric = numeric_series(entry)
    exit_numeric = numeric_series(exit_)
    result = ((exit_numeric - entry_numeric) / entry_numeric) * 100.0
    return result.abs().replace([np.inf, -np.inf], np.nan)


def merge_flags(entry_decisions: list[PriceRepairDecision], exit_decisions: list[PriceRepairDecision]) -> list[list[str]]:
    result: list[list[str]] = []
    for entry, exit_ in zip(entry_decisions, exit_decisions):
        flags: list[str] = []
        for flag in entry.flags:
            append_flag(flags, "entry_" + flag)
        for flag in exit_.flags:
            append_flag(flags, "exit_" + flag)
        result.append(flags)
    return result


def add_row_flag(flags: list[list[str]], mask: pd.Series | np.ndarray, flag: str) -> None:
    mask_array = pd.Series(mask).fillna(False).to_numpy(dtype=bool)
    for idx, enabled in enumerate(mask_array):
        if enabled:
            append_flag(flags[idx], flag)


def classify_rows(flags: list[list[str]]) -> list[str]:
    blocked_markers = (
        "price_invalid",
        "reference_missing",
        "reference_ambiguous",
        "reference_invalid",
        "correction_not_confident",
        "correction_ambiguous",
        "correction_outside_reference_band",
        "corrected_price_return_extreme",
    )
    warning_markers = ("price_scale_corrected",)
    statuses: list[str] = []
    for row_flags in flags:
        if any(marker in flag for flag in row_flags for marker in blocked_markers):
            statuses.append(BLOCKED)
        elif any(marker in flag for flag in row_flags for marker in warning_markers):
            statuses.append(OK)
        else:
            statuses.append(OK)
    return statuses


def classify_report_status(row_count: int, status_counts: dict[str, int]) -> str:
    if row_count <= 0:
        return BLOCKED
    blocked = status_counts.get(BLOCKED, 0)
    if blocked / row_count >= 0.50:
        return BLOCKED
    if blocked or status_counts.get(WARNING, 0):
        return WARNING
    return OK


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def numeric_stats(series: pd.Series) -> dict[str, Any]:
    numeric = numeric_series(series)
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


def sample_rows_for_report(frame: pd.DataFrame, mask: pd.Series, limit: int) -> list[dict[str, Any]]:
    columns = [
        "trade_id",
        "symbol",
        "entry_price_original",
        "exit_price_original",
        "entry_price_repaired",
        "exit_price_repaired",
        "entry_price_scale_factor",
        "exit_price_scale_factor",
        "corrected_price_return_pct",
        "price_scale_repair_status",
        "price_scale_repair_flags",
    ]
    samples: list[dict[str, Any]] = []
    for idx, row in frame.loc[mask, columns].head(max(0, int(limit))).iterrows():
        samples.append({"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in columns}})
    return samples


def count_values(series: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.value_counts(dropna=False).to_dict().items()}


def count_factor_values(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series:
        numeric = safe_float(value)
        key = "null" if numeric is None else format(numeric, ".6g")
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_flags(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.astype(str):
        for flag in value.split(";"):
            if not flag or flag == "OK":
                continue
            counts[flag] = counts.get(flag, 0) + 1
    return counts


def summarize_flags(series: pd.Series) -> dict[str, int]:
    return count_flags(series)


def append_flag(row_flags: list[str], flag: str) -> None:
    if flag not in row_flags:
        row_flags.append(flag)


def recommend_next_action(status: str) -> str:
    if status == BLOCKED:
        return "block_financial_repair_until_price_scale_ocr_anomalies_are_reviewed"
    if status == WARNING:
        return "use_price_scale_repair_for_diagnostic_research_only_and_review_blocked_rows"
    return "price_scale_inputs_repaired_for_offline_research_only"


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
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"dataset_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_outputs(
    *,
    repaired: pd.DataFrame,
    report: PriceScaleOcrRepairReport,
    output_path: str | Path,
    report_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    repaired.to_parquet(tmp_path, index=False)
    tmp_path.replace(output)
    write_json(report_path, report.to_dict())


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
