from __future__ import annotations

import json
import math
import re
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


class TradeFinancialInputRepairError(ValueError):
    pass


@dataclass(frozen=True)
class TradeFinancialInputRepairReport:
    status: str
    rows: int
    output_path: str
    repair_status_counts: dict[str, int]
    repair_flag_counts: dict[str, int]
    leverage_stats_before_after: dict[str, Any]
    volume_stats_before_after: dict[str, Any]
    price_return_stats: dict[str, Any]
    raw_return_comparison: dict[str, Any]
    blocked_summary: dict[str, int]
    warning_summary: dict[str, int]
    sample_blocked_rows: list[dict[str, Any]]
    sample_warning_rows: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repair_trade_financial_inputs(
    frame: pd.DataFrame,
    *,
    output_path: str | Path,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    target_column: str = "target_win",
    time_column: str = "open_1m_ts",
    entry_price_column: str = "entry_price",
    exit_price_column: str = "exit_price",
    side_column: str = "fechar_side",
    volume_column: str = "volume_posicao",
    leverage_column: str = "leverage",
    raw_return_column: str = "return_pct",
    pnl_column: str = "pnl",
    original_pnl_column: str = "pnl_fechado",
    original_return_column: str = "taxa_lucros_perdas_fechados_pct",
    max_abs_price_return_pct: float = 20.0,
    max_leverage: float = 125.0,
    sample_rows: int = 50,
) -> tuple[pd.DataFrame, TradeFinancialInputRepairReport]:
    if not isinstance(frame, pd.DataFrame):
        raise TradeFinancialInputRepairError("repair_input_must_be_dataframe")
    if id_column not in frame.columns:
        raise TradeFinancialInputRepairError(f"required_column_missing:{id_column}")
    if frame[id_column].isna().any():
        raise TradeFinancialInputRepairError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise TradeFinancialInputRepairError("id_column_contains_duplicates")

    repaired = pd.DataFrame(index=frame.index)
    repaired["trade_id"] = frame[id_column]
    repaired["symbol"] = frame[symbol_column] if symbol_column in frame.columns else None
    repaired["open_1m_ts"] = frame[time_column] if time_column in frame.columns else pd.NaT
    repaired["target_win"] = frame[target_column] if target_column in frame.columns else np.nan

    entry_original = original_series(frame, entry_price_column)
    exit_original = original_series(frame, exit_price_column)
    side_original = original_series(frame, side_column)
    volume_original = original_series(frame, volume_column)
    leverage_original = original_series(frame, leverage_column)
    pnl_original = choose_original_series(frame, pnl_column, original_pnl_column)
    raw_return_original = choose_original_series(frame, raw_return_column, original_return_column)

    repaired["entry_price_original"] = entry_original
    repaired["exit_price_original"] = exit_original
    repaired["side_original"] = side_original
    repaired["volume_original"] = volume_original
    repaired["leverage_original"] = leverage_original
    repaired["pnl_original"] = pnl_original
    repaired["raw_return_original"] = raw_return_original

    flags: list[list[str]] = [[] for _ in range(len(frame))]

    entry_repaired = parse_numeric_series(entry_original)
    exit_repaired = parse_numeric_series(exit_original)
    volume_repaired = parse_numeric_series(volume_original)
    leverage_repaired = parse_leverage_series(leverage_original)
    pnl_repaired = parse_numeric_series(pnl_original)
    raw_return_repaired = parse_numeric_series(raw_return_original)
    original_return_candidate = (
        parse_numeric_series(frame[original_return_column])
        if original_return_column in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )

    add_flag(flags, entry_repaired.isna() | entry_repaired.le(0), "entry_price_invalid")
    add_flag(flags, exit_repaired.isna() | exit_repaired.le(0), "exit_price_invalid")
    add_flag(flags, volume_repaired.isna() | volume_repaired.le(0), "volume_invalid")
    add_flag(flags, leverage_repaired.isna() | leverage_repaired.le(0), "leverage_invalid")
    add_flag(flags, leverage_repaired.gt(max_leverage), "leverage_above_max")

    leverage_repaired = leverage_repaired.mask(leverage_repaired.le(0) | leverage_repaired.gt(max_leverage), np.nan)
    side_repaired, side_flags = normalize_side(side_original)
    for idx, row_flags in enumerate(side_flags):
        for flag in row_flags:
            append_flag(flags[idx], flag)

    valid_prices = entry_repaired.gt(0) & exit_repaired.gt(0)
    price_return_pct = calculate_price_return(entry_repaired, exit_repaired, side_repaired)
    add_flag(flags, valid_prices & price_return_pct.abs().gt(max_abs_price_return_pct), "price_return_exceeds_plausible_range")

    raw_reference = raw_return_repaired.combine_first(original_return_candidate)
    discrepancy = (raw_reference - price_return_pct).abs()
    add_flag(flags, valid_prices & raw_reference.notna() & discrepancy.gt(5.0), "raw_return_discrepant")
    add_flag(flags, raw_return_repaired.isna() & original_return_candidate.notna(), "raw_return_repaired_from_original_return")

    status = classify_rows(flags)

    repaired["entry_price_repaired"] = entry_repaired
    repaired["exit_price_repaired"] = exit_repaired
    repaired["side_repaired"] = side_repaired
    repaired["volume_repaired"] = volume_repaired
    repaired["leverage_repaired"] = leverage_repaired
    repaired["pnl_repaired"] = pnl_repaired
    repaired["raw_return_repaired"] = raw_reference
    repaired["price_return_pct"] = price_return_pct
    repaired["repair_status"] = status
    repaired["repair_flags"] = [";".join(items) if items else "OK" for items in flags]
    repaired = repaired.reset_index(drop=True)

    status_counts = count_values(repaired["repair_status"])
    flag_counts = count_flags(repaired["repair_flags"])
    blocked_mask = repaired["repair_status"].eq(BLOCKED)
    warning_mask = repaired["repair_status"].eq(WARNING)
    report = TradeFinancialInputRepairReport(
        status=classify_report_status(len(repaired), status_counts),
        rows=int(len(repaired)),
        output_path=str(output_path),
        repair_status_counts=status_counts,
        repair_flag_counts=flag_counts,
        leverage_stats_before_after={
            "before": numeric_stats(parse_numeric_series(leverage_original)),
            "after": numeric_stats(repaired["leverage_repaired"]),
        },
        volume_stats_before_after={
            "before": numeric_stats(parse_numeric_series(volume_original)),
            "after": numeric_stats(repaired["volume_repaired"]),
        },
        price_return_stats=numeric_stats(repaired["price_return_pct"]),
        raw_return_comparison=raw_return_comparison(raw_reference, repaired["price_return_pct"]),
        blocked_summary=summarize_flags(repaired.loc[blocked_mask, "repair_flags"]),
        warning_summary=summarize_flags(repaired.loc[warning_mask, "repair_flags"]),
        sample_blocked_rows=sample_rows_for_report(repaired, blocked_mask, sample_rows),
        sample_warning_rows=sample_rows_for_report(repaired, warning_mask, sample_rows),
        recommended_next_action=recommend_next_action(classify_report_status(len(repaired), status_counts)),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return repaired, report


def original_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(np.nan, index=frame.index)


def choose_original_series(frame: pd.DataFrame, preferred: str, fallback: str) -> pd.Series:
    if preferred in frame.columns:
        return frame[preferred]
    if fallback in frame.columns:
        return frame[fallback]
    return pd.Series(np.nan, index=frame.index)


def parse_numeric_value(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return safe_float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"[^0-9,\.\-\+]", "", text)
    if not text or text in {"-", "+", ".", ","}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return safe_float(text)


def parse_numeric_series(series: pd.Series) -> pd.Series:
    return pd.Series([parse_numeric_value(value) for value in series], index=series.index, dtype=float)


def parse_leverage_series(series: pd.Series) -> pd.Series:
    return parse_numeric_series(series)


def normalize_side(series: pd.Series) -> tuple[pd.Series, list[list[str]]]:
    flags: list[list[str]] = [[] for _ in range(len(series))]
    values: list[str] = []
    for idx, value in enumerate(series):
        text = "" if value is None or pd.isna(value) else str(value).strip().lower()
        text = text.replace("_", " ").replace("-", " ")
        if not text:
            values.append("UNKNOWN")
            flags[idx].append("side_unknown")
            continue
        if "close long" in text or "fechar long" in text or "fecha long" in text:
            values.append("LONG")
            flags[idx].append("side_inferred_from_close_long")
        elif "close short" in text or "fechar short" in text or "fecha short" in text:
            values.append("SHORT")
            flags[idx].append("side_inferred_from_close_short")
        elif "long" in text or "buy long" in text or "comprado" in text:
            values.append("LONG")
        elif "short" in text or "sell short" in text or "vendido" in text:
            values.append("SHORT")
        elif "sell" in text or "venda" in text or "vend" in text:
            values.append("LONG")
            flags[idx].append("side_inferred_from_close_sell")
        elif "buy" in text or "compra" in text or "compr" in text:
            values.append("SHORT")
            flags[idx].append("side_inferred_from_close_buy")
        else:
            values.append("UNKNOWN")
            flags[idx].append("side_unknown")
    return pd.Series(values, index=series.index, dtype=object), flags


def calculate_price_return(entry: pd.Series, exit_: pd.Series, side: pd.Series) -> pd.Series:
    gross = ((exit_ - entry) / entry) * 100.0
    result = pd.Series(np.nan, index=entry.index, dtype=float)
    valid = entry.gt(0) & exit_.gt(0)
    result.loc[valid & side.eq("LONG")] = gross
    result.loc[valid & side.eq("SHORT")] = -gross
    result.loc[valid & side.eq("UNKNOWN")] = gross.abs()
    return result.replace([np.inf, -np.inf], np.nan)


def classify_rows(flags: list[list[str]]) -> list[str]:
    blocked_flags = {
        "entry_price_invalid",
        "exit_price_invalid",
        "volume_invalid",
        "leverage_invalid",
        "leverage_above_max",
        "side_unknown",
        "price_return_exceeds_plausible_range",
    }
    warning_flags = {
        "raw_return_discrepant",
        "raw_return_repaired_from_original_return",
        "side_inferred_from_close_sell",
        "side_inferred_from_close_buy",
        "side_inferred_from_close_long",
        "side_inferred_from_close_short",
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


def raw_return_comparison(raw_return: pd.Series, price_return: pd.Series) -> dict[str, Any]:
    valid = raw_return.notna() & price_return.notna()
    if not valid.any():
        return {"available": False, "reason": "no_valid_raw_and_price_return_rows"}
    diff = raw_return[valid] - price_return[valid]
    relative = (diff.abs() / price_return[valid].abs().replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    return {
        "available": True,
        "rows_compared": int(valid.sum()),
        "mean_error": safe_float(diff.mean()),
        "median_error": safe_float(diff.median()),
        "abs_error_mean": safe_float(diff.abs().mean()),
        "abs_error_p95": safe_float(diff.abs().quantile(0.95)),
        "relative_error_mean": safe_float(relative.mean()),
        "relative_error_p95": safe_float(relative.quantile(0.95)),
    }


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


def sample_rows_for_report(frame: pd.DataFrame, mask: pd.Series, limit: int) -> list[dict[str, Any]]:
    columns = [
        "trade_id",
        "symbol",
        "entry_price_repaired",
        "exit_price_repaired",
        "side_repaired",
        "volume_repaired",
        "leverage_repaired",
        "raw_return_repaired",
        "price_return_pct",
        "repair_status",
        "repair_flags",
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


def summarize_flags(series: pd.Series) -> dict[str, int]:
    return count_flags(series)


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
        return "block_normalized_return_sidecar_until_financial_inputs_are_repaired_or_excluded"
    if status == WARNING:
        return "use_repaired_financial_inputs_for_diagnostic_research_only_and_review_blocked_rows"
    return "financial_inputs_repaired_for_offline_research_only"


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
    report: TradeFinancialInputRepairReport,
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
