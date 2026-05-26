from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

SIDE_ALIASES = ("side", "direction", "position_side", "fechar_side", "direcao")
TIME_ALIASES = ("open_1m_ts", "open_ts", "timestamp", "datetime", "date")
ORIGINAL_RETURN_HINTS = (
    "taxa_lucros_perdas_fechados_pct",
    "taxa_lucro_perda_fechado_pct",
    "lucros_perdas_fechados_pct",
    "closed_pnl_pct",
)


class ReturnScaleAuditError(ValueError):
    pass


@dataclass(frozen=True)
class ReturnScaleAuditReport:
    status: str
    input_path: str
    sidecar_path: str | None
    rows: int
    columns_used: dict[str, str | None]
    scale_hypothesis: str
    return_pct_stats: dict[str, Any]
    pnl_stats: dict[str, Any]
    by_symbol_stats: dict[str, dict[str, Any]]
    by_target_stats: dict[str, dict[str, Any]]
    outlier_summary: dict[str, int]
    outlier_samples: list[dict[str, Any]]
    recomputation_summary: dict[str, Any]
    suspected_issues: list[str]
    recommended_next_action: str
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_return_scale(
    input_frame: pd.DataFrame,
    sidecar_frame: pd.DataFrame | None = None,
    *,
    input_path: str | Path,
    sidecar_path: str | Path | None = None,
    id_column: str = "trade_id",
    return_column: str = "return_pct",
    pnl_column: str = "pnl",
    entry_price_column: str = "entry_price",
    exit_price_column: str = "exit_price",
    volume_column: str = "volume_posicao",
    leverage_column: str = "leverage",
    target_column: str = "target_win",
    symbol_column: str = "symbol",
    max_abs_return_pct: float = 100.0,
    max_abs_pnl: float = 1_000_000.0,
    sample_outliers: int = 30,
) -> ReturnScaleAuditReport:
    if not isinstance(input_frame, pd.DataFrame):
        raise ReturnScaleAuditError("input_frame_must_be_dataframe")
    if sidecar_frame is not None and not isinstance(sidecar_frame, pd.DataFrame):
        raise ReturnScaleAuditError("sidecar_frame_must_be_dataframe")

    frame = prepare_audit_frame(
        input_frame,
        sidecar_frame,
        id_column=id_column,
        return_column=return_column,
    )
    if return_column not in frame.columns:
        raise ReturnScaleAuditError(f"return_column_missing:{return_column}")

    return_values = numeric_series(frame[return_column])
    pnl_values = numeric_series(frame[pnl_column]) if pnl_column in frame.columns else pd.Series(dtype=float)
    return_stats = numeric_stats(return_values)
    pnl_stats = numeric_stats(pnl_values)

    side_column = find_first_column(frame, SIDE_ALIASES)
    time_column = find_first_column(frame, TIME_ALIASES)
    columns_used = {
        "id_column": id_column if id_column in frame.columns else None,
        "return_column": return_column,
        "pnl_column": pnl_column if pnl_column in frame.columns else None,
        "entry_price_column": entry_price_column if entry_price_column in frame.columns else None,
        "exit_price_column": exit_price_column if exit_price_column in frame.columns else None,
        "volume_column": volume_column if volume_column in frame.columns else None,
        "leverage_column": leverage_column if leverage_column in frame.columns else None,
        "target_column": target_column if target_column in frame.columns else None,
        "symbol_column": symbol_column if symbol_column in frame.columns else None,
        "side_column": side_column,
        "time_column": time_column,
    }

    scale_hypothesis = infer_scale_hypothesis(
        return_values,
        frame=frame,
        leverage_column=leverage_column,
        max_abs_return_pct=max_abs_return_pct,
    )
    outliers = detect_outliers(
        frame,
        return_values=return_values,
        pnl_values=pnl_values,
        return_column=return_column,
        pnl_column=pnl_column,
        entry_price_column=entry_price_column,
        exit_price_column=exit_price_column,
        volume_column=volume_column,
        leverage_column=leverage_column,
        max_abs_return_pct=max_abs_return_pct,
        max_abs_pnl=max_abs_pnl,
    )
    recomputation = recompute_return_summary(
        frame,
        return_values=return_values,
        entry_price_column=entry_price_column,
        exit_price_column=exit_price_column,
        side_column=side_column,
    )
    by_symbol = grouped_stats(frame, symbol_column, return_column)
    by_target = grouped_stats(frame, target_column, return_column)
    if time_column:
        by_symbol["_period_month"] = period_stats(frame, time_column, return_column)

    suspected = suspected_issues(
        scale_hypothesis=scale_hypothesis,
        outlier_summary=outliers["summary"],
        recomputation_summary=recomputation,
        frame=frame,
    )
    status = classify_status(scale_hypothesis, outliers["summary"], recomputation)
    report = ReturnScaleAuditReport(
        status=status,
        input_path=str(input_path),
        sidecar_path=str(sidecar_path) if sidecar_path is not None else None,
        rows=int(len(frame)),
        columns_used=columns_used,
        scale_hypothesis=scale_hypothesis,
        return_pct_stats=return_stats,
        pnl_stats=pnl_stats,
        by_symbol_stats=by_symbol,
        by_target_stats=by_target,
        outlier_summary=outliers["summary"],
        outlier_samples=outliers["samples"][: max(0, int(sample_outliers))],
        recomputation_summary=recomputation,
        suspected_issues=suspected,
        recommended_next_action=recommend_next_action(status, scale_hypothesis, suspected),
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return report


def prepare_audit_frame(
    input_frame: pd.DataFrame,
    sidecar_frame: pd.DataFrame | None,
    *,
    id_column: str,
    return_column: str,
) -> pd.DataFrame:
    if sidecar_frame is None:
        return input_frame.copy(deep=True)
    if id_column not in input_frame.columns:
        raise ReturnScaleAuditError(f"input_id_column_missing:{id_column}")
    if id_column not in sidecar_frame.columns:
        raise ReturnScaleAuditError(f"sidecar_id_column_missing:{id_column}")
    if return_column in input_frame.columns:
        base = input_frame.drop(columns=[return_column]).copy(deep=True)
    else:
        base = input_frame.copy(deep=True)
    sidecar_columns = [
        column
        for column in sidecar_frame.columns
        if column == id_column or column not in base.columns
    ]
    return base.merge(
        sidecar_frame.loc[:, sidecar_columns],
        on=id_column,
        how="left",
        validate="one_to_one",
    )


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def numeric_stats(series: pd.Series) -> dict[str, Any]:
    values = numeric_series(series).dropna()
    stats: dict[str, Any] = {
        "count": int(len(series)),
        "null_count": int(series.isna().sum() + (numeric_series(series).isna() & series.notna()).sum()),
    }
    if values.empty:
        stats.update(
            {
                "min": None,
                "max": None,
                "mean": None,
                "median": None,
                "std": None,
                "p01": None,
                "p05": None,
                "p25": None,
                "p50": None,
                "p75": None,
                "p95": None,
                "p99": None,
                "abs_max": None,
            }
        )
        return stats
    quantiles = values.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99])
    stats.update(
        {
            "min": safe_float(values.min()),
            "max": safe_float(values.max()),
            "mean": safe_float(values.mean()),
            "median": safe_float(values.median()),
            "std": safe_float(values.std(ddof=0)),
            "p01": safe_float(quantiles.loc[0.01]),
            "p05": safe_float(quantiles.loc[0.05]),
            "p25": safe_float(quantiles.loc[0.25]),
            "p50": safe_float(quantiles.loc[0.50]),
            "p75": safe_float(quantiles.loc[0.75]),
            "p95": safe_float(quantiles.loc[0.95]),
            "p99": safe_float(quantiles.loc[0.99]),
            "abs_max": safe_float(values.abs().max()),
        }
    )
    return stats


def infer_scale_hypothesis(
    values: pd.Series,
    *,
    frame: pd.DataFrame,
    leverage_column: str,
    max_abs_return_pct: float,
) -> str:
    clean = values.dropna().abs()
    if clean.empty:
        return "missing_or_non_numeric"
    p95 = float(clean.quantile(0.95))
    p99 = float(clean.quantile(0.99))
    abs_max = float(clean.max())
    if leverage_column in frame.columns:
        leverage = numeric_series(frame[leverage_column]).replace(0, np.nan)
        adjusted = (values.abs() / leverage).dropna()
        if p95 > max_abs_return_pct and not adjusted.empty and float(adjusted.quantile(0.95)) <= max_abs_return_pct:
            return "scale_contaminated_by_leverage"
    if abs_max > max_abs_return_pct * 100 or p99 > max_abs_return_pct * 25:
        return "scale_contaminated_by_ocr_or_extreme_outliers"
    if p95 <= 1.0 and abs_max <= max_abs_return_pct:
        return "decimal_fraction"
    if 1.0 < p95 <= max_abs_return_pct and abs_max <= max_abs_return_pct:
        return "percentage_points"
    if max_abs_return_pct < p95 <= max_abs_return_pct * 25:
        return "percentage_multiplied_by_100_or_unit_mismatch"
    return "inconsistent_scale"


def detect_outliers(
    frame: pd.DataFrame,
    *,
    return_values: pd.Series,
    pnl_values: pd.Series,
    return_column: str,
    pnl_column: str,
    entry_price_column: str,
    exit_price_column: str,
    volume_column: str,
    leverage_column: str,
    max_abs_return_pct: float,
    max_abs_pnl: float,
) -> dict[str, Any]:
    masks: dict[str, pd.Series] = {
        "return_abs_above_limit": return_values.abs() > max_abs_return_pct,
    }
    if pnl_column in frame.columns:
        masks["pnl_abs_above_limit"] = pnl_values.abs() > max_abs_pnl
    if entry_price_column in frame.columns:
        masks["entry_price_invalid"] = numeric_series(frame[entry_price_column]) <= 0
    if exit_price_column in frame.columns:
        masks["exit_price_invalid"] = numeric_series(frame[exit_price_column]) <= 0
    if volume_column in frame.columns:
        masks["volume_invalid"] = numeric_series(frame[volume_column]) <= 0
    if leverage_column in frame.columns:
        masks["leverage_invalid"] = numeric_series(frame[leverage_column]) <= 0

    summary = {name: int(mask.fillna(False).sum()) for name, mask in masks.items()}
    combined = pd.Series(False, index=frame.index)
    for mask in masks.values():
        combined = combined | mask.fillna(False)
    samples: list[dict[str, Any]] = []
    sample_columns = [
        column
        for column in (
            "trade_id",
            "symbol",
            return_column,
            pnl_column,
            entry_price_column,
            exit_price_column,
            volume_column,
            leverage_column,
        )
        if column in frame.columns
    ]
    for idx, row in frame.loc[combined, sample_columns].head(200).iterrows():
        sample = {"row_index": int(idx), **{column: normalize_json_value(row[column]) for column in sample_columns}}
        sample["reasons"] = [name for name, mask in masks.items() if bool(mask.fillna(False).loc[idx])]
        samples.append(sample)
    summary["total_outlier_rows"] = int(combined.sum())
    return {"summary": summary, "samples": samples}


def recompute_return_summary(
    frame: pd.DataFrame,
    *,
    return_values: pd.Series,
    entry_price_column: str,
    exit_price_column: str,
    side_column: str | None,
) -> dict[str, Any]:
    if entry_price_column not in frame.columns or exit_price_column not in frame.columns:
        return {"available": False, "reason": "entry_or_exit_price_missing"}
    entry = numeric_series(frame[entry_price_column])
    exit_ = numeric_series(frame[exit_price_column])
    valid = entry.gt(0) & exit_.gt(0) & return_values.notna()
    if not valid.any():
        return {"available": False, "reason": "no_valid_price_rows"}
    direction = pd.Series(1.0, index=frame.index)
    if side_column and side_column in frame.columns:
        side_text = frame[side_column].astype(str).str.lower()
        direction = pd.Series(np.where(side_text.str.contains("short|sell"), -1.0, 1.0), index=frame.index)
    recomputed = ((exit_ - entry) / entry) * 100.0 * direction
    diff = return_values - recomputed
    valid_diff = diff[valid]
    relative = (valid_diff.abs() / recomputed[valid].abs().replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    return {
        "available": True,
        "rows_compared": int(valid.sum()),
        "return_pct_minus_recomputed_mean": safe_float(valid_diff.mean()),
        "return_pct_minus_recomputed_median": safe_float(valid_diff.median()),
        "abs_error_mean": safe_float(valid_diff.abs().mean()),
        "abs_error_p95": safe_float(valid_diff.abs().quantile(0.95)),
        "relative_error_mean": safe_float(relative.mean()),
        "relative_error_p95": safe_float(relative.quantile(0.95)),
    }


def grouped_stats(frame: pd.DataFrame, group_column: str, value_column: str) -> dict[str, dict[str, Any]]:
    if group_column not in frame.columns or value_column not in frame.columns:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, group in frame.groupby(group_column, dropna=False):
        result[str(key)] = numeric_stats(group[value_column])
    return result


def period_stats(frame: pd.DataFrame, time_column: str, value_column: str) -> dict[str, dict[str, Any]]:
    timestamps = pd.to_datetime(frame[time_column], utc=True, errors="coerce")
    if timestamps.isna().all():
        return {}
    working = frame.copy()
    working["_period"] = timestamps.dt.tz_convert(None).dt.to_period("M").astype(str)
    return grouped_stats(working, "_period", value_column)


def suspected_issues(
    *,
    scale_hypothesis: str,
    outlier_summary: dict[str, int],
    recomputation_summary: dict[str, Any],
    frame: pd.DataFrame,
) -> list[str]:
    issues: list[str] = []
    if scale_hypothesis != "decimal_fraction" and scale_hypothesis != "percentage_points":
        issues.append(scale_hypothesis)
    for name, count in outlier_summary.items():
        if count and name != "total_outlier_rows":
            issues.append(name)
    if recomputation_summary.get("available"):
        if (recomputation_summary.get("abs_error_p95") or 0) > 5:
            issues.append("return_pct_diverges_from_entry_exit_recomputed")
    else:
        issues.append(str(recomputation_summary.get("reason")))
    original_candidates = [column for column in ORIGINAL_RETURN_HINTS if column in frame.columns]
    if original_candidates:
        issues.append("original_return_candidate_present:" + ",".join(original_candidates))
    return unique_strings(issues)


def classify_status(
    scale_hypothesis: str,
    outlier_summary: dict[str, int],
    recomputation_summary: dict[str, Any],
) -> str:
    critical_outliers = (
        outlier_summary.get("return_abs_above_limit", 0)
        + outlier_summary.get("pnl_abs_above_limit", 0)
        + outlier_summary.get("entry_price_invalid", 0)
        + outlier_summary.get("exit_price_invalid", 0)
    )
    if scale_hypothesis in {
        "scale_contaminated_by_ocr_or_extreme_outliers",
        "inconsistent_scale",
    }:
        return BLOCKED
    if critical_outliers:
        return BLOCKED
    if recomputation_summary.get("available") and (recomputation_summary.get("abs_error_p95") or 0) > 10:
        return BLOCKED
    if scale_hypothesis in {
        "percentage_multiplied_by_100_or_unit_mismatch",
        "scale_contaminated_by_leverage",
        "missing_or_non_numeric",
    }:
        return WARNING
    if outlier_summary.get("volume_invalid", 0) or outlier_summary.get("leverage_invalid", 0):
        return WARNING
    return OK


def recommend_next_action(status: str, scale_hypothesis: str, issues: list[str]) -> str:
    if status == BLOCKED:
        return "block_financial_metrics_until_return_scale_is_repaired"
    if status == WARNING:
        return f"review_return_scale_before_using_financial_metrics:{scale_hypothesis}"
    if issues:
        return "document_limitations_and_continue_research_only"
    return "scale_plausible_continue_offline_research_only"


def find_first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in frame.columns:
            return column
    return None


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


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"dataset_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
