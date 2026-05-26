from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import APIRequestContext, Playwright, async_playwright

from .config import RuntimeConfig, SYMBOL_TO_PATH_TOKEN, TIMEFRAME_SECONDS
from .processor import DataProcessor

LOGGER = logging.getLogger("bitradex.collector.endpoint_probe")


@dataclass(slots=True, frozen=True)
class ProbeCandidate:
    url: str
    symbol: str
    timeframe: str
    reason: str


class EndpointProbe:
    """Direct public-endpoint discovery for Bitradex market candles.

    This class is intentionally public-only. It does not use credentials and it
    only probes URL families under Bitradex public market namespaces observed by
    Playwright, especially /v1/future-u/market/.../public/...
    """

    API_HOSTS: tuple[str, ...] = (
        "https://www.bitradex.ai",
        "https://bitradex.ai",
        "https://www.bitradex.com",
        "https://bitradex.com",
    )

    PATHS: tuple[str, ...] = (
        # Confirmed by Playwright network interception on the futures chart.
        "/v1/future-u/market/public/q/kline",
        "/v1/future-u/market/v2/public/kline",
        "/v1/future-u/market/v2/public/klines",
        "/v1/future-u/market/v2/public/kline/list",
        "/v1/future-u/market/v2/public/kline/history",
        "/v1/future-u/market/v2/public/history/kline",
        "/v1/future-u/market/v2/public/candles",
        "/v1/future-u/market/public/kline",
        "/v1/future-u/market/public/klines",
        "/v1/future-u/market/public/kline/list",
        "/v1/future-u/market/public/kline/history",
        "/v1/future-u/market/public/history/kline",
        "/v1/future-u/market/public/candles",
        "/v1/future-u/market/kline",
        "/v1/future-u/market/klines",
        "/v1/future-u/market/history/kline",
        "/v1/future-u/market/candles",
    )

    SYMBOL_VARIANTS: dict[str, tuple[str, ...]] = {
        "BTCUSDT": ("BTCUSDT", "BTC_USDT", "btc_usdt", "btcusdt", "BTC/USDT"),
        "ETHUSDT": ("ETHUSDT", "ETH_USDT", "eth_usdt", "ethusdt", "ETH/USDT"),
    }

    TIMEFRAME_VARIANTS: dict[str, tuple[str, ...]] = {
        "15s": ("15s", "15sec", "15second", "15seconds"),
        "1m": ("1m", "1", "60", "1min", "1minute"),
        "5m": ("5m", "5", "300", "5min", "5minute"),
        "15m": ("15m", "15", "900", "15min", "15minute"),
    }

    def __init__(self, config: RuntimeConfig, processor: DataProcessor) -> None:
        self.config = config
        self.processor = processor
        self._hits: list[dict[str, Any]] = []
        self._failures: list[dict[str, Any]] = []

    async def run(self) -> dict[str, Any]:
        self.config.ensure_directories()
        async with async_playwright() as p:
            request = await self._new_request_context(p)
            try:
                candidates = list(self._build_candidates())
                LOGGER.info("direct_probe_start candidates=%s", len(candidates))
                semaphore = asyncio.Semaphore(max(1, int(self.config.direct_probe_concurrency)))
                tasks = [self._probe_one(request, candidate, semaphore) for candidate in candidates]
                await asyncio.gather(*tasks)
            finally:
                await request.dispose()
        export = self.processor.export_all()
        summary = {
            "status": "ok",
            "mode": "probe",
            "hits": self._hits,
            "hit_count": len(self._hits),
            "failure_count": len(self._failures),
            "failures_sample": self._failures[:50],
            "stats": self.processor.stats(),
            "export": export,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self.config.runtime_dir / "direct_probe_summary.json"
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return summary

    async def _new_request_context(self, p: Playwright) -> APIRequestContext:
        return await p.request.new_context(
            user_agent=self.config.user_agent,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept": "application/json,text/plain,*/*",
                "Origin": "https://www.bitradex.ai",
                "Referer": "https://www.bitradex.ai/en/futures/trade/btc_usdt",
                "Accept-Language": self.config.locale,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            timeout=self.config.page_timeout_ms,
        )

    def _build_candidates(self) -> list[ProbeCandidate]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, int(self.config.direct_probe_days)))
        start_sec = int(start.timestamp())
        end_sec = int(end.timestamp())
        start_ms = start_sec * 1000
        end_ms = end_sec * 1000
        limit = str(max(100, int(self.config.direct_probe_limit)))

        candidates: list[ProbeCandidate] = []
        for symbol in self.config.symbols:
            token = SYMBOL_TO_PATH_TOKEN.get(symbol.upper(), symbol.lower())
            symbol_values = self.SYMBOL_VARIANTS.get(symbol.upper(), (symbol.upper(), token))
            for timeframe in self.config.timeframes:
                tf_values = self.TIMEFRAME_VARIANTS.get(timeframe, (timeframe,))
                for host in self.API_HOSTS:
                    for path in self.PATHS:
                        for sym_key in ("symbol", "pair", "market", "contract"):
                            for tf_key in ("interval", "period", "resolution", "timeframe", "tf"):
                                for sym_value in symbol_values[:4]:
                                    for tf_value in tf_values[:3]:
                                        common = {
                                            sym_key: sym_value,
                                            tf_key: tf_value,
                                            "limit": limit,
                                        }
                                        query_sets = [
                                            # Confirmed Bitradex chart variants.
                                            common,
                                            common | {"endTime": str(end_ms)},
                                            common | {"endTime": str(end_ms), "limit": limit},
                                            common | {"from": str(start_sec), "to": str(end_sec)},
                                            common | {"start": str(start_sec), "end": str(end_sec)},
                                            common | {"startTime": str(start_ms), "endTime": str(end_ms)},
                                            common | {"start_time": str(start_sec), "end_time": str(end_sec)},
                                        ]
                                        for query in query_sets:
                                            candidates.append(
                                                ProbeCandidate(
                                                    url=f"{host}{path}?{urlencode(query)}",
                                                    symbol=symbol.upper(),
                                                    timeframe=timeframe,
                                                    reason="public_market_candidate",
                                                )
                                            )
        # Keep the scan bounded. It is a discovery routine, not a brute-force job.
        seen: set[str] = set()
        unique: list[ProbeCandidate] = []
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            unique.append(candidate)
        return unique[:4000]

    async def _probe_one(self, request: APIRequestContext, candidate: ProbeCandidate, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                response = await request.get(candidate.url, timeout=self.config.page_timeout_ms)
                status = response.status
                if status >= 500 or status in {401, 403, 404, 405}:
                    self._record_failure(candidate, status, "http_status")
                    return
                content_type = (response.headers.get("content-type") or "").lower()
                body = await response.body()
                if not body or len(body) > self.config.max_payload_bytes:
                    self._record_failure(candidate, status, "empty_or_too_large")
                    return
                result = self.processor.process_payload(
                    body,
                    source_url=candidate.url,
                    transport="http:direct_probe",
                    context_symbol=candidate.symbol,
                    context_timeframe=candidate.timeframe,
                )
                if result.candles_found > 0:
                    hit = {
                        "url": candidate.url,
                        "symbol": candidate.symbol,
                        "timeframe": candidate.timeframe,
                        "http_status": status,
                        "content_type": content_type,
                        "candles_found": result.candles_found,
                        "candles_written": result.candles_written,
                    }
                    self._hits.append(hit)
                    LOGGER.info("direct_probe_hit %s", hit)
                else:
                    preview = body[:800].decode("utf-8", errors="ignore") if body else ""
                    self._record_failure(candidate, status, "no_candles", preview=preview)
            except Exception as exc:
                self._record_failure(candidate, None, f"exception:{type(exc).__name__}:{exc}")

    def _record_failure(self, candidate: ProbeCandidate, status: int | None, reason: str, preview: str | None = None) -> None:
        if len(self._failures) < 2000:
            record = {
                "url": candidate.url[:500],
                "symbol": candidate.symbol,
                "timeframe": candidate.timeframe,
                "status": status,
                "reason": reason,
            }
            if preview:
                record["body_preview"] = preview[:800]
            self._failures.append(record)
