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


class NormalizedReturnError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedReturnReport:
    status: str
    rows: int
    output_path: str
    cost_assumptions: dict[str, float]
    columns_used: dict[str, str | None]
    quality_flag_counts: dict[str, int]
    raw_return_stats: dict[str, Any]
    gross_return_stats: dict[str, Any]
    leveraged_return_stats: dict[str, Any]
    net_return_stats: dict[str, Any]
    outlier_summary: dict[str, int]
    outlier_samples: list[dict[str, Any]]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_normalized_return_sidecar(
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
    fee_bps: float = 8.0,
    slippage_bps: float = 5.0,
    spread_bps: float = 3.0,
    max_abs_net_return_pct: float = 100.0,
    sample_outliers: int = 30,
) -> tuple[pd.DataFrame, NormalizedReturnReport]:
    if not isinstance(frame, pd.DataFrame):
        raise NormalizedReturnError("normalized_return_input_must_be_dataframe")
    for required in (id_column, target_column):
        if required not in frame.columns:
            raise NormalizedReturnError(f"required_column_missing:{required}")
    if frame[id_column].isna().any():
        raise NormalizedReturnError("id_column_contains_nulls")
    if frame[id_column].duplicated(keep=False).any():
        raise NormalizedReturnError("id_column_contains_duplicates")

    sidecar = pd.DataFrame(index=frame.index)
    sidecar[id_column] = frame[id_column]
    sidecar[symbol_column] = frame[symbol_column] if symbol_column in frame.columns else None
    sidecar[time_column] = frame[time_column] if time_column in frame.columns else pd.NaT
    sidecar[target_column] = frame[target_column]
    sidecar["raw_return_pct"] = numeric_or_nan(frame, raw_return_column)
    sidecar["pnl"] = numeric_or_nan(frame, pnl_column)
    sidecar["entry_price"] = numeric_or_nan(frame, entry_price_column)
    sidecar["exit_price"] = numeric_or_nan(frame, exit_price_column)
    sidecar[volume_column] = numeric_or_nan(frame, volume_column)
    sidecar[leverage_column] = numeric_or_nan(frame, leverage_column)

    direction, direction_flags = normalize_direction(frame, side_column)
    sidecar["side"] = direction
    flags = initialize_flags(len(frame), direction_flags)

    entry = sidecar["entry_price"]
    exit_ = sidecar["exit_price"]
    valid_entry = entry.gt(0)
    valid_exit = exit_.gt(0)
    add_flag(flags, ~valid_entry, "entry_price_invalid")
    add_flag(flags, ~valid_exit, "exit_price_invalid")

    raw_gross = ((exit_ - entry) / entry) * 100.0
    gross = pd.Series(np.nan, index=frame.index, dtype=float)
    gross.loc[valid_entry & valid_exit & direction.eq("LONG")] = raw_gross
    gross.loc[valid_entry & valid_exit & direction.eq("SHORT")] = -raw_gross
    sidecar["gross_return_pct"] = gross
    sidecar["normalized_return_pct"] = gross

    leverage = sidecar[leverage_column].copy()
    invalid_leverage = leverage.isna() | leverage.le(0)
    add_flag(flags, invalid_leverage, "leverage_invalid_defaulted_to_1")
    leverage = leverage.mask(invalid_leverage, 1.0)
    sidecar[leverage_column] = leverage

    volume = sidecar[volume_column]
    if volume_column in frame.columns:
        add_flag(flags, volume.isna() | volume.le(0), "volume_invalid")

    total_cost_pct = bps_to_percent(fee_bps) + bps_to_percent(slippage_bps) + bps_to_percent(spread_bps)
    sidecar["leveraged_return_pct"] = sidecar["gross_return_pct"] * leverage
    sidecar["estimated_fee_pct"] = bps_to_percent(fee_bps)
    sidecar["estimated_slippage_pct"] = bps_to_percent(slippage_bps)
    sidecar["estimated_spread_pct"] = bps_to_percent(spread_bps)
    sidecar["estimated_total_cost_pct"] = total_cost_pct
    sidecar["net_return_pct"] = sidecar["leveraged_return_pct"] - total_cost_pct

    raw_diff = (sidecar["raw_return_pct"] - sidecar["net_return_pct"]).abs()
    add_flag(flags, raw_diff.gt(10.0), "raw_return_discrepant")
    add_flag(flags, sidecar["net_return_pct"].abs().gt(max_abs_net_return_pct), "net_return_extreme")
    if pnl_column in frame.columns and volume_column in frame.columns:
        notional = entry * volume
        implied_pnl = notional * (sidecar["net_return_pct"] / 100.0)
        pnl_error = (sidecar["pnl"] - implied_pnl).abs()
        add_flag(flags, pnl_error.gt(notional.abs() * 0.10), "pnl_incompatible")

    sidecar["quality_flags"] = [";".join(items) if items else "OK" for items in flags]
    sidecar = sidecar.reset_index(drop=True)

    quality_counts = count_quality_flags(sidecar["quality_flags"])
    outlier_mask = sidecar["quality_flags"].str.contains("net_return_extreme|entry_price_invalid|exit_price_invalid")
    outlier_samples = collect_outlier_samples(sidecar, outlier_mask, sample_outliers)
    outlier_summary = {
        "net_return_extreme": quality_counts.get("net_return_extreme", 0),
        "entry_price_invalid": quality_counts.get("entry_price_invalid", 0),
        "exit_price_invalid": quality_counts.get("exit_price_invalid", 0),
        "total_outlier_rows": int(outlier_mask.sum()),
    }
    status = classify_status(len(sidecar), quality_counts, outlier_summary)
    report = NormalizedReturnReport(
        status=status,
        rows=int(len(sidecar)),
        output_path=str(output_path),
        cost_assumptions={
            "fee_bps": float(fee_bps),
            "slippage_bps": float(slippage_bps),
            "spread_bps": float(spread_bps),
            "total_cost_pct": float(total_cost_pct),
        },
        columns_used={
            "id_column": id_column,
            "symbol_column": symbol_column if symbol_column in frame.columns else None,
            "target_column": target_column,
            "time_column": time_column if time_column in frame.columns else None,
            "entry_price_column": entry_price_column if entry_price_column in frame.columns else None,
            "exit_price_column": exit_price_column if exit_price_column in frame.columns else None,
            "side_column": side_column if side_column in frame.columns else None,
            "volume_column": volume_column if volume_column in frame.columns else None,
            "leverage_column": leverage_column if leverage_column in frame.columns else None,
            "raw_return_column": raw_return_column if raw_return_column in frame.columns else None,
            "pnl_column": pnl_column if pnl_column in frame.columns else None,
        },
        quality_flag_counts=quality_counts,
        raw_return_stats=numeric_stats(sidecar["raw_return_pct"]),
        gross_return_stats=numeric_stats(sidecar["gross_return_pct"]),
        leveraged_return_stats=numeric_stats(sidecar["leveraged_return_pct"]),
        net_return_stats=numeric_stats(sidecar["net_return_pct"]),
        outlier_summary=outlier_summary,
        outlier_samples=outlier_samples,
        recommended_next_action=recommend_next_action(status, quality_counts),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return sidecar, report


def normalize_direction(frame: pd.DataFrame, side_column: str) -> tuple[pd.Series, list[list[str]]]:
    flags: list[list[str]] = [[] for _ in range(len(frame))]
    if side_column not in frame.columns:
        for item in flags:
            item.append("side_unknown")
        return pd.Series(["UNKNOWN"] * len(frame), index=frame.index), flags
    text = frame[side_column].astype(str).str.lower()
    long_mask = text.str.contains("long|buy|compr|alta")
    short_mask = text.str.contains("short|sell|vend|baixa")
    direction = pd.Series("UNKNOWN", index=frame.index, dtype=object)
    direction.loc[long_mask] = "LONG"
    direction.loc[short_mask] = "SHORT"
    unknown = direction.eq("UNKNOWN")
    for idx in np.flatnonzero(unknown.to_numpy()):
        flags[idx].append("side_unknown")
    return direction, flags


def initialize_flags(size: int, initial: list[list[str]]) -> list[list[str]]:
    if len(initial) == size:
        return [list(items) for items in initial]
    return [[] for _ in range(size)]


def add_flag(flags: list[list[str]], mask: pd.Series | np.ndarray, flag: str) -> None:
    mask_array = pd.Series(mask).fillna(False).to_numpy(dtype=bool)
    for idx, enabled in enumerate(mask_array):
        if enabled and flag not in flags[idx]:
            flags[idx].append(flag)


def bps_to_percent(value: float) -> float:
    return float(value) * 0.01


def numeric_or_nan(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


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


def count_quality_flags(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.astype(str):
        for flag in value.split(";"):
            if not flag or flag == "OK":
                continue
            counts[flag] = counts.get(flag, 0) + 1
    return counts


def collect_outlier_samples(sidecar: pd.DataFrame, mask: pd.Series, limit: int) -> list[dict[str, Any]]:
    columns = [
        column
        for column in ("trade_id", "symbol", "raw_return_pct", "gross_return_pct", "net_return_pct", "quality_flags")
        if column in sidecar.columns
    ]
    samples: list[dict[str, Any]] = []
    for idx, row in sidecar.loc[mask, columns].head(max(0, int(limit))).iterrows():
        samples.append({"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in columns}})
    return samples


def classify_status(row_count: int, quality_counts: dict[str, int], outlier_summary: dict[str, int]) -> str:
    if row_count <= 0:
        return BLOCKED
    critical = (
        outlier_summary.get("net_return_extreme", 0)
        + outlier_summary.get("entry_price_invalid", 0)
        + outlier_summary.get("exit_price_invalid", 0)
    )
    if critical:
        return BLOCKED
    invalid_inputs = (
        quality_counts.get("side_unknown", 0)
        + quality_counts.get("leverage_invalid_defaulted_to_1", 0)
        + quality_counts.get("volume_invalid", 0)
    )
    if invalid_inputs / row_count > 0.20:
        return BLOCKED
    if invalid_inputs or quality_counts.get("raw_return_discrepant", 0):
        return WARNING
    return OK


def recommend_next_action(status: str, quality_counts: dict[str, int]) -> str:
    if status == BLOCKED:
        return "block_normalized_financial_metrics_until_required_price_side_inputs_are_repaired"
    if status == WARNING:
        return "use_normalized_returns_for_research_only_with_quality_flags"
    if quality_counts:
        return "continue_research_only_and_monitor_quality_flags"
    return "normalized_returns_plausible_for_offline_research_only"


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
    sidecar: pd.DataFrame,
    report: NormalizedReturnReport,
    output_path: str | Path,
    report_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    sidecar.to_parquet(tmp_path, index=False)
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
