from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

import pandas as pd

BINANCE_USDM_FUTURES_BASE_URL = "https://fapi.binance.com"
BINANCE_USDM_AGGTRADES_PATH = "/fapi/v1/aggTrades"
BINANCE_PUBLIC_DATA_BASE_URL = "https://data.binance.vision"
SOURCE_INTERVAL = "aggTrades"
TARGET_TIMEFRAME = "15s"
MAX_LIMIT = 1000
REST_HISTORICAL_FALLBACK_MAX_AGE_DAYS = 7
AGGTRADE_COLUMNS = [
    "aggregate_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "timestamp_ms",
    "buyer_is_maker",
]
SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
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
}
SourceMode = Literal["archive", "rest", "archive_then_rest"]


@dataclass(frozen=True)
class DownloadConfig:
    project_root: Path
    symbols: tuple[str, ...]
    from_date: date
    to_date: date
    output_dir: Path
    limit: int = MAX_LIMIT
    request_sleep_seconds: float = 0.12
    timeout_seconds: float = 30.0
    max_retries: int = 5
    no_write: bool = False
    source_mode: SourceMode = "archive_then_rest"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_iso_date(value: str) -> date:
    try:
        return datetime.fromisoformat(value.strip()).date()
    except ValueError as exc:
        raise ValueError(f"invalid_date:{value}") from exc


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def to_millis(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def normalize_symbols(raw_symbols: Iterable[str]) -> tuple[str, ...]:
    symbols = tuple(dict.fromkeys(symbol.strip().upper().replace("/", "") for symbol in raw_symbols if symbol.strip()))
    if not symbols:
        raise ValueError("at_least_one_symbol_required")
    return symbols


def normalize_source_mode(value: str) -> SourceMode:
    mode = value.strip().lower().replace("-", "_")
    if mode not in {"archive", "rest", "archive_then_rest"}:
        raise ValueError(f"invalid_source_mode:{value}")
    return mode  # type: ignore[return-value]


def build_aggtrades_rest_url(symbol: str, start_ms: int, end_ms: int, limit: int) -> str:
    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": max(start_ms, end_ms - 1),
            "limit": limit,
        }
    )
    return f"{BINANCE_USDM_FUTURES_BASE_URL}{BINANCE_USDM_AGGTRADES_PATH}?{query}"


def build_archive_url(symbol: str, day: date) -> str:
    stamp = day.strftime("%Y-%m-%d")
    return (
        f"{BINANCE_PUBLIC_DATA_BASE_URL}/data/futures/um/daily/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{stamp}.zip"
    )


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        payload = exc.read().decode("utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        payload = ""
    return payload.strip()[:500]


def fetch_bytes_url(url: str, timeout_seconds: float, *, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SMART-FUTUROS-research-only-binance-public-data/4.0",
            "Accept": accept,
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - public market data only
        return response.read()


def fetch_json_url(url: str, timeout_seconds: float) -> Any:
    payload = fetch_bytes_url(url, timeout_seconds, accept="application/json").decode("utf-8")
    return json.loads(payload)


def fetch_archive_zip(symbol: str, day: date, *, timeout_seconds: float, max_retries: int, request_sleep_seconds: float) -> bytes:
    url = build_archive_url(symbol, day)
    last_error: str | None = None
    for attempt in range(max_retries):
        try:
            return fetch_bytes_url(url, timeout_seconds, accept="application/zip,application/octet-stream,*/*")
        except urllib.error.HTTPError as exc:
            body = _read_http_error_body(exc)
            if exc.code == 404:
                raise FileNotFoundError(f"archive_not_found:{symbol}:{day.isoformat()}:{url}") from exc
            last_error = f"HTTPError:{exc.code}:{body or exc.reason}"
        except (OSError, TimeoutError) as exc:
            last_error = f"{type(exc).__name__}:{exc}"
        time.sleep(min(3.0, request_sleep_seconds * (attempt + 1) * 2.0))
    raise RuntimeError(f"binance_archive_fetch_failed:{symbol}:{day.isoformat()}:{last_error}")


def _read_csv_from_archive(zip_bytes: bytes, symbol: str, day: date) -> pd.DataFrame:
    expected_fragment = f"{symbol}-aggTrades-{day.strftime('%Y-%m-%d')}"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        candidates = [name for name in archive.namelist() if name.endswith(".csv") and expected_fragment in Path(name).name]
        if not candidates:
            candidates = [name for name in archive.namelist() if name.endswith(".csv")]
        if not candidates:
            raise ValueError(f"archive_csv_not_found:{symbol}:{day.isoformat()}")
        with archive.open(candidates[0]) as handle:
            raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    first_line = next(csv.reader([lines[0]]), []) if lines else []
    has_header = bool(first_line and not str(first_line[0]).strip().lstrip("-").isdigit())
    frame = pd.read_csv(io.StringIO(text), header=0 if has_header else None)
    if not has_header:
        frame.columns = AGGTRADE_COLUMNS[: len(frame.columns)]
    return frame


def archive_aggtrades_to_dataframe(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = frame.copy()
    if len(normalized.columns) >= len(AGGTRADE_COLUMNS) and not {
        "aggregate_trade_id",
        "agg_trade_id",
        "a",
    }.intersection({str(column).strip().lower() for column in normalized.columns}):
        normalized = normalized.iloc[:, : len(AGGTRADE_COLUMNS)].copy()
        normalized.columns = AGGTRADE_COLUMNS
    rename_map = {column: str(column).strip().lower().replace(" ", "_") for column in normalized.columns}
    normalized = normalized.rename(columns=rename_map)
    aliases = {
        "agg_trade_id": "aggregate_trade_id",
        "aggtradeid": "aggregate_trade_id",
        "aggregate_tradeid": "aggregate_trade_id",
        "a": "aggregate_trade_id",
        "p": "price",
        "q": "quantity",
        "f": "first_trade_id",
        "l": "last_trade_id",
        "t": "timestamp_ms",
        "time": "timestamp_ms",
        "transact_time": "timestamp_ms",
        "transact_time_ms": "timestamp_ms",
        "transaction_time": "timestamp_ms",
        "trade_time": "timestamp_ms",
        "t": "timestamp_ms",
        "T": "timestamp_ms",
        "m": "buyer_is_maker",
        "is_buyer_maker": "buyer_is_maker",
        "is_buyer_the_maker": "buyer_is_maker",
        "buyer_maker": "buyer_is_maker",
        "was_the_buyer_the_maker": "buyer_is_maker",
    }
    normalized = normalized.rename(columns={k: v for k, v in aliases.items() if k in normalized.columns})
    missing = [column for column in AGGTRADE_COLUMNS if column not in normalized.columns]
    if missing:
        raise ValueError(f"archive_missing_aggtrade_columns:{symbol}:{missing}")
    normalized = normalized[AGGTRADE_COLUMNS].copy()
    normalized.insert(0, "symbol", symbol)
    return _coerce_aggtrade_dataframe(normalized)


def _coerce_aggtrade_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    coerced = frame.copy()
    coerced["price"] = pd.to_numeric(coerced["price"], errors="coerce")
    coerced["quantity"] = pd.to_numeric(coerced["quantity"], errors="coerce")
    coerced["aggregate_trade_id"] = pd.to_numeric(coerced["aggregate_trade_id"], errors="coerce").astype("Int64")
    coerced["first_trade_id"] = pd.to_numeric(coerced["first_trade_id"], errors="coerce").astype("Int64")
    coerced["last_trade_id"] = pd.to_numeric(coerced["last_trade_id"], errors="coerce").astype("Int64")
    coerced["timestamp_ms"] = pd.to_numeric(coerced["timestamp_ms"], errors="coerce").astype("Int64")
    coerced["buyer_is_maker"] = coerced["buyer_is_maker"].map(_coerce_bool)
    coerced["timestamp"] = pd.to_datetime(coerced["timestamp_ms"], unit="ms", utc=True, errors="coerce")
    return coerced.dropna(subset=["timestamp", "price", "quantity"]).sort_values(["symbol", "timestamp", "aggregate_trade_id"]).reset_index(drop=True)


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def aggtrades_json_to_dataframe(rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["symbol", *AGGTRADE_COLUMNS, "timestamp"])
    frame = pd.DataFrame(rows)
    frame = frame.rename(
        columns={
            "a": "aggregate_trade_id",
            "p": "price",
            "q": "quantity",
            "f": "first_trade_id",
            "l": "last_trade_id",
            "T": "timestamp_ms",
            "m": "buyer_is_maker",
        }
    )
    for column in AGGTRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[AGGTRADE_COLUMNS].copy()
    frame.insert(0, "symbol", symbol)
    return _coerce_aggtrade_dataframe(frame)


def resample_aggtrades_to_15s(aggtrades: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "timestamp", "price", "quantity", "buyer_is_maker", "first_trade_id", "last_trade_id"}
    missing = sorted(required.difference(aggtrades.columns))
    if missing:
        raise ValueError(f"missing_aggtrade_columns:{missing}")
    if aggtrades.empty:
        return pd.DataFrame(columns=_target_columns())
    frame = aggtrades.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp", "price", "quantity"]).sort_values(["symbol", "timestamp"])
    frame["quote_asset_volume_raw"] = frame["price"] * frame["quantity"]
    frame["taker_buy_base_asset_volume_raw"] = frame["quantity"].where(frame["buyer_is_maker"] == False, 0.0)  # noqa: E712
    frame["taker_buy_quote_asset_volume_raw"] = frame["quote_asset_volume_raw"].where(frame["buyer_is_maker"] == False, 0.0)  # noqa: E712
    trade_count = (frame["last_trade_id"].astype("Int64") - frame["first_trade_id"].astype("Int64") + 1).astype("Float64")
    frame["number_of_trades_raw"] = trade_count.where(trade_count > 0, 1).fillna(1)
    output_frames: list[pd.DataFrame] = []
    for symbol, symbol_frame in frame.groupby("symbol", sort=True):
        indexed = symbol_frame.set_index("timestamp").sort_index()
        resampled = indexed.resample("15s", label="left", closed="left").agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            quote_asset_volume=("quote_asset_volume_raw", "sum"),
            number_of_agg_trades=("aggregate_trade_id", "count"),
            number_of_trades=("number_of_trades_raw", "sum"),
            taker_buy_base_asset_volume=("taker_buy_base_asset_volume_raw", "sum"),
            taker_buy_quote_asset_volume=("taker_buy_quote_asset_volume_raw", "sum"),
        )
        resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
        resampled.insert(0, "symbol", symbol)
        output_frames.append(resampled)
    if not output_frames:
        return pd.DataFrame(columns=_target_columns())
    result = pd.concat(output_frames, ignore_index=True)
    result.insert(1, "timeframe", TARGET_TIMEFRAME)
    result["timestamp_ms"] = (result["timestamp"].astype("int64") // 1_000_000).astype("int64")
    result["source_interval"] = SOURCE_INTERVAL
    result["source"] = "binance_usdm_futures_public_aggtrades_resampled_15s"
    result["generated_at_utc"] = utc_now_iso()
    for column in _target_columns():
        if column not in result.columns:
            result[column] = pd.NA
    return result[_target_columns()].sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def _target_columns() -> list[str]:
    return [
        "symbol",
        "timeframe",
        "timestamp",
        "timestamp_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_agg_trades",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
        "source_interval",
        "source",
        "generated_at_utc",
    ]


def _summarize_day(symbol: str, day: date, source: str, aggtrades: pd.DataFrame, fifteen_seconds: pd.DataFrame) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "source": source,
        "source_aggtrade_rows": int(len(aggtrades)),
        "target_15s_rows": int(len(fifteen_seconds)),
        "expected_15s_rows_for_full_day": 5760,
        "full_day_15s_coverage": bool(len(fifteen_seconds) >= 5760),
        "min_timestamp_utc": None if fifteen_seconds.empty else fifteen_seconds["timestamp"].min().isoformat().replace("+00:00", "Z"),
        "max_timestamp_utc": None if fifteen_seconds.empty else fifteen_seconds["timestamp"].max().isoformat().replace("+00:00", "Z"),
    }


def fetch_aggtrades_page(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int,
    timeout_seconds: float,
    max_retries: int,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"invalid_limit:{limit}")
    url = build_aggtrades_rest_url(symbol=symbol, start_ms=start_ms, end_ms=end_ms, limit=limit)
    last_error: str | None = None
    for attempt in range(max_retries):
        try:
            payload = fetch_json_url(url, timeout_seconds=timeout_seconds)
            if not isinstance(payload, list):
                raise ValueError(f"unexpected_payload_type:{type(payload).__name__}")
            return payload
        except urllib.error.HTTPError as exc:
            body = _read_http_error_body(exc)
            last_error = f"HTTPError:{exc.code}:{body or exc.reason};url={url}"
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}:{exc};url={url}"
        time.sleep(min(3.0, request_sleep_seconds * (attempt + 1) * 2.0))
    raise RuntimeError(f"binance_rest_aggtrades_fetch_failed:{symbol}:{last_error}")


def download_symbol_day_rest(symbol: str, start: datetime, end: datetime, config: DownloadConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    start_ms = to_millis(start)
    end_ms = to_millis(end)
    cursor_ms = start_ms
    pages = 0
    raw_rows: list[dict[str, Any]] = []
    one_hour_ms = 60 * 60 * 1000
    while cursor_ms < end_ms:
        bounded_end_ms = min(end_ms, cursor_ms + one_hour_ms - 1)
        page = fetch_aggtrades_page(
            symbol=symbol,
            start_ms=cursor_ms,
            end_ms=bounded_end_ms,
            limit=config.limit,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            request_sleep_seconds=config.request_sleep_seconds,
        )
        pages += 1
        if not page:
            cursor_ms = bounded_end_ms + 1
            continue
        raw_rows.extend(page)
        last_ts = max(int(row.get("T", cursor_ms)) for row in page if isinstance(row, dict))
        cursor_ms = max(last_ts + 1, bounded_end_ms + 1)
        time.sleep(config.request_sleep_seconds)
    aggtrades = aggtrades_json_to_dataframe(raw_rows, symbol=symbol)
    fifteen_seconds = resample_aggtrades_to_15s(aggtrades)
    summary = _summarize_day(symbol, start.date(), "rest_fapi_v1_aggtrades", aggtrades, fifteen_seconds)
    summary["pages"] = pages
    summary["rest_warning"] = "REST aggTrades is fallback-only; archive is required for historical full-day backfills."
    return fifteen_seconds, summary


def download_symbol_day_archive(symbol: str, day: date, config: DownloadConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    zip_bytes = fetch_archive_zip(
        symbol,
        day,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        request_sleep_seconds=config.request_sleep_seconds,
    )
    raw_frame = _read_csv_from_archive(zip_bytes, symbol, day)
    aggtrades = archive_aggtrades_to_dataframe(raw_frame, symbol)
    fifteen_seconds = resample_aggtrades_to_15s(aggtrades)
    summary = _summarize_day(symbol, day, "archive_data_binance_vision_aggtrades", aggtrades, fifteen_seconds)
    summary["archive_url"] = build_archive_url(symbol, day)
    return fifteen_seconds, summary


def download_symbol_day(symbol: str, start: datetime, end: datetime, config: DownloadConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    day = start.date()
    archive_error: str | None = None
    if config.source_mode in {"archive", "archive_then_rest"}:
        try:
            return download_symbol_day_archive(symbol, day, config)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            archive_error = f"{type(exc).__name__}:{exc}"
            if config.source_mode == "archive":
                raise RuntimeError(f"archive_source_failed:{symbol}:{day.isoformat()}:{archive_error}") from exc
            max_rest_day = datetime.now(UTC).date() - timedelta(days=REST_HISTORICAL_FALLBACK_MAX_AGE_DAYS)
            if day < max_rest_day:
                raise RuntimeError(
                    f"archive_required_for_historical_aggtrades:{symbol}:{day.isoformat()}:{archive_error}"
                ) from exc
    if config.source_mode in {"rest", "archive_then_rest"}:
        frame, summary = download_symbol_day_rest(symbol, start, end, config)
        if archive_error:
            summary["archive_fallback_reason"] = archive_error
        return frame, summary
    raise ValueError(f"unsupported_source_mode:{config.source_mode}")


def write_daily_output(frame: pd.DataFrame, symbol: str, day: date, output_dir: Path) -> Path:
    symbol_dir = output_dir / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    output = symbol_dir / f"{symbol}_15s_{day.strftime('%Y%m%d')}.parquet"
    frame.to_parquet(output, index=False)
    return output


def run_download(config: DownloadConfig) -> dict[str, Any]:
    if config.to_date < config.from_date:
        raise ValueError("to_date_before_from_date")
    result: dict[str, Any] = {
        "schema_version": "binance_futures_aggtrades_to_15s_redownload_v4",
        "status": "ok",
        "reason": "download_completed" if not config.no_write else "no_write_preflight_ok",
        "generated_at_utc": utc_now_iso(),
        "project_root": str(config.project_root),
        "output_dir": str(config.output_dir),
        "symbols": list(config.symbols),
        "from_date": config.from_date.isoformat(),
        "to_date": config.to_date.isoformat(),
        "source_interval": SOURCE_INTERVAL,
        "target_timeframe": TARGET_TIMEFRAME,
        "source_mode": config.source_mode,
        "rest_endpoint": f"{BINANCE_USDM_FUTURES_BASE_URL}{BINANCE_USDM_AGGTRADES_PATH}",
        "archive_base_url": BINANCE_PUBLIC_DATA_BASE_URL,
        "no_write": config.no_write,
        "days": [],
        "written_files": [],
        "validation_errors": [],
        "warnings": [],
        **SAFETY_FLAGS,
    }
    day = config.from_date
    while day <= config.to_date:
        start, end = day_bounds_utc(day)
        for symbol in config.symbols:
            if config.no_write:
                result["days"].append(
                    {
                        "symbol": symbol,
                        "date": day.isoformat(),
                        "status": "planned",
                        "source": "binance_usdm_futures_public_aggtrades_resampled_15s",
                        "archive_url": build_archive_url(symbol, day),
                    }
                )
                continue
            try:
                frame, summary = download_symbol_day(symbol=symbol, start=start, end=end, config=config)
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                error = f"download_failed:{symbol}:{day.isoformat()}:{type(exc).__name__}:{exc}"
                result["validation_errors"].append(error)
                result["days"].append({"symbol": symbol, "date": day.isoformat(), "status": "blocked", "reason": error})
                continue
            if frame.empty:
                summary["status"] = "warning"
                summary["reason"] = "empty_day_from_public_binance"
                result["warnings"].append(f"empty_day:{symbol}:{day.isoformat()}")
            else:
                output = write_daily_output(frame, symbol, day, config.output_dir)
                summary["status"] = "ok"
                summary["output"] = str(output)
                result["written_files"].append(str(output))
                if not summary.get("full_day_15s_coverage"):
                    result["warnings"].append(f"partial_15s_day:{symbol}:{day.isoformat()}")
            result["days"].append(summary)
        day += timedelta(days=1)
    if result["validation_errors"]:
        result["status"] = "blocked"
        result["reason"] = "download_validation_errors"
    elif result["warnings"]:
        result["status"] = "warning"
        result["reason"] = "download_completed_with_warnings"
    return result
