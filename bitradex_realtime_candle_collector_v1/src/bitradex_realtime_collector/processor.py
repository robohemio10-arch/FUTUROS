from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import re
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import pandas as pd

from .config import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES, TIMEFRAME_SECONDS, RuntimeConfig
from .models import Candle

LOGGER = logging.getLogger("bitradex.collector.processor")

SYMBOL_ALIASES: dict[str, str] = {
    "BTCUSDT": "BTCUSDT",
    "BTC/USDT": "BTCUSDT",
    "BTC_USDT": "BTCUSDT",
    "BTC-USDT": "BTCUSDT",
    "btc_usdt": "BTCUSDT",
    "btcusdt": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "ETH/USDT": "ETHUSDT",
    "ETH_USDT": "ETHUSDT",
    "ETH-USDT": "ETHUSDT",
    "eth_usdt": "ETHUSDT",
    "ethusdt": "ETHUSDT",
}

TIMEFRAME_ALIASES: dict[str, str] = {
    "15s": "15s",
    "15sec": "15s",
    "15second": "15s",
    "15seconds": "15s",
    "15seg": "15s",
    "15segundos": "15s",
    "1": "1m",
    "1m": "1m",
    "60": "1m",
    "60s": "1m",
    "5": "5m",
    "5m": "5m",
    "300": "5m",
    "300s": "5m",
    "15": "15m",
    "15m": "15m",
    "900": "15m",
    "900s": "15m",
    "1h": "1h",
    "60m": "1h",
    "1d": "1d",
    "d": "1d",
}

FORBIDDEN_PRIVATE_HINTS = (
    "authorization",
    "private",
    "account",
    "balance",
    "position",
    "order",
    "apikey",
    "api-key",
    "x-mbx-apikey",
)

KLINE_URL_HINTS = (
    "kline",
    "candle",
    "candlestick",
    "history",
    "bars",
    "chart",
    "tradingview",
    "symbol_info",
    "quote",
    "market",
)


@dataclass(slots=True)
class ProcessResult:
    candles_found: int = 0
    candles_written: int = 0
    rejected: int = 0
    symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()


class DataProcessor:
    """Extract, normalize, store and export captured Bitradex OHLCV data.

    SQLite is the canonical low-memory store. CSV/Parquet files are exported
    from SQLite snapshots so the collector can run continuously without keeping
    the full candle history in RAM.
    """

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.config.ensure_directories()
        self._seen_keys: set[tuple[str, str, str]] = set()
        self._ticker_candle_state: dict[tuple[str, str, str], dict[str, float | datetime]] = {}
        self._last_ticker_volume: dict[str, float] = {}
        self._connection = sqlite3.connect(self.config.sqlite_path, timeout=30, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source_url TEXT NOT NULL,
                transport TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                raw_hash TEXT NOT NULL,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                transport TEXT NOT NULL,
                source_url TEXT NOT NULL,
                raw_hash TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL,
                candles_found INTEGER NOT NULL,
                candles_written INTEGER NOT NULL,
                rejected INTEGER NOT NULL
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts ON candles(symbol, timeframe, timestamp)")

    def process_payload(
        self,
        payload: bytes | str | dict[str, Any] | list[Any],
        *,
        source_url: str,
        transport: str,
        context_symbol: str | None = None,
        context_timeframe: str | None = None,
    ) -> ProcessResult:
        captured_at = datetime.now(timezone.utc)
        raw_bytes = self._payload_to_bytes(payload)
        raw_hash = hashlib.sha256(raw_bytes).hexdigest()
        if self._looks_private(source_url, raw_bytes):
            LOGGER.debug("Skipping private-looking payload url=%s hash=%s", source_url, raw_hash[:12])
            return ProcessResult()

        decoded = self._decode_payload(payload)
        if decoded is None:
            return self._record_event(captured_at, transport, source_url, raw_hash, len(raw_bytes), 0, 0, 1)

        if self.config.enable_raw_payload_audit and self._looks_like_kline_source(source_url, decoded):
            self._append_raw_payload(source_url, transport, raw_hash, decoded, captured_at)

        candles = list(
            self._extract_candles(
                decoded,
                source_url=source_url,
                transport=transport,
                captured_at=captured_at,
                raw_hash=raw_hash,
                context_symbol=context_symbol,
                context_timeframe=context_timeframe,
            )
        )
        # V3 fallback: Bitradex sometimes exposes public ticker streams but does
        # not expose a classic REST/TradingView kline payload to unauthenticated
        # sessions. When no native candles are found, aggregate live ticker prices
        # into 1m/5m/15m OHLC bars. These bars are explicitly marked by transport
        # as ticker_aggregated, so downstream jobs can distinguish them from
        # exchange-native kline payloads.
        if not candles and self.config.enable_ticker_aggregation:
            candles = list(
                self._extract_ticker_aggregated_candles(
                    decoded,
                    source_url=source_url,
                    transport=f"{transport}:ticker_aggregated",
                    captured_at=captured_at,
                    raw_hash=raw_hash,
                    context_symbol=context_symbol,
                )
            )
        written = self._write_candles(candles)
        symbols = tuple(sorted({c.symbol for c in candles}))
        timeframes = tuple(sorted({c.timeframe for c in candles}))
        rejected = max(0, len(candles) - written)
        return self._record_event(captured_at, transport, source_url, raw_hash, len(raw_bytes), len(candles), written, rejected, symbols, timeframes)

    def export_all(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"exports": [], "total_rows": 0}
        for symbol in self.config.symbols:
            for timeframe in self.config.timeframes:
                exported = self.export_symbol_timeframe(symbol, timeframe)
                if exported:
                    summary["exports"].append(exported)
                    summary["total_rows"] += exported["rows"]
        return summary

    def export_symbol_timeframe(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        symbol = self._normalize_symbol(symbol) or symbol.upper()
        timeframe = self._normalize_timeframe(timeframe) or timeframe.lower()
        query = """
            SELECT symbol, timeframe, timestamp, timestamp_ms, open, high, low, close, volume,
                   source_url, transport, captured_at, raw_hash
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp ASC
        """
        df = pd.read_sql_query(query, self._connection, params=(symbol, timeframe))
        if len(df) < self.config.min_export_rows:
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).drop_duplicates(["symbol", "timeframe", "timestamp"], keep="last")
        df = df.sort_values("timestamp")

        base_name = f"bitradex_{symbol.lower()}_futures_{timeframe}"
        csv_path = self.config.output_dir / f"{base_name}.csv"
        parquet_path = self.config.output_dir / f"{base_name}.parquet"
        df.to_csv(csv_path, index=False)
        df.to_parquet(parquet_path, index=False)

        mirror_csv_path = None
        mirror_parquet_path = None
        if self.config.mirror_phase22_dir:
            mirror_csv_path = self.config.mirror_phase22_dir / f"{base_name}_live.csv"
            mirror_parquet_path = self.config.mirror_phase22_dir / f"{base_name}_live.parquet"
            df.to_csv(mirror_csv_path, index=False)
            df.to_parquet(mirror_parquet_path, index=False)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "rows": int(len(df)),
            "csv": str(csv_path),
            "parquet": str(parquet_path),
            "mirror_csv": str(mirror_csv_path) if mirror_csv_path else None,
            "mirror_parquet": str(mirror_parquet_path) if mirror_parquet_path else None,
            "min_ts": df["timestamp"].min().isoformat() if len(df) else None,
            "max_ts": df["timestamp"].max().isoformat() if len(df) else None,
        }

    def stats(self) -> dict[str, Any]:
        rows = self._connection.execute(
            """
            SELECT symbol, timeframe, COUNT(*) AS rows, MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
            FROM candles
            GROUP BY symbol, timeframe
            ORDER BY symbol, timeframe
            """
        ).fetchall()
        return {
            "sqlite": str(self.config.sqlite_path),
            "groups": [
                {"symbol": r[0], "timeframe": r[1], "rows": int(r[2]), "min_ts": r[3], "max_ts": r[4]}
                for r in rows
            ],
            "total_rows": int(sum(r[2] for r in rows)),
        }

    def _write_candles(self, candles: list[Candle]) -> int:
        if not candles:
            return 0
        records: list[tuple[Any, ...]] = []
        for candle in candles:
            if not self._is_valid_candle(candle):
                continue
            key = candle.normalized_key()
            is_live_update = self.config.allow_live_candle_updates and "ticker_aggregated" in candle.transport
            if key in self._seen_keys and not is_live_update:
                continue
            self._seen_keys.add(key)
            record = candle.as_record()
            records.append(
                (
                    record["symbol"],
                    record["timeframe"],
                    record["timestamp"],
                    record["timestamp_ms"],
                    record["open"],
                    record["high"],
                    record["low"],
                    record["close"],
                    record["volume"],
                    record["source_url"],
                    record["transport"],
                    record["captured_at"],
                    record["raw_hash"],
                )
            )
        if not records:
            return 0
        with self._connection:
            self._connection.executemany(
                """
                INSERT OR REPLACE INTO candles (
                    symbol, timeframe, timestamp, timestamp_ms, open, high, low, close, volume,
                    source_url, transport, captured_at, raw_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
        return len(records)

    def _record_event(
        self,
        captured_at: datetime,
        transport: str,
        source_url: str,
        raw_hash: str,
        payload_bytes: int,
        candles_found: int,
        candles_written: int,
        rejected: int,
        symbols: tuple[str, ...] = (),
        timeframes: tuple[str, ...] = (),
    ) -> ProcessResult:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO ingest_events (
                    captured_at, transport, source_url, raw_hash, payload_bytes,
                    candles_found, candles_written, rejected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at.isoformat(),
                    transport,
                    source_url[:2000],
                    raw_hash,
                    int(payload_bytes),
                    int(candles_found),
                    int(candles_written),
                    int(rejected),
                ),
            )
        return ProcessResult(candles_found, candles_written, rejected, symbols, timeframes)

    def _extract_candles(
        self,
        obj: Any,
        *,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
        context_symbol: str | None,
        context_timeframe: str | None,
    ) -> Iterable[Candle]:
        context_symbol = self._normalize_symbol(context_symbol) or self._symbol_from_url(source_url)
        context_timeframe = self._normalize_timeframe(context_timeframe) or self._timeframe_from_url(source_url)
        yield from self._extract_udf_payload(obj, source_url, transport, captured_at, raw_hash, context_symbol, context_timeframe)
        yield from self._extract_dict_list_payload(obj, source_url, transport, captured_at, raw_hash, context_symbol, context_timeframe)
        yield from self._extract_array_payload(obj, source_url, transport, captured_at, raw_hash, context_symbol, context_timeframe)
        if isinstance(obj, dict):
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    yield from self._extract_candles(
                        value,
                        source_url=source_url,
                        transport=transport,
                        captured_at=captured_at,
                        raw_hash=raw_hash,
                        context_symbol=context_symbol,
                        context_timeframe=context_timeframe,
                    )
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    yield from self._extract_candles(
                        value,
                        source_url=source_url,
                        transport=transport,
                        captured_at=captured_at,
                        raw_hash=raw_hash,
                        context_symbol=context_symbol,
                        context_timeframe=context_timeframe,
                    )

    def _extract_udf_payload(
        self,
        obj: Any,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
        context_symbol: str | None,
        context_timeframe: str | None,
    ) -> Iterable[Candle]:
        if not isinstance(obj, dict):
            return
        keys = set(obj.keys())
        if not {"t", "o", "h", "l", "c"}.issubset(keys):
            return
        times, opens, highs, lows, closes = obj.get("t"), obj.get("o"), obj.get("h"), obj.get("l"), obj.get("c")
        volumes = obj.get("v", [0] * len(times) if isinstance(times, list) else [])
        if not all(isinstance(x, list) for x in (times, opens, highs, lows, closes, volumes)):
            return
        length = min(len(times), len(opens), len(highs), len(lows), len(closes), len(volumes))
        symbol = context_symbol or self._normalize_symbol(obj.get("symbol") or obj.get("s"))
        timeframe = context_timeframe or self._normalize_timeframe(obj.get("resolution") or obj.get("interval") or obj.get("period"))
        if not symbol or not timeframe:
            return
        for idx in range(length):
            candle = self._build_candle(
                symbol=symbol,
                timeframe=timeframe,
                ts_value=times[idx],
                open_value=opens[idx],
                high_value=highs[idx],
                low_value=lows[idx],
                close_value=closes[idx],
                volume_value=volumes[idx],
                source_url=source_url,
                transport=transport,
                captured_at=captured_at,
                raw_hash=raw_hash,
            )
            if candle:
                yield candle

    def _extract_dict_list_payload(
        self,
        obj: Any,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
        context_symbol: str | None,
        context_timeframe: str | None,
    ) -> Iterable[Candle]:
        if not isinstance(obj, list) or not obj or not all(isinstance(x, dict) for x in obj[: min(3, len(obj))]):
            return
        for item in obj:
            if not isinstance(item, dict):
                continue
            ts_value = self._first_present(item, ("timestamp", "time", "ts", "t", "datetime", "date", "openTime", "startTime"))
            open_value = self._first_present(item, ("open", "o", "openPrice"))
            high_value = self._first_present(item, ("high", "h", "highPrice"))
            low_value = self._first_present(item, ("low", "l", "lowPrice"))
            close_value = self._first_present(item, ("close", "c", "closePrice", "price"))
            volume_value = self._first_present(item, ("volume", "v", "vol", "amount", "quantity", "q"), default=0)
            symbol = self._normalize_symbol(self._first_present(item, ("symbol", "pair", "market", "s"))) or context_symbol
            timeframe = self._normalize_timeframe(self._first_present(item, ("timeframe", "tf", "interval", "resolution", "period"))) or context_timeframe
            if None in (ts_value, open_value, high_value, low_value, close_value) or not symbol or not timeframe:
                continue
            candle = self._build_candle(
                symbol=symbol,
                timeframe=timeframe,
                ts_value=ts_value,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                volume_value=volume_value,
                source_url=source_url,
                transport=transport,
                captured_at=captured_at,
                raw_hash=raw_hash,
            )
            if candle:
                yield candle

    def _extract_array_payload(
        self,
        obj: Any,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
        context_symbol: str | None,
        context_timeframe: str | None,
    ) -> Iterable[Candle]:
        if not isinstance(obj, list) or not obj:
            return
        rows = [row for row in obj if isinstance(row, list) and len(row) >= 5]
        if not rows:
            return
        inferred_tf = context_timeframe or self._infer_timeframe_from_rows(rows)
        symbol = context_symbol
        if not symbol or not inferred_tf:
            return
        for row in rows:
            parsed = self._parse_ohlcv_row(row)
            if not parsed:
                continue
            ts_value, open_value, high_value, low_value, close_value, volume_value = parsed
            candle = self._build_candle(
                symbol=symbol,
                timeframe=inferred_tf,
                ts_value=ts_value,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                volume_value=volume_value,
                source_url=source_url,
                transport=transport,
                captured_at=captured_at,
                raw_hash=raw_hash,
            )
            if candle:
                yield candle

    def _parse_ohlcv_row(self, row: list[Any]) -> tuple[Any, Any, Any, Any, Any, Any] | None:
        # Common formats: [ts, open, high, low, close, volume] or
        # [open, high, low, close, volume, ts]. Prefer timestamp-like column.
        if len(row) < 5:
            return None
        candidates = []
        if len(row) >= 6:
            candidates.append((row[0], row[1], row[2], row[3], row[4], row[5]))
            candidates.append((row[-1], row[0], row[1], row[2], row[3], row[4]))
        else:
            candidates.append((row[0], row[1], row[2], row[3], row[4], 0))
        for candidate in candidates:
            ts = self._parse_timestamp(candidate[0])
            if not ts:
                continue
            prices = [self._to_float(x) for x in candidate[1:5]]
            if all(x is not None for x in prices):
                volume = self._to_float(candidate[5]) or 0.0
                return (candidate[0], candidate[1], candidate[2], candidate[3], candidate[4], volume)
        return None

    def _build_candle(
        self,
        *,
        symbol: str,
        timeframe: str,
        ts_value: Any,
        open_value: Any,
        high_value: Any,
        low_value: Any,
        close_value: Any,
        volume_value: Any,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
    ) -> Candle | None:
        ts = self._parse_timestamp(ts_value)
        o = self._to_float(open_value)
        h = self._to_float(high_value)
        l = self._to_float(low_value)
        c = self._to_float(close_value)
        v = self._to_float(volume_value) or 0.0
        symbol = self._normalize_symbol(symbol) or ""
        timeframe = self._normalize_timeframe(timeframe) or ""
        if not ts or not symbol or not timeframe or None in (o, h, l, c):
            return None
        candle = Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
            volume=float(v),
            source_url=source_url,
            transport=transport,
            captured_at=captured_at,
            raw_hash=raw_hash,
        )
        return candle if self._is_valid_candle(candle) else None

    def _is_valid_candle(self, candle: Candle) -> bool:
        prices = (candle.open, candle.high, candle.low, candle.close)
        if any(pd.isna(x) for x in prices):
            return False
        if any(float(x) <= 0 for x in prices):
            return False
        if candle.high < max(candle.open, candle.low, candle.close):
            return False
        if candle.low > min(candle.open, candle.high, candle.close):
            return False
        if candle.volume < 0:
            return False
        if candle.symbol not in DEFAULT_SYMBOLS:
            return False
        if candle.timeframe not in TIMEFRAME_SECONDS:
            return False
        return True

    def _decode_payload(self, payload: bytes | str | dict[str, Any] | list[Any]) -> Any | None:
        if isinstance(payload, (dict, list)):
            return payload
        raw = self._payload_to_bytes(payload)
        raw = self._decompress_if_needed(raw)
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        frames = self._extract_tradingview_frames(text)
        if frames:
            return frames
        for candidate in (text, self._maybe_base64_decode(text)):
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except Exception:
                pass
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                return None
        return None

    def _extract_tradingview_frames(self, text: str) -> list[Any]:
        # TradingView socket frames are often packed as ~m~<length>~m~<json>
        frames: list[Any] = []
        pattern = re.compile(r"~m~(\d+)~m~")
        pos = 0
        while True:
            match = pattern.search(text, pos)
            if not match:
                break
            length = int(match.group(1))
            start = match.end()
            frame_text = text[start : start + length]
            pos = start + length
            if not frame_text:
                continue
            try:
                frames.append(json.loads(frame_text))
            except Exception:
                continue
        return frames

    def _decompress_if_needed(self, raw: bytes) -> bytes:
        for fn in (gzip.decompress, zlib.decompress):
            try:
                return fn(raw)
            except Exception:
                continue
        return raw

    def _maybe_base64_decode(self, text: str) -> str | None:
        if len(text) < 16 or not re.fullmatch(r"[A-Za-z0-9+/=\s]+", text):
            return None
        try:
            decoded = base64.b64decode(text, validate=False)
            return decoded.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def _payload_to_bytes(self, payload: bytes | str | dict[str, Any] | list[Any]) -> bytes:
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8", errors="ignore")
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8", errors="ignore")

    def _append_raw_payload(self, source_url: str, transport: str, raw_hash: str, decoded: Any, captured_at: datetime) -> None:
        try:
            with self.config.raw_payload_jsonl.open("a", encoding="utf-8") as fp:
                fp.write(
                    json.dumps(
                        {
                            "captured_at": captured_at.isoformat(),
                            "transport": transport,
                            "source_url": source_url,
                            "raw_hash": raw_hash,
                            "payload": decoded,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        except Exception as exc:
            LOGGER.warning("raw_payload_audit_write_failed error=%s", exc)


    def _extract_ticker_aggregated_candles(
        self,
        obj: Any,
        *,
        source_url: str,
        transport: str,
        captured_at: datetime,
        raw_hash: str,
        context_symbol: str | None,
    ) -> Iterable[Candle]:
        """Aggregate public ticker payloads into live OHLC candles.

        This is a fallback for Bitradex public pages when native kline payloads
        are not visible to an unauthenticated browser session. It only uses
        public market/ticker messages, never private account/order messages.
        """
        updates = list(self._extract_ticker_updates(obj, captured_at, context_symbol))
        for update in updates:
            symbol = update["symbol"]
            price = float(update["price"])
            ts = update["timestamp"]
            volume = float(update.get("volume") or 0.0)
            volume_delta = self._ticker_volume_delta(symbol, volume)
            for timeframe in self.config.timeframes:
                seconds = TIMEFRAME_SECONDS.get(timeframe)
                if not seconds:
                    continue
                bucket = self._floor_timestamp(ts, seconds)
                state_key = (symbol, timeframe, bucket.isoformat())
                state = self._ticker_candle_state.get(state_key)
                if state is None:
                    state = {
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": max(0.0, volume_delta),
                        "timestamp": bucket,
                    }
                else:
                    state["high"] = max(float(state["high"]), price)
                    state["low"] = min(float(state["low"]), price)
                    state["close"] = price
                    state["volume"] = max(0.0, float(state.get("volume") or 0.0) + max(0.0, volume_delta))
                self._ticker_candle_state[state_key] = state
                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bucket,
                    open=float(state["open"]),
                    high=float(state["high"]),
                    low=float(state["low"]),
                    close=float(state["close"]),
                    volume=float(state.get("volume") or 0.0),
                    source_url=source_url,
                    transport=transport,
                    captured_at=captured_at,
                    raw_hash=raw_hash,
                )
                if self._is_valid_candle(candle):
                    yield candle

    def _extract_ticker_updates(self, obj: Any, captured_at: datetime, context_symbol: str | None) -> Iterable[dict[str, Any]]:
        if isinstance(obj, dict):
            symbol = self._ticker_symbol_from_dict(obj) or self._normalize_symbol(context_symbol)
            price = self._ticker_price_from_dict(obj)
            if symbol and price is not None and symbol in self.config.symbols:
                ts = self._parse_timestamp(self._first_present(obj, ("timestamp", "time", "ts", "t", "createdAt", "updatedAt"))) or captured_at
                volume = self._ticker_volume_from_dict(obj) or 0.0
                yield {"symbol": symbol, "price": float(price), "timestamp": ts, "volume": float(volume)}
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    yield from self._extract_ticker_updates(value, captured_at, context_symbol)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    yield from self._extract_ticker_updates(value, captured_at, context_symbol)

    def _ticker_symbol_from_dict(self, item: dict[str, Any]) -> str | None:
        symbol_value = self._first_present(
            item,
            (
                "symbol", "s", "pair", "market", "contract", "contractName", "instrument", "instId",
                "name", "code", "topic", "channel", "ch", "stream",
            ),
        )
        if symbol_value is not None:
            symbol = self._normalize_symbol(symbol_value)
            if symbol:
                return symbol
        text = json.dumps(item, ensure_ascii=False, default=str)[:1200]
        for alias, symbol in SYMBOL_ALIASES.items():
            needle = alias.lower()
            compact = alias.lower().replace("/", "").replace("_", "").replace("-", "")
            lowered = text.lower()
            if needle in lowered or compact in lowered:
                return symbol
        return None

    def _ticker_price_from_dict(self, item: dict[str, Any]) -> float | None:
        # Prefer explicit last/mark/index prices. 'close'/'c' is accepted only as
        # fallback, after native candle extraction has already failed.
        keys = (
            "lastPrice", "last_price", "last", "latestPrice", "latest_price", "tradePrice",
            "price", "p", "markPrice", "mark_price", "indexPrice", "index_price",
            "fairPrice", "close", "c",
        )
        for key in keys:
            if key in item:
                value = self._to_float(item.get(key))
                if value is not None and value > 0:
                    return value
        return None

    def _ticker_volume_from_dict(self, item: dict[str, Any]) -> float | None:
        for key in ("volume", "vol", "v", "amount", "quoteVolume", "quote_volume", "baseVolume", "turnover"):
            if key in item:
                value = self._to_float(item.get(key))
                if value is not None and value >= 0:
                    return value
        return None

    def _ticker_volume_delta(self, symbol: str, current_volume: float) -> float:
        if current_volume <= 0:
            return 0.0
        previous = self._last_ticker_volume.get(symbol)
        self._last_ticker_volume[symbol] = current_volume
        if previous is None:
            return 0.0
        delta = current_volume - previous
        # Cumulative exchange volumes may reset. Do not create negative interval volume.
        return delta if delta > 0 else 0.0

    @staticmethod
    def _floor_timestamp(ts: datetime, seconds: int) -> datetime:
        ts = ts.astimezone(timezone.utc)
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % int(seconds))
        return datetime.fromtimestamp(bucket, tz=timezone.utc)

    def _looks_private(self, source_url: str, raw_bytes: bytes) -> bool:
        """Block private/account endpoints without discarding public market payloads.

        V1 inspected the first bytes of every payload. Bitradex public market
        responses may contain keys such as ``order`` or ``position`` inside
        public metadata, which caused safe public endpoints like
        /v1/future-u/market/v2/public/symbol/list to be skipped. For this
        collector the hard safety rule is URL-based: never process account,
        balance, order, position or explicit private/auth endpoints. Public
        market endpoints are safe to parse because we do not send credentials.
        """
        lowered = source_url.lower()
        blocked_url_fragments = (
            "authorization",
            "apikey",
            "api_key",
            "api-key",
            "/private/",
            "/account/",
            "/balance/",
            "/order/",
            "/position/",
            "spot/balance",
        )
        return any(fragment in lowered for fragment in blocked_url_fragments)

    def _looks_like_kline_source(self, source_url: str, obj: Any) -> bool:
        url_lower = source_url.lower()
        if any(hint in url_lower for hint in KLINE_URL_HINTS):
            return True
        if isinstance(obj, dict):
            keys = {str(k).lower() for k in obj.keys()}
            if {"t", "o", "h", "l", "c"}.issubset(keys):
                return True
            if any(k in keys for k in ("kline", "candles", "bars", "history", "data")):
                return True
        return False

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric > 10_000_000_000_000:
                numeric /= 1_000_000
            elif numeric > 10_000_000_000:
                numeric /= 1_000
            try:
                return datetime.fromtimestamp(numeric, tz=timezone.utc)
            except Exception:
                return None
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return self._parse_timestamp(float(text))
        parsed = pd.to_datetime(text, utc=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime().astimezone(timezone.utc)

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return float(value)
            except Exception:
                return None
        text = str(value).strip().replace(" ", "")
        if not text:
            return None
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        try:
            return float(text)
        except Exception:
            return None

    def _first_present(self, item: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
        for key in keys:
            if key in item and item[key] not in (None, ""):
                return item[key]
        return default

    def _normalize_symbol(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text in SYMBOL_ALIASES:
            return SYMBOL_ALIASES[text]
        normalized = text.upper().replace("/", "").replace("_", "").replace("-", "")
        return SYMBOL_ALIASES.get(normalized)

    def _normalize_timeframe(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        text = text.replace("minute", "m").replace("min", "m")
        return TIMEFRAME_ALIASES.get(text)

    def _symbol_from_url(self, source_url: str) -> str | None:
        lowered = source_url.lower()
        for alias, symbol in SYMBOL_ALIASES.items():
            if alias.lower().replace("/", "_").replace("-", "_") in lowered:
                return symbol
            if alias.lower().replace("/", "").replace("_", "").replace("-", "") in lowered:
                return symbol
        return None

    def _timeframe_from_url(self, source_url: str) -> str | None:
        parsed = urlparse(source_url)
        query = parse_qs(parsed.query)
        for key in ("resolution", "interval", "timeframe", "tf", "period", "granularity"):
            if key in query and query[key]:
                tf = self._normalize_timeframe(query[key][0])
                if tf:
                    return tf
        lowered = source_url.lower()
        for raw, tf in TIMEFRAME_ALIASES.items():
            if re.search(rf"(^|[^a-z0-9]){re.escape(raw)}([^a-z0-9]|$)", lowered):
                return tf
        return None

    def _infer_timeframe_from_rows(self, rows: list[list[Any]]) -> str | None:
        timestamps: list[datetime] = []
        for row in rows[:50]:
            parsed = self._parse_ohlcv_row(row)
            if not parsed:
                continue
            ts = self._parse_timestamp(parsed[0])
            if ts:
                timestamps.append(ts)
        if len(timestamps) < 2:
            return None
        timestamps = sorted(set(timestamps))
        diffs = [int((b - a).total_seconds()) for a, b in zip(timestamps, timestamps[1:]) if b > a]
        if not diffs:
            return None
        median = sorted(diffs)[len(diffs) // 2]
        nearest = min(TIMEFRAME_SECONDS.items(), key=lambda kv: abs(kv[1] - median))
        return nearest[0] if abs(nearest[1] - median) <= max(5, nearest[1] * 0.1) else None
