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


class FinalFinancialQualityResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class FinalFinancialQualityResolutionReport:
    status: str
    rows: int
    output_path: str
    final_quality_status_counts: dict[str, int]
    final_quality_flag_counts: dict[str, int]
    leverage_resolution_summary: dict[str, int]
    extreme_return_summary: dict[str, int]
    sample_blocked_rows: list[dict[str, Any]]
    sample_warning_rows: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_final_financial_quality_blocks(
    frame: pd.DataFrame,
    *,
    output_path: str | Path,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    target_column: str = "target_win",
    time_column: str = "open_1m_ts",
    entry_price_column: str = "entry_price_repaired",
    exit_price_column: str = "exit_price_repaired",
    side_column: str = "side_repaired",
    volume_column: str = "volume_repaired",
    leverage_column: str = "leverage_consistent",
    leverage_original_column: str = "leverage_original",
    raw_return_column: str = "raw_return_consistent",
    pnl_column: str = "pnl_consistent",
    max_leverage: float = 125.0,
    max_abs_price_return_pct: float = 20.0,
    max_abs_net_return_pct: float = 100.0,
    raw_return_warning_threshold: float = 5.0,
    sample_rows: int = 50,
) -> tuple[pd.DataFrame, FinalFinancialQualityResolutionReport]:
    if not isinstance(frame, pd.DataFrame):
        raise FinalFinancialQualityResolutionError("final_quality_input_must_be_dataframe")
    required = (
        id_column,
        entry_price_column,
        exit_price_column,
        side_column,
        volume_column,
        leverage_column,
    )
    for column in required:
        if column not in frame.columns:
            raise FinalFinancialQualityResolutionError(f"required_column_missing:{column}")
    if frame[id_column].isna().any():
        raise FinalFinancialQualityResolutionError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise FinalFinancialQualityResolutionError("id_column_contains_duplicates")

    result = pd.DataFrame(index=frame.index)
    result["trade_id"] = frame[id_column]
    result["symbol"] = frame[symbol_column] if symbol_column in frame.columns else None
    result["open_1m_ts"] = frame[time_column] if time_column in frame.columns else pd.NaT
    result["target_win"] = frame[target_column] if target_column in frame.columns else np.nan

    entry = numeric_series(frame[entry_price_column])
    exit_ = numeric_series(frame[exit_price_column])
    side = normalize_side(frame[side_column])
    volume = numeric_series(frame[volume_column])
    leverage_consistent = numeric_series(frame[leverage_column])
    leverage_original = choose_numeric(frame, leverage_original_column, leverage_column)
    raw_return_consistent = choose_numeric(frame, raw_return_column, "")
    pnl_consistent = choose_numeric(frame, pnl_column, "")

    flags: list[list[str]] = [[] for _ in range(len(frame))]
    add_flag(flags, entry.isna() | entry.le(0), "entry_price_invalid")
    add_flag(flags, exit_.isna() | exit_.le(0), "exit_price_invalid")
    add_flag(flags, side.eq("UNKNOWN"), "side_unknown")
    add_flag(flags, volume.isna() | volume.le(0), "volume_invalid")

    leverage_resolved = pd.Series(np.nan, index=frame.index, dtype=float)
    valid_consistent = leverage_consistent.notna() & leverage_consistent.gt(0) & leverage_consistent.le(max_leverage)
    leverage_resolved.loc[valid_consistent] = leverage_consistent.loc[valid_consistent]

    negative_resolvable = (
        leverage_resolved.isna()
        & leverage_original.notna()
        & leverage_original.lt(0)
        & leverage_original.abs().gt(0)
        & leverage_original.abs().le(max_leverage)
    )
    leverage_resolved.loc[negative_resolvable] = leverage_original.loc[negative_resolvable].abs()
    add_flag(flags, negative_resolvable, "leverage_negative_abs_resolved")

    unresolved = leverage_resolved.isna()
    add_flag(flags, unresolved & leverage_original.isna(), "leverage_missing")
    add_flag(flags, unresolved & leverage_original.eq(0), "leverage_zero")
    add_flag(flags, unresolved & leverage_original.abs().gt(max_leverage), "leverage_above_max")
    add_flag(flags, unresolved & leverage_original.lt(0) & leverage_original.abs().gt(max_leverage), "leverage_above_max")

    price_return_pct = calculate_price_return(entry, exit_, side)
    leveraged_price_return_pct = price_return_pct * leverage_resolved
    expected_pnl = calculate_expected_pnl(entry, exit_, side, volume)

    add_flag(flags, price_return_pct.abs().gt(max_abs_price_return_pct), "price_return_extreme")
    add_flag(flags, leveraged_price_return_pct.abs().gt(max_abs_net_return_pct), "net_return_extreme")

    critical_ok = (
        entry.gt(0)
        & exit_.gt(0)
        & side.isin(["LONG", "SHORT"])
        & volume.gt(0)
        & leverage_resolved.notna()
        & price_return_pct.abs().le(max_abs_price_return_pct)
        & leveraged_price_return_pct.abs().le(max_abs_net_return_pct)
    ).fillna(False)

    raw_return_resolved = pd.Series(np.nan, index=frame.index, dtype=float)
    raw_return_resolved.loc[critical_ok] = leveraged_price_return_pct.loc[critical_ok]
    raw_diff = (raw_return_consistent - leveraged_price_return_pct).abs()
    raw_needs_recalc = critical_ok & (raw_return_consistent.isna() | raw_diff.gt(raw_return_warning_threshold))
    add_flag(flags, raw_needs_recalc, "raw_return_recalculated_from_price")

    pnl_resolved = pd.Series(np.nan, index=frame.index, dtype=float)
    pnl_resolved.loc[critical_ok] = expected_pnl.loc[critical_ok]
    pnl_needs_recalc = critical_ok & (pnl_consistent.isna() | (pnl_consistent - expected_pnl).abs().gt(1.0))
    add_flag(flags, pnl_needs_recalc, "pnl_recalculated_from_price")

    statuses = classify_rows(flags)

    result["entry_price_repaired"] = entry
    result["exit_price_repaired"] = exit_
    result["side_repaired"] = side
    result["volume_repaired"] = volume
    result["leverage_consistent"] = leverage_consistent
    result["leverage_resolved"] = leverage_resolved
    result["raw_return_consistent"] = raw_return_consistent
    result["raw_return_resolved"] = raw_return_resolved
    result["pnl_consistent"] = pnl_consistent
    result["pnl_resolved"] = pnl_resolved
    result["price_return_pct"] = price_return_pct
    result["leveraged_price_return_pct"] = leveraged_price_return_pct
    result["final_quality_status"] = statuses
    result["final_quality_flags"] = [";".join(items) if items else "OK" for items in flags]
    result = result.reset_index(drop=True)

    blocked_mask = result["final_quality_status"].eq(BLOCKED)
    warning_mask = result["final_quality_status"].eq(WARNING)
    status_counts = count_values(result["final_quality_status"])
    flag_counts = count_flags(result["final_quality_flags"])
    report_status = classify_report_status(len(result), status_counts)
    report = FinalFinancialQualityResolutionReport(
        status=report_status,
        rows=int(len(result)),
        output_path=str(output_path),
        final_quality_status_counts=status_counts,
        final_quality_flag_counts=flag_counts,
        leverage_resolution_summary={
            "already_valid": int(valid_consistent.sum()),
            "negative_abs_resolved": int(negative_resolvable.sum()),
            "missing_unresolved": int((unresolved & leverage_original.isna()).sum()),
            "zero_unresolved": int((unresolved & leverage_original.eq(0)).sum()),
            "above_max_unresolved": int((unresolved & leverage_original.abs().gt(max_leverage)).sum()),
            "resolved_total": int(leverage_resolved.notna().sum()),
            "blocked_total": int(leverage_resolved.isna().sum()),
        },
        extreme_return_summary={
            "price_return_extreme": int(price_return_pct.abs().gt(max_abs_price_return_pct).fillna(False).sum()),
            "net_return_extreme": int(leveraged_price_return_pct.abs().gt(max_abs_net_return_pct).fillna(False).sum()),
            "raw_return_recalculated_from_price": int(raw_needs_recalc.sum()),
        },
        sample_blocked_rows=sample_rows_for_report(result, blocked_mask, sample_rows),
        sample_warning_rows=sample_rows_for_report(result, warning_mask, sample_rows),
        recommended_next_action=recommend_next_action(report_status),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return result, report


def choose_numeric(frame: pd.DataFrame, preferred: str, fallback: str) -> pd.Series:
    if preferred and preferred in frame.columns:
        return numeric_series(frame[preferred])
    if fallback and fallback in frame.columns:
        return numeric_series(frame[fallback])
    return pd.Series(np.nan, index=frame.index, dtype=float)


def normalize_side(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.lower().str.replace("_", " ", regex=False).str.replace("-", " ", regex=False)
    result = pd.Series("UNKNOWN", index=series.index, dtype=object)
    result.loc[text.str.contains("long|buy|compr|alta", na=False)] = "LONG"
    result.loc[text.str.contains("short|sell|vend|baixa", na=False)] = "SHORT"
    return result


def calculate_price_return(entry: pd.Series, exit_: pd.Series, side: pd.Series) -> pd.Series:
    gross = ((exit_ - entry) / entry) * 100.0
    result = pd.Series(np.nan, index=entry.index, dtype=float)
    valid = entry.gt(0) & exit_.gt(0)
    result.loc[valid & side.eq("LONG")] = gross
    result.loc[valid & side.eq("SHORT")] = -gross
    return result.replace([np.inf, -np.inf], np.nan)


def calculate_expected_pnl(entry: pd.Series, exit_: pd.Series, side: pd.Series, volume: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=entry.index, dtype=float)
    valid = entry.gt(0) & exit_.gt(0) & volume.gt(0)
    result.loc[valid & side.eq("LONG")] = (exit_ - entry) * volume
    result.loc[valid & side.eq("SHORT")] = (entry - exit_) * volume
    return result.replace([np.inf, -np.inf], np.nan)


def classify_rows(flags: list[list[str]]) -> list[str]:
    blocked_flags = {
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
    warning_flags = {
        "leverage_negative_abs_resolved",
        "raw_return_recalculated_from_price",
        "pnl_recalculated_from_price",
    }
    statuses: list[str] = []
    for row_flags in flags:
        if any(flag in blocked_flags for flag in row_flags):
            statuses.append(BLOCKED)
        elif any(flag in warning_flags for flag in row_flags):
            statuses.append(WARNING)
        else:
            statuses.append(OK)
    return statuses


def classify_report_status(row_count: int, status_counts: dict[str, int]) -> str:
    if row_count <= 0:
        return BLOCKED
    if status_counts.get(BLOCKED, 0):
        return BLOCKED
    if status_counts.get(WARNING, 0):
        return WARNING
    return OK


def recommend_next_action(status: str) -> str:
    if status == BLOCKED:
        return "block_remaining_extreme_or_unresolved_leverage_rows_before_financial_validation"
    if status == WARNING:
        return "use_final_quality_resolved_inputs_for_research_only_with_quality_flags"
    return "final_quality_resolved_inputs_plausible_for_offline_research_only"


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def count_values(series: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.value_counts(dropna=False).to_dict().items()}


def count_flags(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.astype(str):
        for flag in value.split(";"):
            if not flag or flag == "OK":
                continue
            counts[flag] = counts.get(flag, 0) + 1
    return counts


def add_flag(flags: list[list[str]], mask: pd.Series | np.ndarray, flag: str) -> None:
    mask_array = pd.Series(mask).fillna(False).to_numpy(dtype=bool)
    for idx, enabled in enumerate(mask_array):
        if enabled and flag not in flags[idx]:
            flags[idx].append(flag)


def sample_rows_for_report(frame: pd.DataFrame, mask: pd.Series, limit: int) -> list[dict[str, Any]]:
    columns = [
        "trade_id",
        "symbol",
        "entry_price_repaired",
        "exit_price_repaired",
        "side_repaired",
        "volume_repaired",
        "leverage_consistent",
        "leverage_resolved",
        "raw_return_consistent",
        "raw_return_resolved",
        "pnl_consistent",
        "pnl_resolved",
        "price_return_pct",
        "leveraged_price_return_pct",
        "final_quality_status",
        "final_quality_flags",
    ]
    samples: list[dict[str, Any]] = []
    for idx, row in frame.loc[mask, columns].head(max(0, int(limit))).iterrows():
        samples.append({"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in columns}})
    return samples


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
    resolved: pd.DataFrame,
    report: FinalFinancialQualityResolutionReport,
    output_path: str | Path,
    report_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    resolved.to_parquet(tmp_path, index=False)
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
