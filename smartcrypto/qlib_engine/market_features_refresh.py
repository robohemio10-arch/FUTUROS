from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smartcrypto.data.feature_builder import build_market_feature_frame
from smartcrypto.execution.freqtrade_contract import freqtrade_pair, internal_symbol
from smartcrypto.market.market_feature_schema import (
    lookahead_columns,
    sanitize_operational_market_features,
    write_operational_market_features,
)
from smartcrypto.qlib_engine.common import write_json


FEATURE_KEY_COLUMNS = ("symbol", "tf", "ts")
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

# Features whose post-warm-up continuity must never regress back to null. These are
# deliberately chosen from numerically stable indicators. RSI and volume z-score are
# excluded from the hard continuity guard because their mathematical denominator can
# legitimately be zero for degenerate windows.
CONTINUITY_GUARD_FEATURE_COLUMNS = (
    "ret_30",
    "ema_200",
    "dist_ema200",
    "macd_hist",
    "atr_pct_14",
    "vol_120",
    "volume_mean_120",
    "trend_score",
)

# A clean refresh always recalculates the complete historical raw series for every
# affected symbol/timeframe group. This is intentional: truncating the recalculation
# window restarts recursive indicators such as EMA and can create numerically drifted
# features even when no value is null. For the current BTC/ETH paper universe the
# bounded 60-day history is small enough that correctness is preferable to a tail
# approximation.
AFFECTED_GROUP_REBUILD_POLICY = "full_history"


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
    """Refresh Qlib market features without allowing warm-up null regressions.

    The runtime artifact itself contains the raw OHLCV columns required to rebuild
    indicators. The refresh therefore treats existing OHLCV, the configured raw
    source, and the newly downloaded public candles as one raw continuity basis.

    When the current artifact already contains post-warm-up null scars, the complete
    feature history is rematerialized once from that raw basis. Otherwise every
    symbol/timeframe touched by the refresh is recalculated from its complete available
    raw history. This prevents both warm-up null regressions and recursive-indicator
    restart drift (notably EMA200). Unaffected groups are retained as-is.

    This remains paper/shadow infrastructure only. It only uses public market data,
    never accesses private exchange endpoints, and never submits orders or mutates
    risk/model authority.
    """

    current = _normalize_now(now)
    normalized_symbols = [internal_symbol(item) for item in (symbols or ["BTCUSDT", "ETHUSDT"])]
    source = Path(source_path)
    existing = Path(existing_features_path)
    output = Path(output_path)
    report_file = Path(report_path)

    existing_features = _read_existing_features(existing)
    existing_continuity = inspect_feature_continuity(existing_features)
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
                symbols=normalized_symbols,
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
            public_download_report.update(
                {
                    "status": "blocked",
                    "reason": f"{type(exc).__name__}:{exc}",
                }
            )

    source_raw, source_schema_error = _load_raw_source(source)
    if recent_raw is None:
        if source_raw is None:
            reason = "missing_source" if not source.exists() else "invalid_schema"
            report = _blocked_report(
                reason=reason,
                source_path=source,
                existing_features_path=existing,
                output_path=output,
                report_path=report_file,
                source_report={
                    **source_report,
                    **({"schema_error": source_schema_error} if source_schema_error else {}),
                },
                public_download_report=public_download_report,
                existing_continuity=existing_continuity,
                current=current,
            )
            write_json(report_file, report)
            return report
        recent_raw = source_raw.copy()

    try:
        raw_basis, raw_basis_report = _build_raw_continuity_basis(
            existing_features=existing_features,
            source_raw=source_raw,
            recent_raw=recent_raw,
        )
    except (TypeError, ValueError) as exc:
        report = _blocked_report(
            reason="invalid_schema",
            source_path=source,
            existing_features_path=existing,
            output_path=output,
            report_path=report_file,
            source_report={
                **source_report,
                "schema_error": f"raw_continuity_basis_failed:{type(exc).__name__}:{exc}",
            },
            public_download_report=public_download_report,
            existing_continuity=existing_continuity,
            current=current,
        )
        write_json(report_file, report)
        return report

    continuity_repair_triggered = bool(
        existing_features is not None
        and existing_continuity["interior_null_regression_count"] > 0
    )
    rebuild_mode = (
        "full_continuity_repair"
        if continuity_repair_triggered
        else (
            "full_initial_build"
            if existing_features is None
            else "affected_group_full_history"
        )
    )

    rebuild_raw = (
        raw_basis
        if rebuild_mode != "affected_group_full_history"
        else _select_affected_group_full_history_raw(
            raw_basis=raw_basis,
            recent_raw=recent_raw,
        )
    )

    try:
        rebuilt_features = build_market_feature_frame(rebuild_raw)
    except Exception as exc:
        report = _blocked_report(
            reason="feature_rebuild_failed",
            source_path=source,
            existing_features_path=existing,
            output_path=output,
            report_path=report_file,
            source_report={
                **source_report,
                "schema_error": f"feature_rebuild_failed:{type(exc).__name__}:{exc}",
            },
            public_download_report=public_download_report,
            existing_continuity=existing_continuity,
            raw_basis_report=raw_basis_report,
            rebuild_mode=rebuild_mode,
            current=current,
        )
        write_json(report_file, report)
        return report

    operational_candidate = (
        rebuilt_features
        if existing_features is None
        else _merge_features_preserving_non_null(
            existing=existing_features,
            rebuilt=rebuilt_features,
        )
    )
    operational_candidate = _dedupe_features(operational_candidate)
    final_features, schema_report = sanitize_operational_market_features(
        operational_candidate
    )
    schema_error = validate_feature_schema(final_features)
    if not schema_report["operational_feature_schema_ok"]:
        schema_error = (
            f"operational_lookahead_columns:{schema_report['lookahead_columns']}"
        )

    final_continuity = inspect_feature_continuity(final_features)
    if final_continuity["interior_null_regression_count"] > 0:
        schema_error = (
            "feature_continuity_regression_detected:"
            f"{final_continuity['interior_null_regression_count']}"
        )

    if schema_error:
        report = _blocked_report(
            reason=(
                "feature_continuity_regression_detected"
                if str(schema_error).startswith("feature_continuity_regression_detected")
                else "invalid_schema"
            ),
            source_path=source,
            existing_features_path=existing,
            output_path=output,
            report_path=report_file,
            source_report={**source_report, "schema_error": schema_error},
            public_download_report=public_download_report,
            schema_report=schema_report,
            existing_continuity=existing_continuity,
            final_continuity=final_continuity,
            raw_basis_report=raw_basis_report,
            rebuild_mode=rebuild_mode,
            continuity_repair_triggered=continuity_repair_triggered,
            rebuilt_feature_rows=int(len(rebuilt_features)),
            current=current,
        )
        write_json(report_file, report)
        return report

    max_timestamp = _max_timestamp(final_features)
    max_age = _age_minutes(max_timestamp, current) if max_timestamp else None
    group_freshness = inspect_group_freshness(
        final_features,
        expected_groups=[(symbol, timeframe) for symbol in normalized_symbols],
        max_source_age_minutes=max_source_age_minutes,
        now=current,
    )
    if group_freshness["status"] != "ok":
        report = _status_report(
            status="blocked",
            reason=(
                "missing_expected_group"
                if group_freshness["missing_group_count"] > 0
                else "stale_source"
            ),
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
            existing_continuity=existing_continuity,
            final_continuity=final_continuity,
            raw_basis_report=raw_basis_report,
            rebuild_mode=rebuild_mode,
            continuity_repair_triggered=continuity_repair_triggered,
            rebuilt_feature_rows=int(len(rebuilt_features)),
            group_freshness=group_freshness,
            current=current,
        )
        write_json(report_file, report)
        return report

    final_features, schema_report = write_operational_market_features(
        operational_candidate,
        output,
    )
    final_continuity = inspect_feature_continuity(final_features)
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
        existing_continuity=existing_continuity,
        final_continuity=final_continuity,
        raw_basis_report=raw_basis_report,
        rebuild_mode=rebuild_mode,
        continuity_repair_triggered=continuity_repair_triggered,
        rebuilt_feature_rows=int(len(rebuilt_features)),
        group_freshness=group_freshness,
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
                "status": (
                    "ok"
                    if age is not None and age <= float(max_source_age_minutes)
                    else "stale_source"
                ),
            }
        )
        return report
    except Exception as exc:
        report["status"] = "invalid_schema"
        report["error"] = str(exc)
        return report


def inspect_group_freshness(
    frame: pd.DataFrame | None,
    *,
    expected_groups: list[tuple[str, str]],
    max_source_age_minutes: int | float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed unless every expected symbol/timeframe group is individually fresh."""

    current = _normalize_now(now)
    expected = sorted(
        {(internal_symbol(symbol), str(tf)) for symbol, tf in expected_groups}
    )
    if frame is None or frame.empty:
        return {
            "status": "blocked",
            "freshness_scope": "per_expected_group",
            "max_source_age_minutes": float(max_source_age_minutes),
            "expected_group_count": len(expected),
            "fresh_group_count": 0,
            "stale_group_count": 0,
            "missing_group_count": len(expected),
            "groups": [
                {
                    "symbol": symbol,
                    "tf": tf,
                    "status": "missing",
                    "max_timestamp": None,
                    "age_minutes": None,
                }
                for symbol, tf in expected
            ],
        }

    working = frame.copy()
    required = {"symbol", "tf", "ts"}
    if not required.issubset(working.columns):
        return {
            "status": "blocked",
            "freshness_scope": "per_expected_group",
            "max_source_age_minutes": float(max_source_age_minutes),
            "expected_group_count": len(expected),
            "fresh_group_count": 0,
            "stale_group_count": 0,
            "missing_group_count": len(expected),
            "groups": [],
            "reason": "freshness_key_columns_missing",
        }

    working["symbol"] = working["symbol"].map(internal_symbol)
    working["tf"] = working["tf"].astype(str)
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")

    groups: list[dict[str, Any]] = []
    fresh = 0
    stale = 0
    missing = 0
    for symbol, tf in expected:
        subset = working.loc[
            working["symbol"].eq(symbol) & working["tf"].eq(tf),
            "ts",
        ].dropna()
        if subset.empty:
            status = "missing"
            timestamp = None
            age = None
            missing += 1
        else:
            latest = subset.max().to_pydatetime().astimezone(timezone.utc)
            timestamp = latest.isoformat()
            age = float(max(0.0, (current - latest).total_seconds() / 60.0))
            if age <= float(max_source_age_minutes):
                status = "ok"
                fresh += 1
            else:
                status = "stale"
                stale += 1
        groups.append(
            {
                "symbol": symbol,
                "tf": tf,
                "status": status,
                "max_timestamp": timestamp,
                "age_minutes": age,
            }
        )

    return {
        "status": "ok" if stale == 0 and missing == 0 else "blocked",
        "freshness_scope": "per_expected_group",
        "max_source_age_minutes": float(max_source_age_minutes),
        "expected_group_count": len(expected),
        "fresh_group_count": fresh,
        "stale_group_count": stale,
        "missing_group_count": missing,
        "groups": groups,
    }


def inspect_feature_continuity(frame: pd.DataFrame | None) -> dict[str, Any]:
    """Measure post-warm-up null regressions in stable market features."""

    if frame is None or frame.empty:
        return {
            "status": "not_available",
            "row_count": 0,
            "group_count": 0,
            "guard_feature_columns": list(CONTINUITY_GUARD_FEATURE_COLUMNS),
            "interior_null_regression_count": 0,
            "latest_guard_missing_count": 0,
            "feature_metrics": [],
        }

    required_keys = set(FEATURE_KEY_COLUMNS)
    if not required_keys.issubset(frame.columns):
        return {
            "status": "blocked",
            "row_count": int(len(frame)),
            "group_count": 0,
            "guard_feature_columns": list(CONTINUITY_GUARD_FEATURE_COLUMNS),
            "interior_null_regression_count": 0,
            "latest_guard_missing_count": 0,
            "feature_metrics": [],
            "reason": "continuity_key_columns_missing",
        }

    working = frame.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working = working.dropna(subset=list(FEATURE_KEY_COLUMNS)).sort_values(
        list(FEATURE_KEY_COLUMNS),
        kind="mergesort",
    )

    metrics: list[dict[str, Any]] = []
    total_interior_nulls = 0
    latest_guard_missing_count = 0
    group_count = 0
    for (symbol, tf), group in working.groupby(["symbol", "tf"], sort=False):
        group_count += 1
        ordered = group.sort_values("ts", kind="mergesort").reset_index(drop=True)
        for column in CONTINUITY_GUARD_FEATURE_COLUMNS:
            if column not in ordered.columns:
                metrics.append(
                    {
                        "symbol": str(symbol),
                        "tf": str(tf),
                        "feature": column,
                        "first_valid_position": None,
                        "valid_count": 0,
                        "interior_null_count": int(len(ordered)),
                        "latest_missing": True,
                        "column_missing": True,
                    }
                )
                total_interior_nulls += int(len(ordered))
                latest_guard_missing_count += 1
                continue

            valid = ordered[column].notna()
            positions = valid.to_numpy().nonzero()[0]
            if len(positions) == 0:
                interior_null_count = 0
                first_valid_position = None
            else:
                first_valid_position = int(positions[0])
                interior_null_count = int((~valid.iloc[first_valid_position:]).sum())
            latest_missing = bool(not valid.iloc[-1]) if len(valid) else True
            total_interior_nulls += interior_null_count
            latest_guard_missing_count += int(latest_missing)
            metrics.append(
                {
                    "symbol": str(symbol),
                    "tf": str(tf),
                    "feature": column,
                    "first_valid_position": first_valid_position,
                    "valid_count": int(valid.sum()),
                    "interior_null_count": interior_null_count,
                    "latest_missing": latest_missing,
                    "column_missing": False,
                }
            )

    return {
        "status": "ok" if total_interior_nulls == 0 else "blocked",
        "row_count": int(len(working)),
        "group_count": int(group_count),
        "guard_feature_columns": list(CONTINUITY_GUARD_FEATURE_COLUMNS),
        "interior_null_regression_count": int(total_interior_nulls),
        "latest_guard_missing_count": int(latest_guard_missing_count),
        "feature_metrics": metrics,
    }


def validate_raw_market_schema(path: str | Path) -> str | None:
    _, error = _load_raw_source(Path(path))
    return error


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
    """Download closed public Binance Futures candles with bounded pagination."""

    current = _normalize_now(now)
    requested = int(lookback_candles)
    if requested <= 0:
        raise ValueError("lookback_candles_must_be_positive")

    interval_ms = _interval_to_ms(timeframe)
    end_ms = int((current - timedelta(milliseconds=interval_ms)).timestamp() * 1000)
    start_ms = end_ms - int(requested * interval_ms)
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        rows = _fetch_klines_paginated(
            base_url=base_url,
            endpoint=endpoint,
            symbol=internal_symbol(symbol),
            interval=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            requested_candles=requested,
            request_sleep_seconds=request_sleep_seconds,
        )
        frames.append(_normalize_public_klines(rows, symbol, timeframe))
        time.sleep(max(0.0, request_sleep_seconds))

    combined = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=sorted(REQUIRED_RAW_COLUMNS))
    )
    if combined.empty:
        raise RuntimeError("public_market_source_empty")
    return _normalize_raw_frame(combined)


def _fetch_klines_paginated(
    *,
    base_url: str,
    endpoint: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    requested_candles: int,
    request_sleep_seconds: float,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    cursor = int(start_ms)
    while cursor <= end_ms and len(rows) < requested_candles:
        remaining = requested_candles - len(rows)
        limit = min(1500, remaining)
        page = _fetch_klines(
            base_url=base_url,
            endpoint=endpoint,
            symbol=symbol,
            interval=interval,
            start_ms=cursor,
            end_ms=end_ms,
            limit=limit,
        )
        if not page:
            break
        rows.extend(page)
        last_open = int(page[-1][0])
        next_cursor = last_open + _interval_to_ms(interval)
        if next_cursor <= cursor:
            raise RuntimeError("public_market_api_non_advancing_cursor")
        cursor = next_cursor
        if cursor <= end_ms and len(rows) < requested_candles:
            time.sleep(max(0.0, request_sleep_seconds))
    return rows[-requested_candles:]


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
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    request = Request(
        f"{base_url.rstrip('/')}{endpoint}?{urlencode(params)}",
        headers={"User-Agent": "smartcrypto-qlib-refresh/2.0"},
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict):
        raise RuntimeError(f"public_market_api_error:{payload}")
    if not isinstance(payload, list):
        raise RuntimeError("public_market_api_invalid_response")
    return payload


def _normalize_public_klines(
    rows: list[list[Any]],
    symbol: str,
    timeframe: str,
) -> pd.DataFrame:
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
    normalized_symbol = internal_symbol(symbol)
    frame["symbol"] = normalized_symbol
    frame["pair"] = freqtrade_pair(normalized_symbol)
    frame["tf"] = timeframe
    frame["ts"] = pd.to_datetime(
        pd.to_numeric(frame["open_time"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    )
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[
        ["symbol", "pair", "tf", "ts", "open", "high", "low", "close", "volume"]
    ].dropna()


def _read_existing_features(path: Path) -> pd.DataFrame | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return None
    return frame if not frame.empty else None


def _load_raw_source(path: Path) -> tuple[pd.DataFrame | None, str | None]:
    if not path.exists() or not path.is_file():
        return None, "missing_source"
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return None, f"unreadable_source:{exc}"
    missing = sorted(REQUIRED_RAW_COLUMNS.difference(frame.columns))
    if missing:
        return None, f"missing_raw_columns:{missing}"
    if frame.empty:
        return None, "empty_source"
    try:
        return _normalize_raw_frame(frame), None
    except (TypeError, ValueError) as exc:
        return None, f"invalid_raw_rows:{type(exc).__name__}:{exc}"


def _extract_raw_from_features(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    if not REQUIRED_RAW_COLUMNS.issubset(frame.columns):
        return None
    columns = [
        column
        for column in ("symbol", "pair", "tf", "ts", "open", "high", "low", "close", "volume")
        if column in frame.columns
    ]
    return _normalize_raw_frame(frame.loc[:, columns].copy())


def _normalize_raw_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_RAW_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"missing_raw_columns:{missing}")
    result = frame.copy()
    result["symbol"] = result["symbol"].map(internal_symbol)
    result["tf"] = result["tf"].astype(str)
    result["ts"] = pd.to_datetime(result["ts"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(
        subset=["symbol", "tf", "ts", "open", "high", "low", "close", "volume"]
    ).copy()
    if result.empty:
        raise ValueError("empty_source")
    if "pair" not in result.columns:
        result["pair"] = result["symbol"].map(freqtrade_pair)
    else:
        result["pair"] = result["symbol"].map(freqtrade_pair)
    return (
        result[
            ["symbol", "pair", "tf", "ts", "open", "high", "low", "close", "volume"]
        ]
        .sort_values(list(FEATURE_KEY_COLUMNS), kind="mergesort")
        .drop_duplicates(subset=list(FEATURE_KEY_COLUMNS), keep="last")
        .reset_index(drop=True)
    )


def _build_raw_continuity_basis(
    *,
    existing_features: pd.DataFrame | None,
    source_raw: pd.DataFrame | None,
    recent_raw: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    source_names: list[str] = []

    existing_raw = _extract_raw_from_features(existing_features)
    if existing_raw is not None:
        frames.append(existing_raw)
        source_names.append("existing_feature_ohlcv")
    if source_raw is not None:
        frames.append(source_raw)
        source_names.append("configured_raw_source")
    frames.append(_normalize_raw_frame(recent_raw))
    source_names.append("recent_refresh_raw")

    if not frames:
        raise ValueError("raw_continuity_basis_empty")
    combined = _normalize_raw_frame(pd.concat(frames, ignore_index=True, sort=False))
    return combined, {
        "status": "ok",
        "sources": source_names,
        "rows": int(len(combined)),
        "symbols": sorted(combined["symbol"].dropna().astype(str).unique().tolist()),
        "timeframes": sorted(combined["tf"].dropna().astype(str).unique().tolist()),
        "max_timestamp": _max_timestamp(combined),
    }


def _select_affected_group_full_history_raw(
    *,
    raw_basis: pd.DataFrame,
    recent_raw: pd.DataFrame,
) -> pd.DataFrame:
    """Return complete history for every group touched by the current refresh.

    Recursive indicators (EMA in particular) depend on all preceding observations.
    Rebuilding only a recent tail changes their state at the tail boundary and creates
    silent numerical drift even if continuity/null checks pass. The refresh therefore
    rebuilds each affected group from its earliest available raw candle.
    """

    recent = _normalize_raw_frame(recent_raw)
    affected = recent[["symbol", "tf"]].drop_duplicates()
    pieces: list[pd.DataFrame] = []
    for row in affected.itertuples(index=False):
        symbol = str(row.symbol)
        tf = str(row.tf)
        group = raw_basis.loc[
            raw_basis["symbol"].eq(symbol) & raw_basis["tf"].eq(tf)
        ].sort_values("ts", kind="mergesort")
        if not group.empty:
            pieces.append(group)

    if not pieces:
        raise ValueError("affected_group_rebuild_has_no_groups")
    return _normalize_raw_frame(pd.concat(pieces, ignore_index=True, sort=False))


def _merge_features_preserving_non_null(
    *,
    existing: pd.DataFrame,
    rebuilt: pd.DataFrame,
) -> pd.DataFrame:
    """Prefer rebuilt values, but never replace an existing value with null."""

    existing_clean = _dedupe_features(existing)
    rebuilt_clean = _dedupe_features(rebuilt)
    existing_indexed = existing_clean.set_index(list(FEATURE_KEY_COLUMNS))
    rebuilt_indexed = rebuilt_clean.set_index(list(FEATURE_KEY_COLUMNS))

    # DataFrame.combine_first keeps the left value when non-null and fills only its
    # nulls from the right. With rebuilt on the left this implements exactly the
    # continuity contract required for overlapping warm-up rows.
    combined = rebuilt_indexed.combine_first(existing_indexed).reset_index()
    return _dedupe_features(combined)


def _dedupe_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["symbol"] = result["symbol"].map(internal_symbol)
    result["tf"] = result["tf"].astype(str)
    result["ts"] = pd.to_datetime(result["ts"], utc=True, errors="coerce")
    result = result.dropna(subset=list(FEATURE_KEY_COLUMNS)).copy()
    return (
        result.drop_duplicates(subset=list(FEATURE_KEY_COLUMNS), keep="last")
        .sort_values(list(FEATURE_KEY_COLUMNS), kind="mergesort")
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
    return float(
        max(0.0, (now - parsed.to_pydatetime()).total_seconds() / 60.0)
    )


def _interval_to_ms(value: str) -> int:
    text = str(value).strip().lower()
    if text.endswith("m"):
        return int(text[:-1]) * 60_000
    if text.endswith("h"):
        return int(text[:-1]) * 60 * 60_000
    raise ValueError(f"unsupported_timeframe:{value}")


def _normalize_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return (
        current.astimezone(timezone.utc)
        if current.tzinfo
        else current.replace(tzinfo=timezone.utc)
    )


def _blocked_report(
    *,
    reason: str,
    source_path: Path,
    existing_features_path: Path,
    output_path: Path,
    report_path: Path,
    source_report: dict[str, Any],
    public_download_report: dict[str, Any],
    current: datetime,
    schema_report: dict[str, Any] | None = None,
    existing_continuity: dict[str, Any] | None = None,
    final_continuity: dict[str, Any] | None = None,
    raw_basis_report: dict[str, Any] | None = None,
    rebuild_mode: str | None = None,
    continuity_repair_triggered: bool = False,
    rebuilt_feature_rows: int = 0,
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
        "raw_continuity_basis": raw_basis_report or {},
        "rebuild_mode": rebuild_mode,
        "continuity_repair_triggered": bool(continuity_repair_triggered),
        "rebuilt_feature_rows": int(rebuilt_feature_rows),
        "existing_feature_continuity": existing_continuity or {},
        "final_feature_continuity": final_continuity or {},
        **schema_report,
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
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
    existing_continuity: dict[str, Any],
    final_continuity: dict[str, Any],
    raw_basis_report: dict[str, Any],
    rebuild_mode: str,
    continuity_repair_triggered: bool,
    rebuilt_feature_rows: int,
    group_freshness: dict[str, Any] | None = None,
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
        "symbols": (
            sorted(final_features["symbol"].dropna().astype(str).unique().tolist())
            if "symbol" in final_features.columns
            else []
        ),
        "timeframes": (
            sorted(final_features["tf"].dropna().astype(str).unique().tolist())
            if "tf" in final_features.columns
            else []
        ),
        "source_report": source_report,
        "public_download": public_download_report,
        "raw_continuity_basis": raw_basis_report,
        "rebuild_mode": rebuild_mode,
        "affected_group_rebuild_policy": AFFECTED_GROUP_REBUILD_POLICY,
        "group_freshness": group_freshness or {},
        "continuity_repair_triggered": bool(continuity_repair_triggered),
        "rebuilt_feature_rows": int(rebuilt_feature_rows),
        "existing_feature_continuity": existing_continuity,
        "final_feature_continuity": final_continuity,
        **schema_report,
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "created_at": current.isoformat(),
    }
