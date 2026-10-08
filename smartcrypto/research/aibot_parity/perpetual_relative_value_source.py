"""Public, checksummed closed klines and actual funding settlement mark prices."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    PUBLIC_ROOT,
    PITSourceError,
    _klines,
    archive_rows,
    sha256,
    write_once,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT")
FUNDING_ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"


def timestamp(value: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise PITSourceError("timestamp_requires_explicit_timezone")
    return result.tz_convert("UTC")


def public_funding(
    cache: Path, symbol: str, start: pd.Timestamp, end: pd.Timestamp, *, allow_download: bool
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if symbol not in SYMBOLS or start >= end:
        raise PITSourceError("funding_request_invalid")
    start_ms, end_ms = start.value // 1_000_000, end.value // 1_000_000
    query = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms - 1, "limit": 1000}
    url = FUNDING_ENDPOINT + "?" + urlencode(query)
    path = cache / "funding" / f"{symbol}-{start_ms}-{end_ms}.json"
    if any(p.is_symlink() for p in (path, path.with_suffix(".sha256"), *path.parents)):
        raise PITSourceError("funding_cache_symlink")
    if path.is_file():
        raw = path.read_bytes()
        seal = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
        if sha256(raw) != seal:
            raise PITSourceError("funding_cache_hash_mismatch")
    elif allow_download:
        request = urllib.request.Request(url, headers={"User-Agent": "smartcrypto-rv-research/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            if response.url != url:
                raise PITSourceError("funding_redirect_forbidden")
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise PITSourceError("funding_response_oversized")
    else:
        raise PITSourceError(f"SOURCE_CACHE_MISSING:{path.name}")
    payload = json.loads(raw)
    if not isinstance(payload, list) or not 2 <= len(payload) < 1000:
        raise PITSourceError("funding_history_empty_or_pagination_required")
    records = []
    for item in payload:
        if not isinstance(item, Mapping) or item.get("symbol") != symbol:
            raise PITSourceError("funding_symbol_invalid")
        event = int(item["fundingTime"])
        rate, mark = float(item["fundingRate"]), float(item["markPrice"])
        if not start_ms <= event < end_ms or not np.isfinite([rate, mark]).all() or mark <= 0:
            raise PITSourceError("funding_value_or_timestamp_invalid")
        records.append(
            {"event_ms": event, "available_ms": event + 300_000, "rate": rate, "mark": mark}
        )
    frame = pd.DataFrame(records)
    if frame.event_ms.duplicated().any() or not frame.event_ms.is_monotonic_increasing:
        raise PITSourceError("funding_identity_order_invalid")
    intervals = frame.event_ms.diff() / 3_600_000
    if not intervals.iloc[1:].between(0.9, 8.1).all():
        raise PITSourceError("funding_interval_missing_or_invalid")
    frame["interval_hours"] = intervals
    if allow_download:
        write_once(path, raw)
        write_once(path.with_suffix(".sha256"), sha256(raw).encode("ascii"))
    return frame, {
        "url": url,
        "sha256": sha256(raw),
        "rows": len(frame),
        "hash_origin": "local_sha256_of_public_response",
        "mark_price_source": "actual_markPrice_associated_with_funding_charge",
    }


def load_public_history(
    cache: Path, start: pd.Timestamp, end: pd.Timestamp, *, allow_download: bool
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any]]:
    prices: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Any] = {}
    for symbol in SYMBOLS:
        legs = {}
        for market in ("spot", "futures/um"):
            records = []
            for day in pd.date_range(
                start.floor("D"), (end - pd.Timedelta(milliseconds=1)).floor("D"), freq="D"
            ):
                relative = f"{market}/daily/klines/{symbol}/1m/{symbol}-1m-{day:%Y-%m-%d}.zip"
                rows, digest = archive_rows(relative, cache, allow_download=allow_download)
                validated = _klines(rows, spot=market == "spot")
                scale = 1000 if market == "spot" else 1
                for row in rows:
                    if row and row[0] == "open_time":
                        continue
                    opened_ms = int(row[0]) // scale
                    opened_price = float(row[1])
                    if (
                        opened_ms not in validated
                        or not np.isfinite(opened_price)
                        or opened_price <= 0
                    ):
                        raise PITSourceError("execution_open_price_invalid")
                    records.append(
                        {
                            "time_ms": opened_ms,
                            "open": opened_price,
                            "close": validated[opened_ms][0],
                        }
                    )
                provenance[relative] = {
                    "url": PUBLIC_ROOT + relative,
                    "sha256": digest,
                    "rows": len(validated),
                    "hash_origin": "official_CHECKSUM",
                }
            frame = pd.DataFrame(records)
            if frame.time_ms.duplicated().any() or not frame.time_ms.is_monotonic_increasing:
                raise PITSourceError("kline_identity_order_invalid")
            expected = np.arange(start.value // 1_000_000, end.value // 1_000_000, 60_000)
            frame = frame.loc[frame.time_ms.between(int(expected[0]), int(expected[-1]))]
            if not np.array_equal(frame.time_ms.to_numpy(), expected):
                raise PITSourceError("incomplete_public_1m_history")
            legs[market] = frame.set_index("time_ms")
        prices[symbol] = (
            legs["spot"]
            .add_prefix("spot_")
            .join(legs["futures/um"].add_prefix("perp_"), validate="one_to_one")
            .reset_index()
        )
        funding[symbol], proof = public_funding(
            cache, symbol, start, end, allow_download=allow_download
        )
        provenance[f"funding/{symbol}"] = proof
    return (
        prices,
        funding,
        {
            "sources": provenance,
            "provenance_sha256": sha256(
                json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
            ),
            "symbols": list(SYMBOLS),
            "price_rows_per_symbol": {s: len(prices[s]) for s in SYMBOLS},
            "availability_classification": "PIT_MODELLED_CONSERVATIVE",
            "candle_available_delay_seconds": 60,
            "funding_available_delay_seconds": 300,
            "historical_reception_proven": False,
        },
    )
