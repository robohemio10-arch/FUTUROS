from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.data.dataset_manifest import (
    read_table,
    safety_payload,
    unsafe_safety_flags,
    utc_timestamp,
    write_json_if_requested,
)


DEFAULT_REPORT_PATH = Path("data/reports/data_quality_report.json")
REPORT_VERSION = "1.0"
VALID_SIDES = {"long", "short", "buy", "sell"}


def build_data_quality_report(
    *,
    datasets: dict[str, str | Path | None],
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_file = Path(report_path) if report_path is not None else None
    safe = safety_payload(safety_overrides)
    validation_errors: list[str] = []
    warnings: list[str] = []
    dataset_reports: dict[str, Any] = {}
    if unsafe := unsafe_safety_flags(safe):
        validation_errors.extend(f"unsafe_safety_flag:{item}" for item in unsafe)

    for role, path_value in datasets.items():
        if not path_value:
            continue
        dataset_report = audit_dataset(role=role, path=Path(path_value), strict=strict)
        dataset_reports[role] = dataset_report
        validation_errors.extend(dataset_report.get("validation_errors", []))
        warnings.extend(dataset_report.get("warnings", []))

    if strict:
        missing_required = [
            role
            for role, path_value in datasets.items()
            if path_value and dataset_reports.get(role, {}).get("status") == "missing_input"
        ]
        validation_errors.extend(f"missing_required_input:{role}" for role in missing_required)

    blocking_findings = sorted(set(validation_errors))
    status = "blocked" if blocking_findings else "warning" if warnings else "ok"
    if not dataset_reports and strict:
        status = "missing_input"
        blocking_findings.append("no_inputs_provided")
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(blocking_findings or warnings))),
        "generated_at_utc": utc_timestamp(),
        "report_version": REPORT_VERSION,
        "datasets": dataset_reports,
        "global_summary": global_summary(dataset_reports),
        "validation_errors": blocking_findings,
        "warnings": sorted(set(warnings)),
        "blocking_findings": blocking_findings,
        **safe,
    }
    write_json_if_requested(report, report_file)
    return report


def audit_dataset(*, role: str, path: Path, strict: bool) -> dict[str, Any]:
    base = empty_dataset_report(role, path)
    if not path.exists():
        base["status"] = "missing_input"
        base["validation_errors"].append(f"missing_input:{role}:{path}")
        return base
    try:
        frame = read_table(path)
    except Exception as exc:
        base["status"] = "blocked"
        base["validation_errors"].append(f"read_failed:{role}:{exc}")
        return base
    base.update(dataset_metrics(frame, role=role, path=path))
    validation_errors = base["validation_errors"]
    warnings = base["warnings"]
    if frame.empty:
        validation_errors.append(f"empty_dataset:{role}")
    if strict:
        if base["duplicate_order_id_rows"] > 0:
            validation_errors.append(f"duplicate_order_id_rows:{role}")
        if base["missing_time_rows"] > 0 or base["invalid_timestamp_rows"] > 0:
            validation_errors.append(f"critical_timestamp_invalid:{role}")
        if base["missing_price_rows"] > 0:
            validation_errors.append(f"critical_price_missing:{role}")
        if base["invalid_symbol_rows"] > 0:
            validation_errors.append(f"invalid_symbol_rows:{role}")
        if base["invalid_side_rows"] > 0:
            validation_errors.append(f"invalid_side_rows:{role}")
        if base["rows_with_infinite_values"] > 0:
            validation_errors.append(f"infinite_values:{role}")
    if base["temporal_gaps_count"] > 0:
        warnings.append(f"temporal_gaps:{role}")
    if base["rows_with_nan"] > 0:
        warnings.append(f"nan_rows:{role}")
    base["status"] = "blocked" if validation_errors else "warning" if warnings else "ok"
    base["validation_errors"] = sorted(set(validation_errors))
    base["warnings"] = sorted(set(warnings))
    return base


def dataset_metrics(frame: pd.DataFrame, *, role: str, path: Path) -> dict[str, Any]:
    timestamp_column = first_existing(frame, ("timestamp", "open_ts", "open_time_utc", "opened_at", "horario_abertura", "date"))
    close_timestamp_column = first_existing(frame, ("close_time_utc", "closed_at", "horario_fechamento"))
    price_column = first_existing(frame, ("price", "open", "close", "preco_abertura", "preco_fechamento", "entry_price"))
    volume_column = first_existing(frame, ("volume", "amount", "stake_amount", "qty"))
    order_id_column = first_existing(frame, ("order_id", "trade_id", "client_order_id"))
    symbol_column = first_existing(frame, ("symbol", "pair", "moeda"))
    side_column = first_existing(frame, ("side", "fechar_side", "direction"))
    rows = int(len(frame))
    timestamps = parse_timestamps(frame[timestamp_column]) if timestamp_column else pd.Series([], dtype="datetime64[ns, UTC]")
    temporal = temporal_gap_summary(timestamps)
    rows_with_nan = int(frame.isna().any(axis=1).sum()) if rows else 0
    numeric = frame.select_dtypes(include=[np.number])
    inf_rows = int(np.isinf(numeric.to_numpy(dtype=float, copy=False)).any(axis=1).sum()) if not numeric.empty else 0
    enriched_rows = int(frame.get("enriched", pd.Series([False] * rows)).map(normalize_bool).sum()) if "enriched" in frame.columns else int(rows if role in {"trade_enriched", "training_dataset", "microbatch"} and rows else 0)
    excluded_reasons = exclusion_reasons(frame)
    return {
        "role": role,
        "path": str(path),
        "status": "ok",
        "rows": rows,
        "columns": int(len(frame.columns)),
        "rows_total": rows,
        "columns_total": int(len(frame.columns)),
        "duplicate_rows": int(frame.duplicated().sum()) if rows else 0,
        "duplicate_order_id_rows": duplicate_order_ids(frame, order_id_column),
        "missing_required_fields": missing_required_fields(frame, timestamp_column, price_column, symbol_column, side_column),
        "missing_price_rows": missing_values(frame, price_column),
        "missing_time_rows": missing_values(frame, timestamp_column),
        "invalid_timestamp_rows": invalid_timestamps(frame, timestamp_column),
        "invalid_symbol_rows": invalid_symbols(frame, symbol_column),
        "invalid_side_rows": invalid_sides(frame, side_column),
        "negative_or_zero_volume_rows": negative_or_zero(frame, volume_column),
        "rows_with_nan": rows_with_nan,
        "rows_with_infinite_values": inf_rows,
        "min_timestamp": temporal["min_timestamp"],
        "max_timestamp": temporal["max_timestamp"],
        "temporal_gaps_count": temporal["temporal_gaps_count"],
        "largest_temporal_gap_seconds": temporal["largest_temporal_gap_seconds"],
        "enriched_rows": enriched_rows,
        "unenriched_rows": int(max(rows - enriched_rows, 0)),
        "rows_without_open_candle": missing_values(frame, "open_candle_timestamp") if "open_candle_timestamp" in frame.columns else 0,
        "rows_without_close_candle": missing_values(frame, "close_candle_timestamp") if "close_candle_timestamp" in frame.columns else 0,
        "excluded_rows": int(sum(excluded_reasons.values())),
        "exclusion_reasons": excluded_reasons,
        "validation_errors": [],
        "warnings": [],
        "timestamp_column": timestamp_column,
        "close_timestamp_column": close_timestamp_column,
        "price_column": price_column,
        "symbol_column": symbol_column,
        "side_column": side_column,
    }


def empty_dataset_report(role: str, path: Path) -> dict[str, Any]:
    return {
        "role": role,
        "path": str(path),
        "status": "missing_input",
        "rows": 0,
        "columns": 0,
        "rows_total": 0,
        "columns_total": 0,
        "duplicate_rows": 0,
        "duplicate_order_id_rows": 0,
        "missing_required_fields": [],
        "missing_price_rows": 0,
        "missing_time_rows": 0,
        "invalid_timestamp_rows": 0,
        "invalid_symbol_rows": 0,
        "invalid_side_rows": 0,
        "negative_or_zero_volume_rows": 0,
        "rows_with_nan": 0,
        "rows_with_infinite_values": 0,
        "min_timestamp": None,
        "max_timestamp": None,
        "temporal_gaps_count": 0,
        "largest_temporal_gap_seconds": 0.0,
        "enriched_rows": 0,
        "unenriched_rows": 0,
        "rows_without_open_candle": 0,
        "rows_without_close_candle": 0,
        "excluded_rows": 0,
        "exclusion_reasons": {},
        "validation_errors": [],
        "warnings": [],
    }


def global_summary(datasets: dict[str, Any]) -> dict[str, Any]:
    return {
        "datasets_count": int(len(datasets)),
        "rows_total": int(sum(item.get("rows_total", 0) for item in datasets.values())),
        "blocked_datasets": sorted(role for role, item in datasets.items() if item.get("status") == "blocked"),
        "warning_datasets": sorted(role for role, item in datasets.items() if item.get("status") == "warning"),
    }


def first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def missing_required_fields(
    frame: pd.DataFrame,
    timestamp_column: str | None,
    price_column: str | None,
    symbol_column: str | None,
    side_column: str | None,
) -> list[str]:
    missing = []
    if timestamp_column is None:
        missing.append("timestamp")
    if price_column is None:
        missing.append("price")
    if symbol_column is None:
        missing.append("symbol")
    if side_column is None:
        missing.append("side")
    return missing


def missing_values(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return int(len(frame))
    return int(frame[column].isna().sum())


def invalid_timestamps(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return int(len(frame))
    return int(pd.to_datetime(frame[column], utc=True, errors="coerce").isna().sum())


def invalid_symbols(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return int(len(frame))
    values = frame[column].fillna("").astype(str).str.strip()
    return int((values.eq("") | ~values.str.match(r"^[A-Za-z0-9/_:-]+$")).sum())


def invalid_sides(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return int(len(frame))
    values = frame[column].fillna("").astype(str).str.lower().str.strip()
    return int((values.eq("") | ~values.isin(VALID_SIDES)).sum())


def negative_or_zero(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return 0
    values = pd.to_numeric(frame[column], errors="coerce")
    return int((values <= 0).fillna(False).sum())


def duplicate_order_ids(frame: pd.DataFrame, column: str | None) -> int:
    if not column or column not in frame.columns:
        return 0
    values = frame[column].dropna().astype(str).str.strip()
    values = values.loc[values.ne("")]
    return int(values.duplicated(keep="first").sum())


def parse_timestamps(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce").dropna().sort_values()


def temporal_gap_summary(timestamps: pd.Series) -> dict[str, Any]:
    if timestamps.empty:
        return {"min_timestamp": None, "max_timestamp": None, "temporal_gaps_count": 0, "largest_temporal_gap_seconds": 0.0}
    diffs = timestamps.diff().dropna().dt.total_seconds()
    positive = diffs.loc[diffs > 0]
    expected_interval = float(positive.min()) if not positive.empty else 0.0
    threshold = expected_interval * 3 if expected_interval > 0 else 0.0
    gaps = positive.loc[positive > threshold] if threshold > 0 else pd.Series([], dtype=float)
    return {
        "min_timestamp": timestamps.min().isoformat().replace("+00:00", "Z"),
        "max_timestamp": timestamps.max().isoformat().replace("+00:00", "Z"),
        "temporal_gaps_count": int(len(gaps)),
        "largest_temporal_gap_seconds": float(positive.max()) if not positive.empty else 0.0,
    }


def exclusion_reasons(frame: pd.DataFrame) -> dict[str, int]:
    for column in ("exclusion_reason", "excluded_reason", "reason"):
        if column in frame.columns:
            values = frame[column].dropna().astype(str)
            values = values.loc[values.str.strip().ne("")]
            return {str(key): int(value) for key, value in values.value_counts().sort_index().items()}
    if "excluded" in frame.columns:
        count = int(frame["excluded"].map(normalize_bool).sum())
        return {"excluded_flag": count} if count else {}
    return {}


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "ok"}
