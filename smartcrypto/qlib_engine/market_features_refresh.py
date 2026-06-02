from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smartcrypto.data.feature_builder import build_market_features
from smartcrypto.market.market_feature_schema import (
    sanitize_operational_market_features,
    write_operational_market_features,
)
from smartcrypto.qlib_engine.common import write_json


REQUIRED_RAW_COLUMNS = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
REQUIRED_FEATURE_COLUMNS = {
    "symbol",
    "pair",
    "tf",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ret_1",
    "ema_20",
    "rsi_14",
    "market_regime",
}


def refresh_qlib_market_features(
    *,
    source_path: str | Path = "data/raw/futures_ohlcv_60d.parquet",
    existing_features_path: str | Path = "data/features/market_features_60d.parquet",
    output_path: str | Path = "data/features/market_features_60d.parquet",
    report_path: str | Path = "data/reports/qlib_market_features_refresh_report.json",
    symbols: list[str] | None = None,
    timeframe: str = "5m",
    max_source_age_minutes: int | float = 15,
    public_download_enabled: bool = True,
    public_download_lookback_candles: int = 1500,
    raw_recent_output_path: str | Path = "data/raw/qlib_market_features_refresh_recent.parquet",
    base_url: str = "https://fapi.binance.com",
    endpoint: str = "/fapi/v1/klines",
    request_sleep_seconds: float = 0.15,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh Qlib market features with recent public OHLCV data.

    This is paper/shadow infrastructure only. It uses public candle data, never
    private exchange endpoints, and only writes runtime files under ignored paths.
    """
    current = _normalize_now(now)
    symbols = symbols or ["BTCUSDT", "ETHUSDT"]
    source = Path(source_path)
    existing = Path(existing_features_path)
    output = Path(output_path)
    report_file = Path(report_path)

    source_report = inspect_market_feature_source(
        existing if existing.exists() else source,
        max_source_age_minutes=max_source_age_minutes,
        now=current,
    )

    recent_raw: pd.DataFrame | None = None
    public_download_report: dict[str, Any] = {
        "enabled": bool(public_download_enabled),
        "status": "skipped",
        "reason": None,
        "rows": 0,
    }
    if public_download_enabled:
        try:
            recent_raw = fetch_public_market_ohlcv(
                symbols=symbols,
                timeframe=timeframe,
                lookback_candles=public_download_lookback_candles,
                base_url=base_url,
                endpoint=endpoint,
                request_sleep_seconds=request_sleep_seconds,
                now=current,
            )
            raw_recent_path = Path(raw_recent_output_path)
            raw_recent_path.parent.mkdir(parents=True, exist_ok=True)
            recent_raw.to_parquet(raw_recent_path, index=False)
            public_download_report.update(
                {
                    "status": "ok",
                    "rows": int(len(recent_raw)),
                    "max_timestamp": _max_timestamp(recent_raw),
                    "raw_recent_output_path": str(raw_recent_path),
                }
            )
        except Exception as exc:
            public_download_report.update({"status": "blocked", "reason": str(exc)})

    if recent_raw is None:
        if not source.exists():
            report = _blocked_report(
                reason="missing_source",
                source_path=source,
                existing_features_path=existing,
                output_path=output,
                report_path=report_file,
                source_report=source_report,
                public_download_report=public_download_report,
                current=current,
            )
            write_json(report_file, report)
            return report
        validation_error = validate_raw_market_schema(source)
        if validation_error:
            report = _blocked_report(
                reason="invalid_schema",
                source_path=source,
                existing_features_path=existing,
                output_path=output,
                report_path=report_file,
                source_report={**source_report, "schema_error": validation_error},
                public_download_report=public_download_report,
                current=current,
            )
            write_json(report_file, report)
            return report
        recent_raw = pd.read_parquet(source)

    recent_features = _build_features_from_raw(recent_raw, output)
    final_features = recent_features
    if existing.exists():
        existing_features = pd.read_parquet(existing)
        final_features = pd.concat([existing_features, recent_features], ignore_index=True, sort=False)

    operational_candidate = _dedupe_features(final_features)
    final_features, schema_report = sanitize_operational_market_features(operational_candidate)
    schema_error = validate_feature_schema(final_features)
    if not schema_report["operational_feature_schema_ok"]:
        schema_error = f"operational_lookahead_columns:{schema_report['lookahead_columns']}"
    if schema_error:
        report = _blocked_report(
            reason="invalid_schema",
            source_path=source,
            existing_features_path=existing,
            output_path=output,
            report_path=report_file,
            source_report={**source_report, "schema_error": schema_error},
            public_download_report=public_download_report,
            schema_report=schema_report,
            current=current,
        )
        write_json(report_file, report)
        return report

    max_timestamp = _max_timestamp(final_features)
    max_age = _age_minutes(max_timestamp, current) if max_timestamp else None
    if max_age is None or max_age > float(max_source_age_minutes):
        report = _status_report(
            status="blocked",
            reason="stale_source",
            source_path=source,
            existing_features_path=existing,
            output_path=output,
            report_path=report_file,
            final_features=final_features,
            max_timestamp=max_timestamp,
            max_age=max_age,
            max_source_age_minutes=max_source_age_minutes,
            source_report=source_report,
            public_download_report=public_download_report,
            schema_report=schema_report,
            current=current,
        )
        write_json(report_file, report)
        return report

    final_features, schema_report = write_operational_market_features(operational_candidate, output)
    report = _status_report(
        status="ok",
        reason=None,
        source_path=source,
        existing_features_path=existing,
        output_path=output,
        report_path=report_file,
        final_features=final_features,
        max_timestamp=max_timestamp,
        max_age=max_age,
        max_source_age_minutes=max_source_age_minutes,
        source_report=source_report,
        public_download_report=public_download_report,
        schema_report=schema_report,
        current=current,
    )
    write_json(report_file, report)
    return report


def inspect_market_feature_source(
    path: str | Path,
    *,
    max_source_age_minutes: int | float = 15,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _normalize_now(now)
    target = Path(path)
    report: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "rows": 0,
        "max_timestamp": None,
        "age_minutes": None,
        "max_source_age_minutes": float(max_source_age_minutes),
        "status": "missing_source",
    }
    if not target.exists():
        return report
    try:
        frame = pd.read_parquet(target)
        report["rows"] = int(len(frame))
        max_timestamp = _max_timestamp(frame)
        age = _age_minutes(max_timestamp, current) if max_timestamp else None
        report.update(
            {
                "max_timestamp": max_timestamp,
                "age_minutes": age,
                "status": "ok" if age is not None and age <= float(max_source_age_minutes) else "stale_source",
            }
        )
        return report
    except Exception as exc:
        report["status"] = "invalid_schema"
        report["error"] = str(exc)
        return report


def validate_raw_market_schema(path: str | Path) -> str | None:
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return f"unreadable_source:{exc}"
    missing = sorted(REQUIRED_RAW_COLUMNS.difference(frame.columns))
    if missing:
        return f"missing_raw_columns:{missing}"
    if frame.empty:
        return "empty_source"
    return None


def validate_feature_schema(frame: pd.DataFrame) -> str | None:
    missing = sorted(REQUIRED_FEATURE_COLUMNS.difference(frame.columns))
    if missing:
        return f"missing_feature_columns:{missing}"
    if frame.empty:
        return "empty_features"
    return None


def fetch_public_market_ohlcv(
    *,
    symbols: list[str],
    timeframe: str,
    lookback_candles: int,
    base_url: str,
    endpoint: str,
    request_sleep_seconds: float,
    now: datetime | None = None,
) -> pd.DataFrame:
    current = _normalize_now(now)
    interval_ms = _interval_to_ms(timeframe)
    end_ms = int((current - timedelta(milliseconds=interval_ms)).timestamp() * 1000)
    start_ms = end_ms - int(lookback_candles * interval_ms)
    frames = []
    for symbol in symbols:
        rows = _fetch_klines(
            base_url=base_url,
            endpoint=endpoint,
            symbol=symbol,
            interval=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=min(max(lookback_candles, 1), 1500),
        )
        frames.append(_normalize_public_klines(rows, symbol, timeframe))
        time.sleep(max(0.0, request_sleep_seconds))
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=sorted(REQUIRED_RAW_COLUMNS))
    if combined.empty:
        raise RuntimeError("public_market_source_empty")
    return combined.drop_duplicates(subset=["symbol", "tf", "ts"]).sort_values(["symbol", "tf", "ts"])


def _fetch_klines(
    *,
    base_url: str,
    endpoint: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int,
) -> list[list[Any]]:
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit}
    request = Request(f"{base_url.rstrip('/')}{endpoint}?{urlencode(params)}", headers={"User-Agent": "smartcrypto-qlib-refresh/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict):
        raise RuntimeError(f"public_market_api_error:{payload}")
    if not isinstance(payload, list):
        raise RuntimeError("public_market_api_invalid_response")
    return payload


def _normalize_public_klines(rows: list[list[Any]], symbol: str, timeframe: str) -> pd.DataFrame:
    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "number_of_trades",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return pd.DataFrame(columns=sorted(REQUIRED_RAW_COLUMNS))
    frame["symbol"] = symbol
    frame["pair"] = symbol.replace("USDT", "/USDT:USDT")
    frame["tf"] = timeframe
    frame["ts"] = pd.to_datetime(pd.to_numeric(frame["open_time"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[["symbol", "pair", "tf", "ts", "open", "high", "low", "close", "volume"]].dropna()


def _build_features_from_raw(raw: pd.DataFrame, output: Path) -> pd.DataFrame:
    temp_raw_path = output.with_suffix(output.suffix + ".refresh_raw.tmp.parquet")
    temp_features_path = output.with_suffix(output.suffix + ".refresh_features.tmp.parquet")
    temp_raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(temp_raw_path, index=False)
    try:
        return build_market_features(temp_raw_path, temp_features_path)
    finally:
        temp_raw_path.unlink(missing_ok=True)
        temp_features_path.unlink(missing_ok=True)


def _dedupe_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["ts"] = pd.to_datetime(result["ts"], utc=True, errors="coerce")
    result = result.dropna(subset=["symbol", "tf", "ts"]).copy()
    return (
        result.drop_duplicates(subset=["symbol", "tf", "ts"], keep="last")
        .sort_values(["symbol", "tf", "ts"])
        .reset_index(drop=True)
    )


def _max_timestamp(frame: pd.DataFrame | None) -> str | None:
    if frame is None or frame.empty or "ts" not in frame.columns:
        return None
    parsed = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dropna()
    if parsed.empty:
        return None
    return parsed.max().to_pydatetime().astimezone(timezone.utc).isoformat()


def _age_minutes(timestamp: str | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    parsed = pd.to_datetime(timestamp, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(max(0.0, (now - parsed.to_pydatetime()).total_seconds() / 60.0))


def _interval_to_ms(value: str) -> int:
    text = str(value).strip().lower()
    if text.endswith("m"):
        return int(text[:-1]) * 60_000
    if text.endswith("h"):
        return int(text[:-1]) * 60 * 60_000
    raise ValueError(f"unsupported_timeframe:{value}")


def _normalize_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc) if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _blocked_report(
    *,
    reason: str,
    source_path: Path,
    existing_features_path: Path,
    output_path: Path,
    report_path: Path,
    source_report: dict[str, Any],
    public_download_report: dict[str, Any],
    schema_report: dict[str, Any] | None = None,
    current: datetime,
) -> dict[str, Any]:
    schema_report = schema_report or {
        "output_schema_status": "unknown",
        "operational_feature_schema_ok": False,
        "lookahead_columns": [],
        "lookahead_columns_count": 0,
        "lookahead_columns_removed": [],
        "lookahead_columns_removed_count": 0,
        "labels_output_path": None,
    }
    return {
        "status": "blocked",
        "reason": reason,
        "source_path": str(source_path),
        "existing_features_path": str(existing_features_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "rows": 0,
        "market_features_rows": 0,
        "market_features_max_timestamp": None,
        "market_features_age_minutes": None,
        "source_report": source_report,
        "public_download": public_download_report,
        **schema_report,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": current.isoformat(),
    }


def _status_report(
    *,
    status: str,
    reason: str | None,
    source_path: Path,
    existing_features_path: Path,
    output_path: Path,
    report_path: Path,
    final_features: pd.DataFrame,
    max_timestamp: str | None,
    max_age: float | None,
    max_source_age_minutes: int | float,
    source_report: dict[str, Any],
    public_download_report: dict[str, Any],
    schema_report: dict[str, Any],
    current: datetime,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "source_path": str(source_path),
        "existing_features_path": str(existing_features_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "rows": int(len(final_features)),
        "market_features_rows": int(len(final_features)),
        "market_features_max_timestamp": max_timestamp,
        "market_features_age_minutes": max_age,
        "max_source_age_minutes": float(max_source_age_minutes),
        "symbols": sorted(final_features["symbol"].dropna().astype(str).unique().tolist()) if "symbol" in final_features.columns else [],
        "timeframes": sorted(final_features["tf"].dropna().astype(str).unique().tolist()) if "tf" in final_features.columns else [],
        "source_report": source_report,
        "public_download": public_download_report,
        **schema_report,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": current.isoformat(),
    }
