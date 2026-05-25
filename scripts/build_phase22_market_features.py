from __future__ import annotations
import argparse, json, shutil, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

BASE_COLS = ["symbol", "pair", "tf", "ts", "ts_ms", "open", "high", "low", "close", "volume"]

def read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)

def raw_files(raw_dir: Path, symbols: list[str], interval: str) -> list[Path]:
    files: list[Path] = []
    for symbol in symbols:
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.parquet")))
        files.extend(sorted(raw_dir.glob(f"{symbol}_{interval}_*.csv")))
    return files

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
    group["macd_signal"] = group["macd_line"].ewm(span=9, adjust=False, min_periods=9).mean()
    group["macd_hist"] = group["macd_line"] - group["macd_signal"]

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    group["atr_14"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    group["atr_pct_14"] = group["atr_14"] / close.replace(0, np.nan)
    group["vol_30"] = group["ret_1"].rolling(30, min_periods=10).std()
    group["vol_120"] = group["ret_1"].rolling(120, min_periods=30).std()
    group["volume_mean_30"] = volume.rolling(30, min_periods=10).mean()
    group["volume_mean_120"] = volume.rolling(120, min_periods=30).mean()
    group["volume_rel_30"] = volume / group["volume_mean_30"].replace(0, np.nan)
    group["volume_z_30"] = (volume - group["volume_mean_30"]) / volume.rolling(30, min_periods=10).std().replace(0, np.nan)

    group["hl_range"] = (high - low) / close.replace(0, np.nan)
    group["body_range"] = (close - open_).abs() / close.replace(0, np.nan)
    group["upper_wick"] = (high - pd.concat([open_, close], axis=1).max(axis=1)) / close.replace(0, np.nan)
    group["lower_wick"] = (pd.concat([open_, close], axis=1).min(axis=1) - low) / close.replace(0, np.nan)
    group["trend_score"] = np.sign(group["dist_ema20"].fillna(0)) + np.sign(group["dist_ema50"].fillna(0)) + np.sign(group["dist_ema200"].fillna(0))

    high_vol = group["atr_pct_14"].fillna(0) > group["atr_pct_14"].rolling(500, min_periods=100).quantile(0.75).fillna(np.inf)
    trend_up = group["trend_score"] >= 2
    trend_down = group["trend_score"] <= -2
    regime = pd.Series(np.where(trend_up, "trend_up", np.where(trend_down, "trend_down", "range")), index=group.index)
    group["market_regime"] = np.where(high_vol, regime + "_high_vol", regime)
    return group

def normalize_raw(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.rename(columns={"open_time": "ts_ms"}).copy()
    if "ts" not in raw.columns:
        raw["ts"] = pd.to_datetime(raw["ts_ms"], unit="ms", utc=True)
    else:
        raw["ts"] = pd.to_datetime(raw["ts"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "ts_ms"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["ts_ms"] = raw["ts_ms"].astype("int64")
    if "tf" not in raw.columns:
        raw["tf"] = "1m"
    if "pair" not in raw.columns:
        raw["pair"] = raw["symbol"].astype(str).str.replace("USDT", "/USDT:USDT", regex=False)
    return raw[BASE_COLS].dropna(subset=["ts", "ts_ms", "open", "high", "low", "close"])

def write_sqlite(frame: pd.DataFrame, sqlite_path: Path, table: str) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as con:
        frame.to_sql(table, con, if_exists="replace", index=False, chunksize=20000)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="data/raw/binance_futures_klines")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--output", default="data/features/market_features_1m_backfill.parquet")
    parser.add_argument("--main-features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--sqlite", default="data/sqlite/trading_dataset.sqlite")
    parser.add_argument("--sqlite-table", default="market_features")
    parser.add_argument("--update-main-features", action="store_true")
    parser.add_argument("--backup", action="store_true")
    args = parser.parse_args()

    files = raw_files(Path(args.raw_dir), args.symbols, args.interval)
    if not files:
        raise FileNotFoundError(f"Nenhum candle encontrado em {args.raw_dir} para {args.symbols} {args.interval}")

    raw = pd.concat([normalize_raw(read_table(path)) for path in files], ignore_index=True)
    raw = raw.drop_duplicates(subset=["symbol", "tf", "ts_ms"]).sort_values(["symbol", "tf", "ts_ms"])
    features = raw.groupby(["symbol", "tf"], group_keys=False).apply(build_group_features).reset_index(drop=True)
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
                backup_dir = Path("data/backups/phase22") / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(main_path, backup_dir / main_path.name)
            final_features = pd.concat([existing, features], ignore_index=True, sort=False)
            final_features["ts"] = pd.to_datetime(final_features["ts"], utc=True, errors="coerce")
            final_features["ts_ms"] = pd.to_numeric(final_features["ts_ms"], errors="coerce").astype("Int64")
            final_features = final_features.dropna(subset=["symbol", "tf", "ts_ms"])
            final_features["ts_ms"] = final_features["ts_ms"].astype("int64")
            final_features = final_features.drop_duplicates(subset=["symbol", "tf", "ts_ms"], keep="last")
            final_features = final_features.sort_values(["symbol", "tf", "ts_ms"])
        else:
            main_path.parent.mkdir(parents=True, exist_ok=True)
        final_features.to_parquet(main_path, index=False)
        main_report = {"path": str(main_path), "rows": int(len(final_features)), "min_ts": final_features["ts"].min().isoformat(), "max_ts": final_features["ts"].max().isoformat()}

    write_sqlite(final_features, Path(args.sqlite), args.sqlite_table)
    report = {
        "status": "ok",
        "phase": "phase22_historical_market_backfill_features",
        "raw_files": [str(path) for path in files],
        "raw_rows": int(len(raw)),
        "backfill_features": {"path": str(output_path), "rows": int(len(features)), "columns": list(features.columns), "min_ts": features["ts"].min().isoformat(), "max_ts": features["ts"].max().isoformat()},
        "main_features": main_report,
        "sqlite": {"path": args.sqlite, "table": args.sqlite_table, "rows_written": int(len(final_features))},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase22_features_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
