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


class LeveragePnlReturnConsistencyError(ValueError):
    pass


@dataclass(frozen=True)
class LeveragePnlReturnConsistencyReport:
    status: str
    rows: int
    output_path: str
    consistency_status_counts: dict[str, int]
    consistency_flag_counts: dict[str, int]
    leverage_resolution_summary: dict[str, int]
    raw_return_semantics_summary: dict[str, int]
    pnl_consistency_summary: dict[str, int]
    net_extreme_candidates: dict[str, int]
    blocked_summary: dict[str, int]
    warning_summary: dict[str, int]
    sample_blocked_rows: list[dict[str, Any]]
    sample_warning_rows: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repair_leverage_pnl_return_consistency(
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
    leverage_column: str = "leverage_repaired",
    raw_return_column: str = "raw_return_repaired",
    pnl_column: str = "pnl_repaired",
    original_pnl_column: str = "pnl_fechado",
    original_return_column: str = "taxa_lucros_perdas_fechados_pct",
    max_leverage: float = 125.0,
    default_leverage_policy: str = "block",
    raw_return_discrepancy_threshold: float = 5.0,
    pnl_tolerance_pct: float = 5.0,
    sample_rows: int = 50,
) -> tuple[pd.DataFrame, LeveragePnlReturnConsistencyReport]:
    if not isinstance(frame, pd.DataFrame):
        raise LeveragePnlReturnConsistencyError("consistency_input_must_be_dataframe")
    if default_leverage_policy != "block":
        raise LeveragePnlReturnConsistencyError("unsupported_default_leverage_policy:only_block_is_allowed")
    for required in (id_column, entry_price_column, exit_price_column, side_column, volume_column, leverage_column):
        if required not in frame.columns:
            raise LeveragePnlReturnConsistencyError(f"required_column_missing:{required}")
    if id_column not in frame.columns:
        raise LeveragePnlReturnConsistencyError(f"required_column_missing:{id_column}")
    if frame[id_column].isna().any():
        raise LeveragePnlReturnConsistencyError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise LeveragePnlReturnConsistencyError("id_column_contains_duplicates")

    result = pd.DataFrame(index=frame.index)
    result["trade_id"] = frame[id_column]
    result["symbol"] = frame[symbol_column] if symbol_column in frame.columns else None
    result["open_1m_ts"] = frame[time_column] if time_column in frame.columns else pd.NaT
    result["target_win"] = frame[target_column] if target_column in frame.columns else np.nan

    entry = numeric_series(frame[entry_price_column])
    exit_ = numeric_series(frame[exit_price_column])
    side = normalize_side(frame[side_column])
    volume = numeric_series(frame[volume_column])
    leverage_original = numeric_series(frame[leverage_column])
    pnl_original = choose_numeric(frame, pnl_column, original_pnl_column)
    raw_return_original = choose_numeric(frame, raw_return_column, original_return_column)

    flags: list[list[str]] = [[] for _ in range(len(frame))]
    add_flag(flags, entry.isna() | entry.le(0), "entry_price_invalid")
    add_flag(flags, exit_.isna() | exit_.le(0), "exit_price_invalid")
    add_flag(flags, side.eq("UNKNOWN"), "side_unknown")
    add_flag(flags, volume.isna() | volume.le(0), "volume_invalid")
    add_flag(flags, leverage_original.isna(), "leverage_missing")
    add_flag(flags, leverage_original.eq(0), "leverage_zero")
    add_flag(flags, leverage_original.lt(0), "leverage_negative")
    add_flag(flags, leverage_original.gt(max_leverage), "leverage_above_max")

    leverage_valid = leverage_original.notna() & leverage_original.gt(0) & leverage_original.le(max_leverage)
    leverage_consistent = leverage_original.where(leverage_valid, np.nan)
    price_return_pct = calculate_price_return(entry, exit_, side)
    leveraged_price_return_pct = price_return_pct * leverage_consistent
    expected_pnl = calculate_expected_pnl(entry, exit_, side, volume)

    raw_semantics = classify_raw_return_semantics(
        raw_return_original,
        price_return_pct,
        leveraged_price_return_pct,
        threshold=raw_return_discrepancy_threshold,
    )
    for idx, semantic in enumerate(raw_semantics):
        if semantic == "sentinel_or_ocr_error":
            append_flag(flags[idx], "raw_return_sentinel")
        elif semantic == "discrepant":
            append_flag(flags[idx], "raw_return_discrepant")
        elif semantic == "missing":
            append_flag(flags[idx], "raw_return_missing")

    pnl_state = classify_pnl_consistency(
        pnl_original,
        expected_pnl,
        entry=entry,
        volume=volume,
        tolerance_pct=pnl_tolerance_pct,
    )
    pnl_error_abs = (pnl_original - expected_pnl).abs().replace([np.inf, -np.inf], np.nan)
    pnl_error_pct = calculate_pnl_error_pct(pnl_original, expected_pnl)
    pnl_warning_only = pd.Series(False, index=frame.index, dtype=bool)
    for idx, state in enumerate(pnl_state):
        if state == "missing":
            append_flag(flags[idx], "pnl_missing")
        elif state == "incompatible":
            append_flag(flags[idx], "pnl_incompatible")
            pnl_warning_only.iloc[idx] = True

    add_flag(flags, leveraged_price_return_pct.abs().gt(100.0), "net_return_extreme_candidate")
    statuses = classify_rows(flags)
    status_series = pd.Series(statuses, index=frame.index)

    raw_return_consistent = pd.Series(np.nan, index=frame.index, dtype=float)
    usable_return_mask = status_series.isin([OK, WARNING])
    raw_return_consistent.loc[usable_return_mask] = leveraged_price_return_pct.loc[usable_return_mask]
    pnl_consistent = pd.Series(np.nan, index=frame.index, dtype=float)
    pnl_consistent.loc[usable_return_mask] = expected_pnl.loc[usable_return_mask]

    result["entry_price_repaired"] = entry
    result["exit_price_repaired"] = exit_
    result["side_repaired"] = side
    result["volume_repaired"] = volume
    result["leverage_original"] = leverage_original
    result["leverage_consistent"] = leverage_consistent
    result["pnl_original"] = pnl_original
    result["pnl_consistent"] = pnl_consistent
    result["raw_return_original"] = raw_return_original
    result["raw_return_consistent"] = raw_return_consistent
    result["price_return_pct"] = price_return_pct
    result["leveraged_price_return_pct"] = leveraged_price_return_pct
    result["expected_pnl_from_price"] = expected_pnl
    result["raw_return_semantics"] = raw_semantics
    result["pnl_consistency"] = pnl_state
    result["pnl_semantics_guess"] = guess_pnl_semantics(pnl_state, pnl_error_pct)
    result["pnl_error_abs"] = pnl_error_abs
    result["pnl_error_pct"] = pnl_error_pct
    result["pnl_warning_only"] = pnl_warning_only & status_series.eq(WARNING)
    result["consistency_status"] = statuses
    result["consistency_flags"] = [";".join(items) if items else "OK" for items in flags]
    result = result.reset_index(drop=True)

    blocked_mask = result["consistency_status"].eq(BLOCKED)
    warning_mask = result["consistency_status"].eq(WARNING)
    status_counts = count_values(result["consistency_status"])
    flag_counts = count_flags(result["consistency_flags"])
    report_status = classify_report_status(len(result), status_counts)
    report = LeveragePnlReturnConsistencyReport(
        status=report_status,
        rows=int(len(result)),
        output_path=str(output_path),
        consistency_status_counts=status_counts,
        consistency_flag_counts=flag_counts,
        leverage_resolution_summary={
            "valid": int(leverage_valid.sum()),
            "missing": int(leverage_original.isna().sum()),
            "zero": int(leverage_original.eq(0).sum()),
            "negative": int(leverage_original.lt(0).sum()),
            "above_max": int(leverage_original.gt(max_leverage).sum()),
            "blocked_by_policy": int((~leverage_valid).sum()),
        },
        raw_return_semantics_summary=count_values(pd.Series(raw_semantics)),
        pnl_consistency_summary=count_values(pd.Series(pnl_state)),
        net_extreme_candidates={
            "rows": int(leveraged_price_return_pct.abs().gt(100.0).fillna(False).sum()),
        },
        blocked_summary=count_flags(result.loc[blocked_mask, "consistency_flags"]),
        warning_summary=count_flags(result.loc[warning_mask, "consistency_flags"]),
        sample_blocked_rows=sample_rows_for_report(result, blocked_mask, sample_rows),
        sample_warning_rows=sample_rows_for_report(result, warning_mask, sample_rows),
        recommended_next_action=recommend_next_action(report_status),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return result, report


def choose_numeric(frame: pd.DataFrame, preferred: str, fallback: str) -> pd.Series:
    if preferred in frame.columns:
        return numeric_series(frame[preferred])
    if fallback in frame.columns:
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


def classify_raw_return_semantics(
    raw_return: pd.Series,
    price_return_pct: pd.Series,
    leveraged_price_return_pct: pd.Series,
    *,
    threshold: float,
) -> list[str]:
    semantics: list[str] = []
    for raw, price_ret, leveraged_ret in zip(raw_return, price_return_pct, leveraged_price_return_pct):
        raw_value = safe_float(raw)
        if raw_value is None:
            semantics.append("missing")
            continue
        if abs(raw_value) >= 1000.0 or math.isclose(abs(raw_value), 1999.999999, rel_tol=0, abs_tol=0.01):
            semantics.append("sentinel_or_ocr_error")
            continue
        if close_enough(raw_value, price_ret, threshold):
            semantics.append("percentage_price")
        elif close_enough(raw_value, leveraged_ret, threshold):
            semantics.append("percentage_leveraged")
        elif close_enough(raw_value * 100.0, price_ret, threshold):
            semantics.append("decimal_price")
        elif close_enough(raw_value * 100.0, leveraged_ret, threshold):
            semantics.append("decimal_leveraged")
        else:
            semantics.append("discrepant")
    return semantics


def classify_pnl_consistency(
    pnl: pd.Series,
    expected_pnl: pd.Series,
    *,
    entry: pd.Series,
    volume: pd.Series,
    tolerance_pct: float,
) -> list[str]:
    result: list[str] = []
    notional = (entry * volume).abs()
    for actual, expected, basis in zip(pnl, expected_pnl, notional):
        actual_value = safe_float(actual)
        expected_value = safe_float(expected)
        basis_value = safe_float(basis)
        if actual_value is None:
            result.append("missing")
            continue
        if expected_value is None or basis_value is None or basis_value <= 0:
            result.append("unknown")
            continue
        # Compare against the modeled PnL itself, with a small absolute floor for
        # near-zero trades. Using the full notional as tolerance can mask large
        # PnL errors on normal trades, e.g. expected 20 USDT vs actual 1 USDT.
        tolerance = max(abs(expected_value) * (float(tolerance_pct) / 100.0), 1.0)
        if abs(actual_value - expected_value) <= tolerance:
            result.append("coherent")
        else:
            result.append("incompatible")
    return result


def calculate_pnl_error_pct(pnl: pd.Series, expected_pnl: pd.Series) -> pd.Series:
    denominator = expected_pnl.abs().where(expected_pnl.abs().gt(1e-9), 1.0)
    error_pct = ((pnl - expected_pnl).abs() / denominator) * 100.0
    return error_pct.replace([np.inf, -np.inf], np.nan)


def guess_pnl_semantics(pnl_state: list[str], pnl_error_pct: pd.Series) -> list[str]:
    guesses: list[str] = []
    for state, error in zip(pnl_state, pnl_error_pct):
        error_value = safe_float(error)
        if state == "coherent":
            guesses.append("absolute_price_delta_pnl")
        elif state == "missing":
            guesses.append("missing")
        elif state == "unknown":
            guesses.append("unknown")
        elif error_value is not None and error_value <= 25.0:
            guesses.append("possibly_cost_adjusted_pnl")
        else:
            guesses.append("mixed_or_untrusted_pnl")
    return guesses


def classify_rows(flags: list[list[str]]) -> list[str]:
    blocked_flags = {
        "entry_price_invalid",
        "exit_price_invalid",
        "side_unknown",
        "volume_invalid",
        "leverage_missing",
        "leverage_zero",
        "leverage_negative",
        "leverage_above_max",
        "net_return_extreme_candidate",
    }
    warning_flags = {
        "raw_return_sentinel",
        "raw_return_discrepant",
        "raw_return_missing",
        "pnl_missing",
        "pnl_incompatible",
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
    blocked = status_counts.get(BLOCKED, 0)
    if blocked / row_count >= 0.50:
        return BLOCKED
    if blocked or status_counts.get(WARNING, 0):
        return WARNING
    return OK


def close_enough(left: Any, right: Any, threshold: float) -> bool:
    left_value = safe_float(left)
    right_value = safe_float(right)
    if left_value is None or right_value is None:
        return False
    return abs(left_value - right_value) <= float(threshold)


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def sample_rows_for_report(frame: pd.DataFrame, mask: pd.Series, limit: int) -> list[dict[str, Any]]:
    columns = [
        "trade_id",
        "symbol",
        "entry_price_repaired",
        "exit_price_repaired",
        "side_repaired",
        "volume_repaired",
        "leverage_original",
        "leverage_consistent",
        "raw_return_original",
        "raw_return_consistent",
        "pnl_original",
        "pnl_consistent",
        "price_return_pct",
        "leveraged_price_return_pct",
        "expected_pnl_from_price",
        "pnl_semantics_guess",
        "pnl_error_abs",
        "pnl_error_pct",
        "pnl_warning_only",
        "consistency_status",
        "consistency_flags",
    ]
    samples: list[dict[str, Any]] = []
    for idx, row in frame.loc[mask, columns].head(max(0, int(limit))).iterrows():
        samples.append({"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in columns}})
    return samples


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
        if enabled:
            append_flag(flags[idx], flag)


def append_flag(row_flags: list[str], flag: str) -> None:
    if flag not in row_flags:
        row_flags.append(flag)


def recommend_next_action(status: str) -> str:
    if status == BLOCKED:
        return "block_only_rows_with_invalid_critical_price_side_volume_leverage_or_extreme_returns"
    if status == WARNING:
        return "use_consistency_repaired_financial_inputs_for_diagnostic_research_only"
    return "consistency_repaired_inputs_plausible_for_offline_research_only"


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
    report: LeveragePnlReturnConsistencyReport,
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
