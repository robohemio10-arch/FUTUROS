from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm

from smartcrypto.execution.freqtrade_contract import ccxt_symbol, freqtrade_pair, internal_symbol


@dataclass(frozen=True)
class DownloadResult:
    symbol: str
    timeframe: str
    rows: int
    path: Path


def download_ohlcv(
    symbols: list[str],
    timeframes: list[str],
    days: int,
    output_dir: str | Path,
) -> pd.DataFrame:
    exchange = _build_exchange()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    since_ms = _utc_ms(start)
    until_ms = _utc_ms(end)

    frames: list[pd.DataFrame] = []
    results: list[DownloadResult] = []

    for symbol in symbols:
        for timeframe in timeframes:
            frame = _fetch_ohlcv_full(exchange, symbol, timeframe, since_ms, until_ms)
            path = destination / f"futures_ohlcv_{internal_symbol(symbol)}_{timeframe}_{days}d.parquet"
            frame.to_parquet(path, index=False)
            frames.append(frame)
            results.append(DownloadResult(internal_symbol(symbol), timeframe, len(frame), path))

    if not frames:
        raise RuntimeError("no market data was downloaded")

    consolidated = pd.concat(frames, ignore_index=True).sort_values(["symbol", "tf", "ts"])
    consolidated_path = destination / f"futures_ohlcv_{days}d.parquet"
    consolidated.to_parquet(consolidated_path, index=False)

    _write_manifest(destination, days, results, consolidated_path)
    return consolidated


def _build_exchange() -> Any:
    import ccxt

    return ccxt.binance(
        {
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
            },
        }
    )


def _fetch_ohlcv_full(
    exchange: Any,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
) -> pd.DataFrame:
    rows: list[list[float]] = []
    current = since_ms
    step_ms = _timeframe_ms(timeframe)
    market_symbol = ccxt_symbol(symbol)
    attempts = 0

    with tqdm(desc=f"{market_symbol} {timeframe}", leave=False) as progress:
        while current < until_ms:
            try:
                batch = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, since=current, limit=1000)
                attempts = 0
            except Exception as exc:
                attempts += 1
                if attempts > 8:
                    raise RuntimeError(f"failed to download {market_symbol} {timeframe} at {current}") from exc
                time.sleep(min(2.0 * attempts, 15.0))
                continue

            if not batch:
                current += step_ms
                time.sleep(0.3)
                continue

            rows.extend(batch)
            previous = current
            current = int(batch[-1][0]) + step_ms

            if current <= previous:
                current = previous + step_ms

            progress.update(len(batch))
            time.sleep(0.15)

    return _build_ohlcv_frame(rows, symbol, timeframe)


def _build_ohlcv_frame(rows: list[list[float]], symbol: str, timeframe: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["symbol", "pair", "tf", "ts", "ts_ms", "open", "high", "low", "close", "volume"]
        )

    frame = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close", "volume"])
    frame = frame.drop_duplicates(subset=["ts_ms"]).sort_values("ts_ms").reset_index(drop=True)
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    frame["ts"] = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True)
    frame["symbol"] = internal_symbol(symbol)
    frame["pair"] = freqtrade_pair(symbol)
    frame["tf"] = timeframe

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    return frame[["symbol", "pair", "tf", "ts", "ts_ms", "open", "high", "low", "close", "volume"]].dropna()


def _write_manifest(
    destination: Path,
    days: int,
    results: list[DownloadResult],
    consolidated_path: Path,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "days": days,
        "consolidated_path": str(consolidated_path),
        "files": [
            {
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "rows": item.rows,
                "path": str(item.path),
            }
            for item in results
        ],
    }
    (destination / "download_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _utc_ms(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _timeframe_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    amount = int(timeframe[:-1])

    if unit == "m":
        return amount * 60_000

    if unit == "h":
        return amount * 3_600_000

    if unit == "d":
        return amount * 86_400_000

    raise ValueError(f"unsupported timeframe: {timeframe}")
