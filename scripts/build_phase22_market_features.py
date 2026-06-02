from __future__ import annotations
import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

BASE_COLS = ["symbol", "pair", "tf", "ts", "ts_ms", "open", "high", "low", "close", "volume"]
PRICE_COLS = ["open", "high", "low", "close", "volume"]
TIMESTAMP_ALIASES = ("timestamp", "ts", "ts_ms", "open_time")
FEATURES_REPORT_PATH = Path("data/reports/phase22_features_report.json")
DATA_QUALITY_REPORT_PATH = Path("data/reports/phase22_data_quality_report.json")
MAIN_OVERWRITE_BLOCK_REASON = "main_features_backup_required_before_overwrite"


class Phase22FeatureBuildError(ValueError):
    pass


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def raw_files(raw_dir: Path, symbols: list[str], interval: str) -> list[Path]:
    files: list[Path] = []
    for symbol in symbols:
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.parquet")))
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.csv")))
    return files


def duplicate_column_names(frame: pd.DataFrame) -> list[str]:
    counts: dict[str, int] = {}
    duplicates: list[str] = []
    for column in frame.columns:
        name = str(column)
        counts[name] = counts.get(name, 0) + 1
        if counts[name] == 2:
            duplicates.append(name)
    return sorted(duplicates)


def collapse_duplicate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if not frame.columns.has_duplicates:
        return frame.copy()

    collapsed: dict[str, pd.Series] = {}
    ordered_names = list(dict.fromkeys(str(col) for col in frame.columns))
    for name in ordered_names:
        selection = frame.loc[:, frame.columns.astype(str) == name]
        if selection.shape[1] == 1:
            collapsed[name] = selection.iloc[:, 0]
        else:
            collapsed[name] = selection.bfill(axis=1).iloc[:, 0]
    return pd.DataFrame(collapsed, index=frame.index)


def series_1d(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise Phase22FeatureBuildError(f"missing_required_column:{column}")
    value = frame.loc[:, column]
    if isinstance(value, pd.DataFrame):
        if value.shape[1] == 0:
            raise Phase22FeatureBuildError(f"missing_required_column:{column}")
        value = value.bfill(axis=1).iloc[:, 0]
    if not isinstance(value, pd.Series):
        raise Phase22FeatureBuildError(f"column_not_1d:{column}")
    return value


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = series_1d(frame, column)
    if isinstance(value, pd.DataFrame):
        raise Phase22FeatureBuildError(f"column_not_1d:{column}")
    return pd.to_numeric(value, errors="coerce")


def validate_required_columns(frame: pd.DataFrame) -> None:
    missing = [col for col in ["symbol", *PRICE_COLS] if col not in frame.columns]
    if missing:
        raise Phase22FeatureBuildError(f"missing_required_columns:{missing}")
    if not any(col in frame.columns for col in TIMESTAMP_ALIASES):
        raise Phase22FeatureBuildError(
            "missing_required_timestamp_column:timestamp|ts|ts_ms|open_time"
        )


def canonicalize_timestamp(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if "ts_ms" in frame.columns:
        ts_ms = numeric_series(frame, "ts_ms")
        frame["ts_ms"] = ts_ms
        frame["ts"] = pd.to_datetime(ts_ms, unit="ms", utc=True, errors="coerce")
        return frame

    if "open_time" in frame.columns:
        ts_ms = numeric_series(frame, "open_time")
        frame["ts_ms"] = ts_ms
        frame["ts"] = pd.to_datetime(ts_ms, unit="ms", utc=True, errors="coerce")
        return frame

    timestamp_col = "ts" if "ts" in frame.columns else "timestamp"
    parsed = pd.to_datetime(series_1d(frame, timestamp_col), utc=True, errors="coerce")
    frame["ts"] = parsed
    parsed_ns = parsed.astype("int64")
    frame["ts_ms"] = pd.Series(
        np.where(parsed.notna(), parsed_ns // 1_000_000, np.nan),
        index=frame.index,
    )
    return frame


def validate_normalized(frame: pd.DataFrame) -> None:
    required = ["ts", "ts_ms", "symbol", *PRICE_COLS]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise Phase22FeatureBuildError(f"normalized_missing_columns:{missing}")

    invalid_counts: dict[str, int] = {}
    for col in ["ts", "ts_ms", *PRICE_COLS]:
        count = int(frame[col].isna().sum())
        if count:
            invalid_counts[col] = count
    if invalid_counts and len(frame) == sum(invalid_counts.values()):
        raise Phase22FeatureBuildError(f"all_rows_invalid_after_normalization:{invalid_counts}")
    if frame.empty:
        raise Phase22FeatureBuildError("no_rows_after_normalization")

    ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    if ts.isna().all():
        raise Phase22FeatureBuildError("all_timestamps_invalid_after_normalization")
    if getattr(ts.dt, "tz", None) is None:
        raise Phase22FeatureBuildError("timestamp_timezone_not_utc")


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def build_group_features(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("ts_ms").copy()
    close = group["close"]
    high = group["high"]
    low = group["low"]
    open_ = group["open"]
    volume = group["volume"]

    for period in [1, 3, 5, 10, 15, 30]:
        group[f"ret_{period}"] = close.pct_change(period)

    group["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    group["ema_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    group["ema_200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    group["dist_ema20"] = close / group["ema_20"] - 1
    group["dist_ema50"] = close / group["ema_50"] - 1
    group["dist_ema200"] = close / group["ema_200"] - 1
    group["rsi_14"] = rsi(close, 14)

    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    group["macd_line"] = ema_12 - ema_26
    group["macd_signal"] = (
        group["macd_line"].ewm(span=9, adjust=False, min_periods=9).mean()
    )
    group["macd_hist"] = group["macd_line"] - group["macd_signal"]

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    group["atr_14"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    group["atr_pct_14"] = group["atr_14"] / close.replace(0, np.nan)
    group["vol_30"] = group["ret_1"].rolling(30, min_periods=10).std()
    group["vol_120"] = group["ret_1"].rolling(120, min_periods=30).std()
    group["volume_mean_30"] = volume.rolling(30, min_periods=10).mean()
    group["volume_mean_120"] = volume.rolling(120, min_periods=30).mean()
    group["volume_rel_30"] = volume / group["volume_mean_30"].replace(0, np.nan)
    volume_std_30 = volume.rolling(30, min_periods=10).std().replace(0, np.nan)
    group["volume_z_30"] = (volume - group["volume_mean_30"]) / volume_std_30

    group["hl_range"] = (high - low) / close.replace(0, np.nan)
    group["body_range"] = (close - open_).abs() / close.replace(0, np.nan)
    candle_edges = pd.concat([open_, close], axis=1)
    group["upper_wick"] = (
        high - candle_edges.max(axis=1)
    ) / close.replace(0, np.nan)
    group["lower_wick"] = (
        candle_edges.min(axis=1) - low
    ) / close.replace(0, np.nan)
    group["trend_score"] = (
        np.sign(group["dist_ema20"].fillna(0))
        + np.sign(group["dist_ema50"].fillna(0))
        + np.sign(group["dist_ema200"].fillna(0))
    )

    atr_threshold = (
        group["atr_pct_14"]
        .rolling(500, min_periods=100)
        .quantile(0.75)
        .fillna(np.inf)
    )
    high_vol = group["atr_pct_14"].fillna(0) > atr_threshold
    trend_up = group["trend_score"] >= 2
    trend_down = group["trend_score"] <= -2
    regime = pd.Series(
        np.where(trend_up, "trend_up", np.where(trend_down, "trend_down", "range")),
        index=group.index,
    )
    group["market_regime"] = np.where(high_vol, regime + "_high_vol", regime)
    return group

def normalize_raw(raw: pd.DataFrame, interval: str = "1m") -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame):
        raise Phase22FeatureBuildError("raw_input_must_be_dataframe")
    raw = collapse_duplicate_columns(raw)
    validate_required_columns(raw)
    raw = canonicalize_timestamp(raw)
    raw["symbol"] = series_1d(raw, "symbol").astype(str).str.upper().str.strip()
    for col in PRICE_COLS:
        raw[col] = numeric_series(raw, col)
    raw["ts_ms"] = numeric_series(raw, "ts_ms")
    if "tf" not in raw.columns:
        raw["tf"] = interval
    else:
        raw["tf"] = series_1d(raw, "tf").fillna(interval).astype(str)
    if "pair" not in raw.columns:
        raw["pair"] = raw["symbol"].str.replace("USDT", "/USDT:USDT", regex=False)
    else:
        raw["pair"] = series_1d(raw, "pair").fillna("").astype(str)
    normalized = raw[BASE_COLS].dropna(
        subset=["ts", "ts_ms", "symbol", "open", "high", "low", "close", "volume"]
    ).copy()
    validate_normalized(normalized)
    normalized["ts_ms"] = normalized["ts_ms"].astype("int64")
    normalized["ts"] = pd.to_datetime(normalized["ts"], utc=True, errors="coerce")
    return normalized


def resample_to_5m(normalized: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, group in normalized.groupby("symbol", sort=True):
        group = group.sort_values("ts").copy()
        if group.empty:
            continue
        group = group.set_index("ts")
        aggregated = group.resample("5min", label="left", closed="left").agg(
            {
                "pair": "last",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        aggregated = aggregated.dropna(subset=["open", "high", "low", "close"])
        if aggregated.empty:
            continue
        aggregated["symbol"] = symbol
        aggregated["tf"] = "5m"
        aggregated["ts"] = pd.to_datetime(aggregated.index, utc=True)
        aggregated["ts_ms"] = aggregated["ts"].astype("int64") // 1_000_000
        frames.append(aggregated[BASE_COLS])
    if not frames:
        return pd.DataFrame(columns=BASE_COLS)
    return pd.concat(frames, ignore_index=True)


def build_feature_frame(normalized: pd.DataFrame) -> pd.DataFrame:
    one_minute = normalized.copy()
    one_minute["tf"] = "1m"
    five_minute = resample_to_5m(one_minute)
    market = pd.concat([one_minute, five_minute], ignore_index=True)
    market = market.drop_duplicates(subset=["symbol", "tf", "ts_ms"]).sort_values(
        ["symbol", "tf", "ts_ms"]
    )
    feature_frames = [
        build_group_features(group)
        for _, group in market.groupby(["symbol", "tf"], sort=True)
    ]
    features = pd.concat(feature_frames, ignore_index=True)
    features = features.sort_values(["symbol", "tf", "ts_ms"]).reset_index(drop=True)
    duplicate_columns = duplicate_column_names(features)
    if duplicate_columns:
        raise Phase22FeatureBuildError(f"duplicate_columns_in_features:{duplicate_columns}")
    lookahead_columns = [col for col in features.columns if col.startswith("future_")]
    if lookahead_columns:
        raise Phase22FeatureBuildError(f"lookahead_columns_in_features:{lookahead_columns}")
    return features


def frame_summary(frame: pd.DataFrame, path: Path | None = None) -> dict:
    if frame.empty or "ts" not in frame.columns:
        min_ts = None
        max_ts = None
    else:
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dropna()
        min_ts = ts.min().isoformat() if not ts.empty else None
        max_ts = ts.max().isoformat() if not ts.empty else None
    summary = {
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "duplicate_columns": duplicate_column_names(frame),
        "symbols": sorted(frame["symbol"].dropna().astype(str).unique().tolist())
        if "symbol" in frame.columns
        else [],
        "timeframes": sorted(frame["tf"].dropna().astype(str).unique().tolist())
        if "tf" in frame.columns
        else [],
        "min_ts": min_ts,
        "max_ts": max_ts,
    }
    if path is not None:
        summary["path"] = str(path)
    return summary


def controlled_report(
    *,
    status: str,
    reason: str,
    files: list[Path],
    raw: pd.DataFrame | None,
    features: pd.DataFrame | None,
    output_path: Path,
    main_report: dict | None = None,
    sqlite_report: dict | None = None,
    file_reports: list[dict] | None = None,
) -> dict:
    feature_summary = frame_summary(features, output_path) if features is not None else {
        "path": str(output_path),
        "rows": 0,
        "columns": [],
        "duplicate_columns": [],
        "symbols": [],
        "timeframes": [],
        "min_ts": None,
        "max_ts": None,
    }
    raw_summary = frame_summary(raw) if raw is not None else {
        "rows": 0,
        "duplicate_columns": [],
        "symbols": [],
        "timeframes": [],
        "min_ts": None,
        "max_ts": None,
    }
    duplicate_columns = sorted(
        set(raw_summary.get("duplicate_columns", []))
        | set(feature_summary.get("duplicate_columns", []))
        | {
            duplicate
            for item in file_reports or []
            for duplicate in item.get("duplicate_columns", [])
        }
    )
    return {
        "status": status,
        "reason": reason,
        "phase": "phase22_historical_market_backfill_features",
        "rows": int(feature_summary["rows"]),
        "min_ts": feature_summary["min_ts"],
        "max_ts": feature_summary["max_ts"],
        "symbols": feature_summary["symbols"],
        "timeframes": feature_summary["timeframes"],
        "duplicate_columns": duplicate_columns,
        "raw_files": [str(path) for path in files],
        "raw_rows": int(raw_summary["rows"]),
        "raw_file_reports": file_reports or [],
        "backfill_features": feature_summary,
        "main_features": main_report,
        "sqlite": sqlite_report,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_sqlite(frame: pd.DataFrame, sqlite_path: Path, table: str) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as con:
        frame.to_sql(table, con, if_exists="replace", index=False, chunksize=20000)


def merge_with_main_features(
    features: pd.DataFrame,
    main_path: Path,
    *,
    backup: bool,
    backups_dir: Path,
) -> tuple[pd.DataFrame, dict]:
    final_features = features
    backup_path = None
    if main_path.exists():
        if not backup:
            raise Phase22FeatureBuildError(MAIN_OVERWRITE_BLOCK_REASON)
        backup_dir = backups_dir / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / main_path.name
        shutil.copy2(main_path, backup_path)

        existing = pd.read_parquet(main_path)
        final_features = pd.concat([existing, features], ignore_index=True, sort=False)
        final_features["ts"] = pd.to_datetime(final_features["ts"], utc=True, errors="coerce")
        final_features["ts_ms"] = pd.to_numeric(final_features["ts_ms"], errors="coerce").astype(
            "Int64"
        )
        final_features = final_features.dropna(subset=["symbol", "tf", "ts_ms"])
        final_features["ts_ms"] = final_features["ts_ms"].astype("int64")
        final_features = final_features.drop_duplicates(
            subset=["symbol", "tf", "ts_ms"],
            keep="last",
        )
        final_features = final_features.sort_values(["symbol", "tf", "ts_ms"])
    else:
        main_path.parent.mkdir(parents=True, exist_ok=True)

    if duplicate_column_names(final_features):
        raise Phase22FeatureBuildError(
            f"duplicate_columns_in_main_features:{duplicate_column_names(final_features)}"
        )
    final_features.to_parquet(main_path, index=False)
    main_report = frame_summary(final_features, main_path)
    main_report["backup_path"] = str(backup_path) if backup_path is not None else None
    return final_features, main_report


def run_phase22_feature_build(
    *,
    raw_dir: Path,
    symbols: list[str],
    interval: str,
    output_path: Path,
    main_features_path: Path,
    sqlite_path: Path,
    sqlite_table: str,
    update_main_features: bool,
    backup: bool,
    backups_dir: Path = Path("data/backups/phase22"),
    features_report_path: Path = FEATURES_REPORT_PATH,
    quality_report_path: Path = DATA_QUALITY_REPORT_PATH,
) -> dict:
    files = raw_files(raw_dir, symbols, interval)
    file_reports: list[dict] = []
    raw: pd.DataFrame | None = None
    features: pd.DataFrame | None = None
    main_report = None
    sqlite_report = None

    try:
        if not files:
            raise Phase22FeatureBuildError(
                f"missing_raw_files:{raw_dir}:{symbols}:{interval}"
            )

        normalized_frames: list[pd.DataFrame] = []
        for path in files:
            table = read_table(path)
            duplicates = duplicate_column_names(table)
            normalized = normalize_raw(table, interval)
            file_reports.append(
                {
                    "path": str(path),
                    "status": "ok",
                    "reason": "ok",
                    "rows": int(len(normalized)),
                    "duplicate_columns": duplicates,
                    "min_ts": normalized["ts"].min().isoformat(),
                    "max_ts": normalized["ts"].max().isoformat(),
                    "symbols": sorted(normalized["symbol"].unique().tolist()),
                }
            )
            normalized_frames.append(normalized)

        raw = pd.concat(normalized_frames, ignore_index=True)
        raw = raw.drop_duplicates(subset=["symbol", "tf", "ts_ms"]).sort_values(
            ["symbol", "tf", "ts_ms"]
        )
        raw = raw.reset_index(drop=True)
        features = build_feature_frame(raw)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(output_path, index=False)

        final_features = features
        if update_main_features:
            final_features, main_report = merge_with_main_features(
                features,
                main_features_path,
                backup=backup,
                backups_dir=backups_dir,
            )

        write_sqlite(final_features, sqlite_path, sqlite_table)
        sqlite_report = {
            "path": str(sqlite_path),
            "table": sqlite_table,
            "rows_written": int(len(final_features)),
        }
        report = controlled_report(
            status="ok",
            reason="ok",
            files=files,
            raw=raw,
            features=features,
            output_path=output_path,
            main_report=main_report,
            sqlite_report=sqlite_report,
            file_reports=file_reports,
        )
    except Exception as exc:
        reason = str(exc)
        if not isinstance(exc, Phase22FeatureBuildError):
            reason = f"invalid_schema:{reason}"
        report = controlled_report(
            status="blocked",
            reason=reason,
            files=files,
            raw=raw,
            features=features,
            output_path=output_path,
            main_report=main_report,
            sqlite_report=sqlite_report,
            file_reports=file_reports,
        )

    quality_report = {
        "status": report["status"],
        "reason": report["reason"],
        "rows": report["rows"],
        "min_ts": report["min_ts"],
        "max_ts": report["max_ts"],
        "symbols": report["symbols"],
        "timeframes": report["timeframes"],
        "duplicate_columns": report["duplicate_columns"],
        "raw_file_reports": report["raw_file_reports"],
        "runtime_mode": "paper",
        "shadow_only": True,
        "exchange_private_access": False,
        "created_at": report["created_at"],
    }
    write_json(features_report_path, report)
    write_json(quality_report_path, quality_report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="data/raw/binance_futures_klines")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--interval", default="1m")
    parser.add_argument(
        "--output",
        default="data/features/market_features_1m_backfill.parquet",
    )
    parser.add_argument(
        "--main-features",
        default="data/features/market_features_60d.parquet",
    )
    parser.add_argument("--sqlite", default="data/sqlite/trading_dataset.sqlite")
    parser.add_argument("--sqlite-table", default="market_features")
    parser.add_argument("--update-main-features", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--backups-dir", default="data/backups/phase22")
    parser.add_argument("--features-report", default=str(FEATURES_REPORT_PATH))
    parser.add_argument("--quality-report", default=str(DATA_QUALITY_REPORT_PATH))
    args = parser.parse_args()

    report = run_phase22_feature_build(
        raw_dir=Path(args.raw_dir),
        symbols=args.symbols,
        interval=args.interval,
        output_path=Path(args.output),
        main_features_path=Path(args.main_features),
        sqlite_path=Path(args.sqlite),
        sqlite_table=args.sqlite_table,
        update_main_features=args.update_main_features,
        backup=args.backup,
        backups_dir=Path(args.backups_dir),
        features_report_path=Path(args.features_report),
        quality_report_path=Path(args.quality_report),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] != "ok":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
