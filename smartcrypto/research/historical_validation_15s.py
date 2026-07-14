"""Canonical 15s full historical validation for SmartCrypto research.

This module is paper/shadow/research only. It reads local files, computes
statistical evidence, and writes only optional JSON reports under data/reports.
It never calls exchange APIs, never submits orders, never changes risk settings,
and never trains or promotes models.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)

from smartcrypto.research.execution_costs import CostModel, apply_execution_costs
from smartcrypto.research.monte_carlo_risk import MonteCarloConfig, run_monte_carlo
from smartcrypto.research.walkforward_validation import WalkForwardConfig, run_walkforward_validation


SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "runtime_mode": "paper",
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_training_dataset": False,
    "research_only": True,
}


READ_EXCEPTIONS = (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    ImportError,
    RuntimeError,
    UnicodeError,
    EOFError,
    pd.errors.ParserError,
    pd.errors.EmptyDataError,
)

TIMESTAMP_CANDIDATES = (
    "timestamp",
    "ts",
    "date",
    "datetime",
    "open_time",
    "open_time_utc",
    "close_time",
    "close_time_utc",
    "open_ts",
    "close_ts",
    "horario_abertura",
    "horario_fechamento",
    "horario_transacao",
    "transaction_time_utc",
    "time",
)
SYMBOL_CANDIDATES = ("symbol", "symbol_norm", "symbol_x", "symbol_y", "pair", "moeda")
PNL_CANDIDATES = (
    "pnl",
    "net_pnl_usdt",
    "pnl_usdt",
    "reported_pnl_usdt",
    "pnl_fechado",
    "profit_abs",
    "close_profit_abs",
    "realized_profit",
    "profit_usdt",
)
TRADE_TIMESTAMP_CANDIDATES = (
    "open_ts",
    "open_time_utc",
    "close_ts",
    "close_time_utc",
    "horario_abertura",
    "horario_fechamento",
    "horario_transacao",
    "transaction_time_utc",
    "timestamp",
    "date",
    "open_date",
    "close_date",
)

CANONICAL_15S_RELATIVE_ROOT = Path("data/raw/binance_futures_klines_15s")
REQUIRED_CANDLE_COLUMNS = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
EXPECTED_15S_ROWS_PER_DAY = 24 * 60 * 4
CANONICAL_FILE_RE = re.compile(r"(?P<symbol>[A-Z0-9]+)_15s_(?P<date>\d{8})\.(?:parquet|csv)$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceSummary:
    path: str
    exists: bool
    source_kind: str
    rows: int = 0
    columns: int = 0
    file_date: str | None = None
    timestamp_column: str | None = None
    min_timestamp_utc: str | None = None
    max_timestamp_utc: str | None = None
    median_interval_seconds: float | None = None
    max_interval_seconds: float | None = None
    duplicate_timestamp_count: int = 0
    gap_count: int = 0
    expected_rows: int = EXPECTED_15S_ROWS_PER_DAY
    full_day_15s_coverage: bool = False
    symbols: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationInputs:
    project_root: Path
    from_date: str
    timeframe: str = "15s"
    required_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    min_trades: int = 3_000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [to_jsonable(item) for item in value.tolist()]
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True))


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported_table_format:{path}")


def resolve_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    normalized = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return None


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    raw = frame[column]
    if raw.dtype == object or pd.api.types.is_string_dtype(raw):
        values = raw.astype(str).str.strip().str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        return pd.to_numeric(values, errors="coerce")
    return pd.to_numeric(raw, errors="coerce")


def normalize_symbol(value: Any) -> str:
    return str(value).upper().replace("/", "").replace(":USDT", "USDT").replace("_", "").strip()


def _canonical_root(project_root: Path) -> Path:
    return project_root / CANONICAL_15S_RELATIVE_ROOT


def _match_canonical_filename(path: Path) -> re.Match[str] | None:
    return CANONICAL_FILE_RE.match(path.name)


def discover_candle_source_paths(project_root: Path, timeframe: str = "15s") -> list[Path]:
    if timeframe != "15s":
        return []
    root = _canonical_root(project_root)
    if not root.exists():
        return []
    paths = sorted(path.resolve() for path in root.rglob("*_15s_*.parquet") if path.is_file())
    paths.extend(sorted(path.resolve() for path in root.rglob("*_15s_*.csv") if path.is_file()))
    return [Path(path) for path in sorted(set(paths))]


def classify_source(path: Path, project_root: Path | None = None) -> str:
    normalized = str(path).replace("\\", "/").lower()
    canonical_marker = "/data/raw/binance_futures_klines_15s/"
    if canonical_marker in normalized and _match_canonical_filename(path):
        return "canonical_binance_usdm_aggtrades_15s"
    if "/reports/" in normalized or "anomal" in normalized or "rejected" in normalized or "invalid" in normalized:
        return "noncanonical_audit_or_rejected_artifact"
    if "bitradex" in normalized:
        return "noncanonical_bitradex_15s"
    return "noncanonical_candidate_15s"


def _expected_day_bounds(file_date: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(file_date, tz="UTC")
    end = start + pd.Timedelta(hours=23, minutes=59, seconds=45)
    return start, end


def summarize_source(path: Path, *, project_root: Path | None = None, expected_interval_seconds: int = 15) -> SourceSummary:
    source_kind = classify_source(path, project_root)
    if not path.exists():
        return SourceSummary(path=str(path), exists=False, source_kind=source_kind, validation_errors=("missing_source",))

    match = _match_canonical_filename(path)
    file_date: str | None = None
    file_symbol: str | None = None
    if match:
        file_date = datetime.strptime(match.group("date"), "%Y%m%d").date().isoformat()
        file_symbol = normalize_symbol(match.group("symbol"))

    try:
        frame = read_table(path)
    except READ_EXCEPTIONS as exc:
        return SourceSummary(
            path=str(path),
            exists=True,
            source_kind=source_kind,
            file_date=file_date,
            validation_errors=(f"source_not_readable:{type(exc).__name__}:{exc}",),
        )

    warnings: list[str] = []
    errors: list[str] = []
    if source_kind != "canonical_binance_usdm_aggtrades_15s":
        errors.append("source_is_not_canonical_binance_15s")

    missing_columns = [column for column in REQUIRED_CANDLE_COLUMNS if column not in frame.columns]
    if missing_columns:
        errors.append(f"missing_required_candle_columns:{missing_columns}")

    timestamp_column = "timestamp" if "timestamp" in frame.columns else resolve_column(frame.columns, TIMESTAMP_CANDIDATES)
    symbol_column = "symbol" if "symbol" in frame.columns else resolve_column(frame.columns, SYMBOL_CANDIDATES)

    min_timestamp: str | None = None
    max_timestamp: str | None = None
    median_interval: float | None = None
    max_interval: float | None = None
    gap_count = 0
    duplicate_count = 0
    full_day = False

    if timestamp_column:
        timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dropna().sort_values()
        if not timestamps.empty:
            duplicate_count = int(timestamps.duplicated().sum())
            unique_ts = timestamps.drop_duplicates()
            min_ts = unique_ts.min()
            max_ts = unique_ts.max()
            min_timestamp = min_ts.isoformat().replace("+00:00", "Z")
            max_timestamp = max_ts.isoformat().replace("+00:00", "Z")
            deltas = unique_ts.diff().dropna().dt.total_seconds()
            if not deltas.empty:
                median_interval = float(deltas.median())
                max_interval = float(deltas.max())
                gap_count = int((deltas > float(expected_interval_seconds)).sum())
                if median_interval != float(expected_interval_seconds):
                    errors.append(f"median_interval_not_15s:{median_interval}")
                if max_interval != float(expected_interval_seconds):
                    errors.append(f"max_interval_not_15s:{max_interval}")
            if duplicate_count:
                errors.append(f"duplicate_timestamps:{duplicate_count}")
            if file_date:
                expected_start, expected_end = _expected_day_bounds(file_date)
                full_day = bool(len(frame) == EXPECTED_15S_ROWS_PER_DAY and min_ts == expected_start and max_ts == expected_end)
                if len(frame) != EXPECTED_15S_ROWS_PER_DAY:
                    errors.append(f"unexpected_15s_row_count:{len(frame)}:{EXPECTED_15S_ROWS_PER_DAY}")
                if min_ts != expected_start:
                    errors.append(f"unexpected_day_start:{min_ts.isoformat()}:{expected_start.isoformat()}")
                if max_ts != expected_end:
                    errors.append(f"unexpected_day_end:{max_ts.isoformat()}:{expected_end.isoformat()}")
        else:
            errors.append(f"timestamp_column_unparseable:{timestamp_column}")
    else:
        errors.append("timestamp_column_not_found")

    symbols: tuple[str, ...] = ()
    if symbol_column:
        symbols = tuple(sorted({normalize_symbol(value) for value in frame[symbol_column].dropna().unique()}))
    elif file_symbol:
        symbols = (file_symbol,)
    if file_symbol and symbols and file_symbol not in symbols:
        errors.append(f"filename_symbol_not_in_data:{file_symbol}:{symbols}")

    return SourceSummary(
        path=str(path),
        exists=True,
        source_kind=source_kind,
        rows=int(len(frame)),
        columns=int(len(frame.columns)),
        file_date=file_date,
        timestamp_column=timestamp_column,
        min_timestamp_utc=min_timestamp,
        max_timestamp_utc=max_timestamp,
        median_interval_seconds=median_interval,
        max_interval_seconds=max_interval,
        duplicate_timestamp_count=int(duplicate_count),
        gap_count=int(gap_count),
        expected_rows=EXPECTED_15S_ROWS_PER_DAY,
        full_day_15s_coverage=full_day,
        symbols=symbols,
        validation_errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _date_range_inclusive(start_date: str, end_date: str) -> list[str]:
    start = pd.Timestamp(start_date, tz="UTC").date()
    end = pd.Timestamp(end_date, tz="UTC").date()
    if end < start:
        return []
    return [day.date().isoformat() for day in pd.date_range(start=start, end=end, freq="D", tz="UTC")]


def audit_15s_candle_coverage(inputs: ValidationInputs) -> dict[str, Any]:
    if inputs.timeframe != "15s":
        return {
            **SAFETY_FLAGS,
            "schema_version": "full_historical_validation_15s_coverage_v2",
            "status": "blocked",
            "reason": "unsupported_timeframe_for_canonical_15s_audit",
            "timeframe": inputs.timeframe,
            "validation_errors": [f"unsupported_timeframe:{inputs.timeframe}"],
            "warnings": [],
            "generated_at_utc": utc_now_iso(),
        }

    paths = discover_candle_source_paths(inputs.project_root, inputs.timeframe)
    summaries = [summarize_source(path, project_root=inputs.project_root, expected_interval_seconds=15) for path in paths]
    canonical_ok = [
        summary
        for summary in summaries
        if summary.source_kind == "canonical_binance_usdm_aggtrades_15s" and not summary.validation_errors and summary.file_date
    ]

    from_date = pd.Timestamp(inputs.from_date, tz="UTC").date().isoformat()
    required_symbols = tuple(normalize_symbol(symbol) for symbol in inputs.required_symbols)
    ok_dates_by_symbol: dict[str, set[str]] = {symbol: set() for symbol in required_symbols}
    source_counts_by_symbol: dict[str, int] = {symbol: 0 for symbol in required_symbols}

    for summary in canonical_ok:
        summary_symbols = set(summary.symbols)
        for symbol in required_symbols:
            if symbol in summary_symbols or symbol.lower() in summary.path.lower():
                source_counts_by_symbol[symbol] += 1
                if summary.file_date:
                    ok_dates_by_symbol[symbol].add(summary.file_date)

    all_ok_dates = sorted({date for dates in ok_dates_by_symbol.values() for date in dates if date >= from_date})
    coverage_through = max(all_ok_dates) if all_ok_dates else None
    expected_dates = _date_range_inclusive(from_date, coverage_through) if coverage_through else []

    missing_by_symbol: dict[str, list[str]] = {}
    for symbol in required_symbols:
        missing = [date for date in expected_dates if date not in ok_dates_by_symbol[symbol]]
        if missing:
            missing_by_symbol[symbol] = missing

    symbol_coverage: dict[str, dict[str, Any]] = {}
    for symbol in required_symbols:
        dates = sorted(date for date in ok_dates_by_symbol[symbol] if date >= from_date)
        symbol_coverage[symbol] = {
            "canonical_source_count": int(source_counts_by_symbol[symbol]),
            "covered_day_count": int(len(dates)),
            "first_covered_date": dates[0] if dates else None,
            "last_covered_date": dates[-1] if dates else None,
            "missing_day_count": int(len(missing_by_symbol.get(symbol, []))),
            "missing_days_sample": missing_by_symbol.get(symbol, [])[:20],
        }

    invalid_sources = [summary for summary in summaries if summary.validation_errors]
    validation_errors: list[str] = []
    warnings: list[str] = []
    if not paths:
        validation_errors.append("canonical_15s_root_has_no_files")
    if not canonical_ok:
        validation_errors.append("no_valid_canonical_binance_15s_files")
    if not coverage_through:
        validation_errors.append("no_canonical_15s_coverage_on_or_after_from_date")
    for symbol in required_symbols:
        if not ok_dates_by_symbol[symbol]:
            validation_errors.append(f"missing_required_symbol:{symbol}")
        if symbol in missing_by_symbol:
            validation_errors.append(f"missing_continuous_days:{symbol}:{len(missing_by_symbol[symbol])}")
    if invalid_sources:
        warnings.append(f"invalid_canonical_source_count:{len(invalid_sources)}")

    status = "blocked" if validation_errors else "ok"
    reason = "canonical_15s_coverage_validation_errors" if validation_errors else "canonical_binance_15s_coverage_established"

    return {
        **SAFETY_FLAGS,
        "schema_version": "full_historical_validation_15s_coverage_v2",
        "status": status,
        "reason": reason,
        "project_root": str(inputs.project_root),
        "canonical_root": str(_canonical_root(inputs.project_root)),
        "from_date": inputs.from_date,
        "timeframe": inputs.timeframe,
        "required_symbols": list(required_symbols),
        "expected_rows_per_symbol_day": EXPECTED_15S_ROWS_PER_DAY,
        "canonical_source_count": int(len(summaries)),
        "valid_canonical_source_count": int(len(canonical_ok)),
        "invalid_canonical_source_count": int(len(invalid_sources)),
        "coverage_start_date": from_date,
        "coverage_through_date": coverage_through,
        "expected_continuous_day_count": int(len(expected_dates)),
        "expected_file_count_through_coverage": int(len(expected_dates) * len(required_symbols)),
        "expected_row_count_through_coverage": int(len(expected_dates) * len(required_symbols) * EXPECTED_15S_ROWS_PER_DAY),
        "symbol_coverage": symbol_coverage,
        "sources": [asdict(summary) for summary in summaries],
        "validation_errors": validation_errors,
        "warnings": warnings,
        "generated_at_utc": utc_now_iso(),
    }


def discover_trade_source(project_root: Path) -> Path | None:
    candidates = [
        project_root / "data" / "features" / "trade_enriched.parquet",
        project_root / "data" / "features" / "trade_enriched.csv",
        project_root / "data" / "features" / "training_dataset_quality_gated_binance_1m.parquet",
        project_root / "data" / "features" / "training_dataset_quality_gated_binance_1m_plus_15s_shadow.parquet",
        project_root / "data" / "features" / "training_dataset.parquet",
    ]
    return next((path for path in candidates if path.exists()), None)


def load_trade_frame(project_root: Path) -> tuple[pd.DataFrame | None, Path | None, str | None]:
    source = discover_trade_source(project_root)
    if source is not None:
        try:
            return read_table(source), source, None
        except READ_EXCEPTIONS as exc:
            return None, source, f"trade_source_not_readable:{type(exc).__name__}:{exc}"

    bundle = read_trader_master_readonly(
        project_root=project_root,
        trader_master_path=DEFAULT_MASTER,
    )
    legacy_source = project_root / DEFAULT_MASTER
    if bundle.report.get("status") != "ok":
        return (
            None,
            legacy_source,
            f"legacy_master_read_blocked:{bundle.report.get('reason', 'unknown')}",
        )
    return pd.DataFrame.from_records(bundle.source_rows), legacy_source, None


def audit_trade_base(frame: pd.DataFrame | None, source: Path | None, *, min_trades: int) -> dict[str, Any]:
    if frame is None:
        return {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "trade_frame_unavailable",
            "source": str(source) if source else None,
        }

    timestamp_column = resolve_column(frame.columns, TRADE_TIMESTAMP_CANDIDATES)
    pnl_column = resolve_column(frame.columns, PNL_CANDIDATES)
    symbol_column = resolve_column(frame.columns, SYMBOL_CANDIDATES)
    warnings: list[str] = []
    errors: list[str] = []

    if len(frame) < int(min_trades):
        errors.append(f"insufficient_trades:{len(frame)}:{min_trades}")
    if not timestamp_column:
        errors.append("trade_timestamp_column_not_found")
    if not pnl_column:
        errors.append("pnl_column_not_found")

    usable_pnl_rows = 0
    net_pnl = None
    if pnl_column:
        pnl = numeric_series(frame, pnl_column).dropna()
        usable_pnl_rows = int(len(pnl))
        if usable_pnl_rows == 0:
            errors.append(f"pnl_column_unparseable:{pnl_column}")
        else:
            net_pnl = float(pnl.sum())

    duplicate_key_count = 0
    for key in ("trade_id", "order_id", "internal_order_id", "_dedup_key"):
        if key in frame.columns:
            duplicate_key_count = int(frame[key].dropna().duplicated().sum())
            if duplicate_key_count:
                warnings.append(f"duplicate_{key}:{duplicate_key_count}")
            break

    symbols: list[str] = []
    if symbol_column:
        symbols = sorted({normalize_symbol(value) for value in frame[symbol_column].dropna().unique()})

    min_ts: str | None = None
    max_ts: str | None = None
    usable_timestamp_rows = 0
    if timestamp_column:
        timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dropna()
        usable_timestamp_rows = int(len(timestamps))
        if timestamps.empty:
            errors.append(f"trade_timestamp_column_unparseable:{timestamp_column}")
        else:
            min_ts = timestamps.min().isoformat().replace("+00:00", "Z")
            max_ts = timestamps.max().isoformat().replace("+00:00", "Z")

    return {
        **SAFETY_FLAGS,
        "status": "blocked" if errors else "ok",
        "reason": "trade_base_validation_errors" if errors else "trade_base_ok",
        "source": str(source) if source else None,
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "timestamp_column": timestamp_column,
        "usable_timestamp_rows": int(usable_timestamp_rows),
        "pnl_column": pnl_column,
        "usable_pnl_rows": int(usable_pnl_rows),
        "net_pnl_usdt": net_pnl,
        "symbol_column": symbol_column,
        "symbols": symbols,
        "min_timestamp_utc": min_ts,
        "max_timestamp_utc": max_ts,
        "duplicate_key_count": int(duplicate_key_count),
        "validation_errors": errors,
        "warnings": warnings,
    }


def build_readiness_status(reports: dict[str, Any]) -> tuple[str, str]:
    blocking = [name for name, report in reports.items() if isinstance(report, dict) and report.get("status") == "blocked"]
    if blocking:
        return "blocked", f"blocked_components:{blocking}"
    warnings = [name for name, report in reports.items() if isinstance(report, dict) and report.get("status") == "warning"]
    if warnings:
        return "warning", f"warning_components:{warnings}"
    return "ok", "all_research_components_ok_live_and_canary_still_blocked"


def run_full_historical_validation(
    *,
    project_root: Path,
    from_date: str,
    timeframe: str = "15s",
    no_write: bool = True,
    min_trades: int = 3_000,
    iterations: int = 2_000,
) -> dict[str, Any]:
    inputs = ValidationInputs(project_root=project_root, from_date=from_date, timeframe=timeframe, min_trades=min_trades)
    coverage = audit_15s_candle_coverage(inputs)
    trades, trade_source, trade_error = load_trade_frame(project_root)
    trade_audit = audit_trade_base(trades, trade_source, min_trades=min_trades)
    if trade_error:
        trade_audit = {**trade_audit, "status": "blocked", "reason": trade_error}

    reports: dict[str, Any] = {
        "candle_coverage": coverage,
        "trade_base": trade_audit,
    }

    if trades is not None and trade_audit.get("pnl_column") and trade_audit.get("status") == "ok":
        pnl_column = str(trade_audit["pnl_column"])
        costed, cost_report = apply_execution_costs(trades, pnl_column=pnl_column, cost_model=CostModel())
        reports["execution_costs"] = cost_report
        reports["monte_carlo_before_costs"] = run_monte_carlo(
            costed,
            pnl_column="validation_before_costs_pnl_usdt",
            config=MonteCarloConfig(iterations=int(iterations), seed=42, block_size=20),
        )
        reports["monte_carlo_after_costs"] = run_monte_carlo(
            costed,
            pnl_column="validation_after_costs_pnl_usdt",
            config=MonteCarloConfig(iterations=int(iterations), seed=43, block_size=20),
        )
        timestamp_column = trade_audit.get("timestamp_column")
        if timestamp_column:
            dynamic_train_rows = min(600, max(30, int(len(costed) * 0.35)))
            dynamic_test_rows = min(250, max(20, int(len(costed) * 0.08)))
            reports["walkforward_after_costs"] = run_walkforward_validation(
                costed,
                timestamp_column=str(timestamp_column),
                pnl_column="validation_after_costs_pnl_usdt",
                config=WalkForwardConfig(
                    min_train_rows=dynamic_train_rows,
                    test_rows=dynamic_test_rows,
                    embargo_rows=5,
                    max_folds=24,
                    mode="expanding",
                ),
            )
        else:
            reports["walkforward_after_costs"] = {
                **SAFETY_FLAGS,
                "status": "blocked",
                "reason": "timestamp_column_missing_for_walkforward",
            }
    else:
        reports["execution_costs"] = {**SAFETY_FLAGS, "status": "blocked", "reason": "pnl_unavailable_for_cost_simulation"}
        reports["monte_carlo_before_costs"] = {**SAFETY_FLAGS, "status": "blocked", "reason": "pnl_unavailable_for_monte_carlo"}
        reports["monte_carlo_after_costs"] = {**SAFETY_FLAGS, "status": "blocked", "reason": "pnl_unavailable_for_monte_carlo"}
        reports["walkforward_after_costs"] = {**SAFETY_FLAGS, "status": "blocked", "reason": "pnl_unavailable_for_walkforward"}

    reports["mae_mfe"] = {
        **SAFETY_FLAGS,
        "status": "ok" if coverage.get("status") == "ok" and trade_audit.get("status") == "ok" else "blocked",
        "reason": "mae_mfe_columns_available_in_trade_enriched_or_reconstructable_from_canonical_15s"
        if coverage.get("status") == "ok" and trade_audit.get("status") == "ok"
        else "requires_valid_trade_base_and_canonical_15s_candles",
        "existing_mfe_column": "mfe_pct" if trades is not None and "mfe_pct" in trades.columns else None,
        "existing_mae_column": "mae_pct" if trades is not None and "mae_pct" in trades.columns else None,
    }

    research_status, research_reason = build_readiness_status(reports)
    payload: dict[str, Any] = {
        **SAFETY_FLAGS,
        "schema_version": "full_historical_validation_15s_core_v2",
        "status": research_status,
        "reason": research_reason,
        "project_root": str(project_root),
        "from_date": from_date,
        "timeframe": timeframe,
        "min_trades": int(min_trades),
        "iterations": int(iterations),
        "no_write": bool(no_write),
        "generated_at_utc": utc_now_iso(),
        "reports": reports,
        "readiness": {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "research_evidence_only_live_and_canary_remain_blocked",
            "live_release_allowed": False,
            "canary_release_allowed": False,
        },
    }

    if not no_write:
        output_dir = project_root / "data" / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "full_historical_validation_15s_core_v2.json"
        output_path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        payload["output_path"] = str(output_path)
        payload["write_performed"] = True
    else:
        payload["write_performed"] = False

    return payload
