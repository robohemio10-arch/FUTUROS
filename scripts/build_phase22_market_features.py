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


class Phase22FeatureBuildError(ValueError):
    pass

def read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)

def raw_files(raw_dir: Path, symbols: list[str], interval: str) -> list[Path]:
    files: list[Path] = []
    for symbol in symbols:
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.parquet")))
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.csv")))
    return files


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
    return pd.to_numeric(series_1d(frame, column), errors="coerce")


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
    for period in [1, 3, 5]:
        group[f"future_ret_{period}"] = close.shift(-period) / close - 1

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
    return normalized

def write_sqlite(frame: pd.DataFrame, sqlite_path: Path, table: str) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as con:
        frame.to_sql(table, con, if_exists="replace", index=False, chunksize=20000)

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
    args = parser.parse_args()

    files = raw_files(Path(args.raw_dir), args.symbols, args.interval)
    if not files:
        raise FileNotFoundError(
            f"Nenhum candle encontrado em {args.raw_dir} "
            f"para {args.symbols} {args.interval}"
        )

    normalized_frames: list[pd.DataFrame] = []
    for path in files:
        try:
            normalized_frames.append(normalize_raw(read_table(path), args.interval))
        except Exception as exc:
            raise Phase22FeatureBuildError(f"failed_to_normalize:{path}:{exc}") from exc
    raw = pd.concat(normalized_frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["symbol", "tf", "ts_ms"]).sort_values(
        ["symbol", "tf", "ts_ms"]
    )
    features = (
        raw.groupby(["symbol", "tf"], group_keys=False)
        .apply(build_group_features)
        .reset_index(drop=True)
    )
    features = features.sort_values(["symbol", "tf", "ts_ms"])

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(output_path, index=False)

    final_features = features
    main_report = None
    main_path = Path(args.main_features)
    if args.update_main_features:
        if main_path.exists():
            existing = pd.read_parquet(main_path)
            if args.backup:
                backup_dir = Path("data/backups/phase22") / datetime.now(
                    timezone.utc
                ).strftime("%Y%m%d_%H%M%S")
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(main_path, backup_dir / main_path.name)
            final_features = pd.concat([existing, features], ignore_index=True, sort=False)
            final_features["ts"] = pd.to_datetime(
                final_features["ts"],
                utc=True,
                errors="coerce",
            )
            final_features["ts_ms"] = pd.to_numeric(
                final_features["ts_ms"],
                errors="coerce",
            ).astype("Int64")
            final_features = final_features.dropna(subset=["symbol", "tf", "ts_ms"])
            final_features["ts_ms"] = final_features["ts_ms"].astype("int64")
            final_features = final_features.drop_duplicates(
                subset=["symbol", "tf", "ts_ms"],
                keep="last",
            )
            final_features = final_features.sort_values(["symbol", "tf", "ts_ms"])
        else:
            main_path.parent.mkdir(parents=True, exist_ok=True)
        final_features.to_parquet(main_path, index=False)
        main_report = {
            "path": str(main_path),
            "rows": int(len(final_features)),
            "min_ts": final_features["ts"].min().isoformat(),
            "max_ts": final_features["ts"].max().isoformat(),
        }

    write_sqlite(final_features, Path(args.sqlite), args.sqlite_table)
    report = {
        "status": "ok",
        "phase": "phase22_historical_market_backfill_features",
        "raw_files": [str(path) for path in files],
        "raw_rows": int(len(raw)),
        "backfill_features": {
            "path": str(output_path),
            "rows": int(len(features)),
            "columns": list(features.columns),
            "min_ts": features["ts"].min().isoformat(),
            "max_ts": features["ts"].max().isoformat(),
        },
        "main_features": main_report,
        "sqlite": {
            "path": args.sqlite,
            "table": args.sqlite_table,
            "rows_written": int(len(final_features)),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase22_features_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
