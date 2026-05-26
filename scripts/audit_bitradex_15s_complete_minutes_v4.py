from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(".")
BITRADEX_DIR = ROOT / "bitradex_realtime_candle_collector_v1" / "data" / "output"
BINANCE_DIR = ROOT / "data" / "raw" / "binance_futures_klines"
OUT_DIR = ROOT / "data" / "reports" / "binance_bitradex_15s_complete_minutes_v4"

PRICE_GUARDS = {
    "BTCUSDT": {"min": 10000.0, "max": 300000.0},
    "ETHUSDT": {"min": 500.0, "max": 20000.0},
}


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def parse_optional_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        raise RuntimeError(f"Timestamp inválido: {value}")
    return ts


def read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise RuntimeError(f"Formato não suportado: {path}")


def load_bitradex_15s(
    symbol: str,
    start_utc: pd.Timestamp | None,
    end_utc: pd.Timestamp | None,
    max_lag_seconds: float,
) -> tuple[pd.DataFrame, dict]:
    parquet_path = BITRADEX_DIR / f"bitradex_{symbol.lower()}_futures_15s.parquet"
    csv_path = BITRADEX_DIR / f"bitradex_{symbol.lower()}_futures_15s.csv"

    if parquet_path.exists():
        path = parquet_path
    elif csv_path.exists():
        path = csv_path
    else:
        raise FileNotFoundError(f"Arquivo Bitradex 15s não encontrado para {symbol}")

    raw = read_any(path).copy()
    raw_rows = len(raw)

    required = ["timestamp", "captured_at", "open", "high", "low", "close"]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise RuntimeError(f"Colunas ausentes em {path}: {missing}")

    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce", utc=True)
    raw["captured_at"] = pd.to_datetime(raw["captured_at"], errors="coerce", utc=True)

    for col in ["open", "high", "low", "close", "volume"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
        else:
            raw[col] = 0.0

    parsed = raw.dropna(subset=["timestamp", "captured_at", "open", "high", "low", "close"]).copy()

    guard = PRICE_GUARDS[symbol]

    price_ok = (
        parsed["open"].between(guard["min"], guard["max"])
        & parsed["high"].between(guard["min"], guard["max"])
        & parsed["low"].between(guard["min"], guard["max"])
        & parsed["close"].between(guard["min"], guard["max"])
        & (parsed["low"] <= parsed["high"])
        & (parsed["open"] <= parsed["high"])
        & (parsed["close"] <= parsed["high"])
        & (parsed["open"] >= parsed["low"])
        & (parsed["close"] >= parsed["low"])
    )

    parsed["lag_seconds_abs"] = (parsed["captured_at"] - parsed["timestamp"]).dt.total_seconds().abs()
    lag_ok = parsed["lag_seconds_abs"] <= max_lag_seconds

    window_ok = pd.Series(True, index=parsed.index)

    if start_utc is not None:
        window_ok &= parsed["timestamp"] >= start_utc
        window_ok &= parsed["captured_at"] >= start_utc

    if end_utc is not None:
        window_ok &= parsed["timestamp"] <= end_utc

    clean = parsed[price_ok & lag_ok & window_ok].copy()
    rejected = parsed[~(price_ok & lag_ok & window_ok)].copy()

    clean = clean.sort_values(["timestamp", "captured_at"])
    clean = clean.drop_duplicates("timestamp", keep="last").reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clean.to_csv(OUT_DIR / f"{symbol}_bitradex_15s_clean_window.csv", index=False, encoding="utf-8-sig")
    rejected.to_csv(OUT_DIR / f"{symbol}_bitradex_15s_rejected_window.csv", index=False, encoding="utf-8-sig")

    meta = {
        "symbol": symbol,
        "source_file": str(path),
        "raw_rows": int(raw_rows),
        "parsed_rows": int(len(parsed)),
        "clean_rows": int(len(clean)),
        "rejected_rows": int(len(rejected)),
        "start_utc_filter": start_utc.isoformat() if start_utc is not None else None,
        "end_utc_filter": end_utc.isoformat() if end_utc is not None else None,
        "max_lag_seconds": float(max_lag_seconds),
        "min_ts": clean["timestamp"].min().isoformat() if len(clean) else None,
        "max_ts": clean["timestamp"].max().isoformat() if len(clean) else None,
        "max_lag_seconds_abs_clean": float(clean["lag_seconds_abs"].max()) if len(clean) else None,
    }

    return clean, meta


def resample_complete_minutes(
    df: pd.DataFrame,
    min_15s_per_minute: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "subcandles_15s"])
        return empty, empty

    work = df.copy()
    work["minute"] = work["timestamp"].dt.floor("min")
    work = work.sort_values("timestamp")

    grouped = work.groupby("minute", dropna=False)

    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        subcandles_15s=("timestamp", "count"),
        first_15s_ts=("timestamp", "min"),
        last_15s_ts=("timestamp", "max"),
    ).reset_index()

    out = out.rename(columns={"minute": "timestamp"})

    complete = out[out["subcandles_15s"] >= min_15s_per_minute].copy()
    incomplete = out[out["subcandles_15s"] < min_15s_per_minute].copy()

    return complete.reset_index(drop=True), incomplete.reset_index(drop=True)


def ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def fetch_binance_1m(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    start_ms = ms(start)
    end_ms = ms(end)

    while start_ms <= end_ms:
        params = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1500,
        })

        url = f"https://fapi.binance.com/fapi/v1/klines?{params}"

        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not payload:
            break

        rows.extend(payload)

        last_open_ms = int(payload[-1][0])
        next_start_ms = last_open_ms + 60_000

        if next_start_ms <= start_ms:
            break

        start_ms = next_start_ms
        time.sleep(0.10)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=[
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ])

    out = pd.DataFrame()
    out["timestamp"] = pd.to_datetime(pd.to_numeric(df["open_time"], errors="coerce"), unit="ms", utc=True)
    out["open"] = pd.to_numeric(df["open"], errors="coerce")
    out["high"] = pd.to_numeric(df["high"], errors="coerce")
    out["low"] = pd.to_numeric(df["low"], errors="coerce")
    out["close"] = pd.to_numeric(df["close"], errors="coerce")
    out["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    out = out.dropna(subset=["timestamp", "open", "high", "low", "close"])
    out = out.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)

    return out


def save_binance_recent(symbol: str, df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    BINANCE_DIR.mkdir(parents=True, exist_ok=True)

    start_tag = start.strftime("%Y%m%d_%H%M")
    end_tag = end.strftime("%Y%m%d_%H%M")

    csv_path = BINANCE_DIR / f"{symbol}_1m_v4_bitradex15s_{start_tag}_{end_tag}.csv"
    parquet_path = BINANCE_DIR / f"{symbol}_1m_v4_bitradex15s_{start_tag}_{end_tag}.parquet"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    parquet_written = False
    try:
        df.to_parquet(parquet_path, index=False)
        parquet_written = True
    except Exception:
        pass

    return {
        "csv": str(csv_path),
        "parquet": str(parquet_path) if parquet_written else None,
    }


def compare(
    symbol: str,
    binance_1m: pd.DataFrame,
    bitradex_1m: pd.DataFrame,
    close_tolerance_bps: float,
    range_overlap_min_pct: float,
    min_common_rows: int,
) -> tuple[pd.DataFrame, dict]:
    b = binance_1m.set_index("timestamp")[["open", "high", "low", "close", "volume"]].add_prefix("binance_")
    x = bitradex_1m.set_index("timestamp")[["open", "high", "low", "close", "volume", "subcandles_15s"]].add_prefix("bitradex_")

    m = b.join(x, how="inner").reset_index()

    if m.empty:
        return m, {
            "symbol": symbol,
            "status": "no_overlap",
            "common_rows": 0,
            "reason": "Sem candles 1m completas em comum.",
        }

    m["close_diff_bps"] = (m["bitradex_close"] - m["binance_close"]).abs() / m["binance_close"] * 10000.0

    union_high = m[["binance_high", "bitradex_high"]].max(axis=1)
    union_low = m[["binance_low", "bitradex_low"]].min(axis=1)
    inter_high = m[["binance_high", "bitradex_high"]].min(axis=1)
    inter_low = m[["binance_low", "bitradex_low"]].max(axis=1)

    union = (union_high - union_low).replace(0, np.nan)
    inter = (inter_high - inter_low).clip(lower=0)

    m["range_overlap_pct"] = (inter / union * 100.0).fillna(0.0)

    m["direction_match"] = (
        (m["binance_close"] >= m["binance_open"])
        == (m["bitradex_close"] >= m["bitradex_open"])
    )

    m["compatible"] = (
        (m["close_diff_bps"] <= close_tolerance_bps)
        & (m["range_overlap_pct"] >= range_overlap_min_pct)
    )

    compatible_ratio = float(m["compatible"].mean())

    if len(m) < min_common_rows:
        status = "needs_more_complete_minutes"
    elif compatible_ratio >= 0.95:
        status = "approved"
    elif compatible_ratio >= 0.80:
        status = "warning_partial_compatibility"
    else:
        status = "rejected_low_compatibility"

    report = {
        "symbol": symbol,
        "status": status,
        "common_rows": int(len(m)),
        "min_common_rows_required": int(min_common_rows),
        "compatible_rows": int(m["compatible"].sum()),
        "divergent_rows": int((~m["compatible"]).sum()),
        "compatible_ratio": compatible_ratio,
        "first_common_ts": m["timestamp"].min().isoformat(),
        "last_common_ts": m["timestamp"].max().isoformat(),
        "close_diff_bps": {
            "mean": float(m["close_diff_bps"].mean()),
            "median": float(m["close_diff_bps"].median()),
            "p95": float(m["close_diff_bps"].quantile(0.95)),
            "p99": float(m["close_diff_bps"].quantile(0.99)),
            "max": float(m["close_diff_bps"].max()),
        },
        "range_overlap_pct": {
            "mean": float(m["range_overlap_pct"].mean()),
            "median": float(m["range_overlap_pct"].median()),
            "p05": float(m["range_overlap_pct"].quantile(0.05)),
        },
        "direction_match_rate": float(m["direction_match"].mean()),
        "subcandles_15s": {
            "min": int(m["bitradex_subcandles_15s"].min()),
            "median": float(m["bitradex_subcandles_15s"].median()),
            "max": int(m["bitradex_subcandles_15s"].max()),
        },
    }

    return m, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--start-utc", type=str, default=None)
    parser.add_argument("--end-utc", type=str, default=None)
    parser.add_argument("--max-lag-seconds", type=float, default=90.0)
    parser.add_argument("--min-15s-per-minute", type=int, default=3)
    parser.add_argument("--close-tolerance-bps", type=float, default=20.0)
    parser.add_argument("--range-overlap-min-pct", type=float, default=50.0)
    parser.add_argument("--min-common-rows", type=int, default=20)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    start_utc = parse_optional_ts(args.start_utc)
    end_utc = parse_optional_ts(args.end_utc)

    summary = {
        "status": "ok",
        "mode": "binance_bitradex_15s_complete_minutes_v4",
        "safety": {
            "audit_only": True,
            "sends_orders": False,
            "changes_risk": False,
            "live_trading": False,
        },
        "parameters": vars(args),
        "symbols": {},
    }

    for symbol in args.symbols:
        bit15, bit15_meta = load_bitradex_15s(
            symbol=symbol,
            start_utc=start_utc,
            end_utc=end_utc,
            max_lag_seconds=args.max_lag_seconds,
        )

        bit1m_complete, bit1m_incomplete = resample_complete_minutes(
            bit15,
            min_15s_per_minute=args.min_15s_per_minute,
        )

        bit1m_complete_path = OUT_DIR / f"{symbol}_bitradex_15s_agg1m_complete.csv"
        bit1m_incomplete_path = OUT_DIR / f"{symbol}_bitradex_15s_agg1m_incomplete.csv"

        bit1m_complete.to_csv(bit1m_complete_path, index=False, encoding="utf-8-sig")
        bit1m_incomplete.to_csv(bit1m_incomplete_path, index=False, encoding="utf-8-sig")

        if bit1m_complete.empty:
            summary["symbols"][symbol] = {
                "status": "needs_more_complete_minutes",
                "bitradex_15s": bit15_meta,
                "bitradex_agg_1m": {
                    "complete_rows": 0,
                    "incomplete_rows": int(len(bit1m_incomplete)),
                    "min_15s_per_minute_required": int(args.min_15s_per_minute),
                },
                "outputs": {
                    "complete_1m_csv": str(bit1m_complete_path),
                    "incomplete_1m_csv": str(bit1m_incomplete_path),
                    "clean_15s_csv": str(OUT_DIR / f"{symbol}_bitradex_15s_clean_window.csv"),
                    "rejected_15s_csv": str(OUT_DIR / f"{symbol}_bitradex_15s_rejected_window.csv"),
                },
            }
            continue

        dl_start = bit1m_complete["timestamp"].min() - pd.Timedelta(minutes=5)
        dl_end = bit1m_complete["timestamp"].max() + pd.Timedelta(minutes=5)

        binance = fetch_binance_1m(symbol, dl_start, dl_end)
        binance_files = save_binance_recent(symbol, binance, dl_start, dl_end)

        comp, comp_report = compare(
            symbol=symbol,
            binance_1m=binance,
            bitradex_1m=bit1m_complete,
            close_tolerance_bps=args.close_tolerance_bps,
            range_overlap_min_pct=args.range_overlap_min_pct,
            min_common_rows=args.min_common_rows,
        )

        comp_path = OUT_DIR / f"{symbol}_complete15s_agg1m_vs_binance1m.csv"
        anomalies_path = OUT_DIR / f"{symbol}_complete15s_agg1m_vs_binance1m_anomalies.csv"

        comp.to_csv(comp_path, index=False, encoding="utf-8-sig")

        if "compatible" in comp.columns:
            comp[~comp["compatible"]].to_csv(anomalies_path, index=False, encoding="utf-8-sig")
        else:
            pd.DataFrame().to_csv(anomalies_path, index=False, encoding="utf-8-sig")

        summary["symbols"][symbol] = {
            "bitradex_15s": bit15_meta,
            "bitradex_agg_1m": {
                "complete_rows": int(len(bit1m_complete)),
                "incomplete_rows": int(len(bit1m_incomplete)),
                "min_ts": bit1m_complete["timestamp"].min().isoformat(),
                "max_ts": bit1m_complete["timestamp"].max().isoformat(),
                "min_15s_per_minute_required": int(args.min_15s_per_minute),
            },
            "binance_1m_downloaded": {
                "rows": int(len(binance)),
                "min_ts": binance["timestamp"].min().isoformat() if len(binance) else None,
                "max_ts": binance["timestamp"].max().isoformat() if len(binance) else None,
                "files": binance_files,
            },
            "comparison": comp_report,
            "outputs": {
                "comparison_csv": str(comp_path),
                "anomalies_csv": str(anomalies_path),
                "complete_1m_csv": str(bit1m_complete_path),
                "incomplete_1m_csv": str(bit1m_incomplete_path),
                "clean_15s_csv": str(OUT_DIR / f"{symbol}_bitradex_15s_clean_window.csv"),
                "rejected_15s_csv": str(OUT_DIR / f"{symbol}_bitradex_15s_rejected_window.csv"),
            },
        }

    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
