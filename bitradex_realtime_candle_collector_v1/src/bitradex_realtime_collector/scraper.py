from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import Browser, BrowserContext, Page, Response, async_playwright

from .config import RuntimeConfig
from .processor import DataProcessor, KLINE_URL_HINTS

LOGGER = logging.getLogger("bitradex.collector.scraper")


@dataclass(slots=True)
class DiscoveredEndpoint:
    url: str
    method: str = "GET"
    first_seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    hits: int = 1
    candle_hits: int = 0

    def touch(self, candle_hits: int = 0) -> None:
        self.last_seen_at = datetime.now(timezone.utc).isoformat()
        self.hits += 1
        self.candle_hits += int(candle_hits)


class EndpointCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._endpoints: dict[str, DiscoveredEndpoint] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for item in payload.get("endpoints", []):
                endpoint = DiscoveredEndpoint(**item)
                self._endpoints[endpoint.url] = endpoint
        except Exception as exc:
            LOGGER.warning("endpoint_catalog_load_failed path=%s error=%s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"endpoints": [asdict(item) for item in self._endpoints.values()]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, url: str, candle_hits: int = 0) -> None:
        normalized = self._normalize_url(url)
        if normalized in self._endpoints:
            self._endpoints[normalized].touch(candle_hits)
        else:
            self._endpoints[normalized] = DiscoveredEndpoint(url=normalized, candle_hits=int(candle_hits))

    def replay_candidates(self) -> list[str]:
        candidates = []
        for endpoint in self._endpoints.values():
            if endpoint.candle_hits > 0 and self._has_time_params(endpoint.url):
                candidates.append(endpoint.url)
        return candidates

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        safe_query = {}
        for key, value in query.items():
            if key.lower() in {"token", "signature", "auth", "authorization", "apikey", "api_key"}:
                continue
            safe_query[key] = value
        return urlunparse(parsed._replace(query=urlencode(safe_query, doseq=True)))

    @staticmethod
    def _has_time_params(url: str) -> bool:
        query = {k.lower() for k in parse_qs(urlparse(url).query).keys()}
        return bool(query & {"from", "to", "start", "end", "starttime", "endtime", "start_time", "end_time", "limit", "size"})


class ScraperHandler:
    """Playwright network collector for Bitradex futures chart traffic."""

    def __init__(self, config: RuntimeConfig, processor: DataProcessor) -> None:
        self.config = config
        self.processor = processor
        self.catalog = EndpointCatalog(config.discovered_endpoints_path)
        self.stop_event = asyncio.Event()
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: list[Page] = []
        self._last_export_monotonic = 0.0
        self._last_heartbeat_monotonic = 0.0
        self._response_tasks: set[asyncio.Task[Any]] = set()

    async def run(self) -> None:
        self._install_signal_handlers()
        async with async_playwright() as p:
            launch_kwargs: dict[str, Any] = {
                "headless": self.config.headless,
                "args": [
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--mute-audio",
                ],
            }
            if self.config.browser_channel:
                launch_kwargs["channel"] = self.config.browser_channel
            self._browser = await p.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(
                user_agent=self.config.user_agent,
                viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                locale=self.config.locale,
                timezone_id=self.config.timezone_id,
                ignore_https_errors=True,
            )
            self._context.set_default_timeout(self.config.page_timeout_ms)
            await self._open_pages()
            await self._main_loop()
            await self._shutdown()

    async def _open_pages(self) -> None:
        """Open one validated futures page per symbol.

        V1 accepted a page even when Bitradex redirected the requested futures
        route to https://www.bitradex.ai/. That root page only emits generic
        ticker/balance metadata and never loads TradingView candles. V2 tries all
        canonical URL variants and accepts a page only when the URL/title/body
        still shows the requested futures pair.
        """
        assert self._context is not None
        opened_symbols: set[str] = set()
        urls_by_symbol: dict[str, list[str]] = {}
        for symbol, url in self.config.trading_urls():
            urls_by_symbol.setdefault(symbol, []).append(url)

        for symbol in self.config.symbols:
            symbol = symbol.upper()
            if symbol in opened_symbols:
                continue
            selected_page: Page | None = None
            selected_url: str | None = None
            for url in urls_by_symbol.get(symbol, []):
                page = await self._context.new_page()
                self._attach_page_handlers(page, symbol)
                try:
                    LOGGER.info("navigating symbol=%s url=%s", symbol, url)
                    await page.goto(url, wait_until="domcontentloaded", timeout=self.config.page_timeout_ms)
                    await page.wait_for_timeout(6_000)
                    if await self._page_has_trade_context(page, symbol):
                        selected_page = page
                        selected_url = url
                        break
                    LOGGER.warning(
                        "route_validation_failed symbol=%s requested=%s final_url=%s title=%s",
                        symbol,
                        url,
                        page.url,
                        await self._safe_title(page),
                    )
                    await page.close()
                except Exception as exc:
                    LOGGER.warning("navigation_failed symbol=%s url=%s error=%s", symbol, url, exc)
                    try:
                        await page.close()
                    except Exception:
                        pass

            if selected_page is None:
                if self.config.route_validation_required:
                    LOGGER.error("no_valid_futures_route symbol=%s tried=%s", symbol, urls_by_symbol.get(symbol, []))
                    continue
                # Conservative fallback only when explicitly disabled.
                fallback_url = urls_by_symbol.get(symbol, [])[0]
                fallback_page = await self._context.new_page()
                self._attach_page_handlers(fallback_page, symbol)
                await fallback_page.goto(fallback_url, wait_until="domcontentloaded", timeout=self.config.page_timeout_ms)
                await fallback_page.wait_for_timeout(6_000)
                selected_page = fallback_page
                selected_url = fallback_url

            self._pages.append(selected_page)
            opened_symbols.add(symbol)
            LOGGER.info("page_ready symbol=%s requested=%s final_url=%s", symbol, selected_url, selected_page.url)

        if not self._pages:
            raise RuntimeError(
                "No validated Bitradex futures trading pages could be opened. "
                "Run --mode probe and/or --headful to inspect current public routes."
            )

    async def _safe_title(self, page: Page) -> str:
        try:
            return await page.title()
        except Exception:
            return ""

    async def _page_has_trade_context(self, page: Page, symbol: str) -> bool:
        token = symbol.lower().replace("usdt", "_usdt")
        compact = symbol.lower()
        url_lower = page.url.lower()
        if token in url_lower and "futures" in url_lower:
            return True
        try:
            title = (await page.title()).lower()
        except Exception:
            title = ""
        if (symbol.lower() in title or symbol.replace("USDT", "/USDT").lower() in title) and ("future" in title or "derivative" in title):
            return True
        try:
            body_text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 12000) : ''")
        except Exception:
            body_text = ""
        body_lower = str(body_text).lower()
        pair_text = symbol.replace("USDT", "/USDT").lower()
        return (token in body_lower or compact in body_lower or pair_text in body_lower) and ("future" in body_lower or "perpetual" in body_lower or "swap" in body_lower)

    def _attach_page_handlers(self, page: Page, symbol: str) -> None:
        page.on("response", lambda response: self._schedule_task(self._on_response(response, symbol)))
        page.on("websocket", lambda ws: self._on_websocket(ws, symbol))
        page.on("console", lambda msg: LOGGER.debug("browser_console symbol=%s type=%s text=%s", symbol, msg.type, msg.text[:300]))
        page.on("pageerror", lambda exc: LOGGER.warning("browser_page_error symbol=%s error=%s", symbol, exc))

    def _on_websocket(self, ws: Any, symbol: str) -> None:
        LOGGER.info("websocket_opened symbol=%s url=%s", symbol, getattr(ws, "url", "unknown"))
        ws.on("framereceived", lambda payload: self._schedule_task(self._on_ws_frame(payload, ws.url, symbol, "websocket:received")))
        ws.on("framesent", lambda payload: self._schedule_task(self._on_ws_frame(payload, ws.url, symbol, "websocket:sent")))
        ws.on("close", lambda: LOGGER.info("websocket_closed symbol=%s url=%s", symbol, getattr(ws, "url", "unknown")))

    def _schedule_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._response_tasks.add(task)
        task.add_done_callback(self._response_tasks.discard)

    async def _on_response(self, response: Response, context_symbol: str) -> None:
        request = response.request
        resource_type = request.resource_type
        url = response.url
        if resource_type not in {"xhr", "fetch", "websocket", "eventsource"} and not self._url_has_kline_hint(url):
            return
        try:
            content_type = (response.headers.get("content-type") or "").lower()
            if "json" not in content_type and not self._url_has_kline_hint(url):
                return
            body = await asyncio.wait_for(response.body(), timeout=self.config.response_body_timeout_seconds)
            if not body or len(body) > self.config.max_payload_bytes:
                return
            if self.config.audit_all_network:
                self._append_network_audit(body, url, context_symbol, f"http:{resource_type}", content_type)
            result = self.processor.process_payload(
                body,
                source_url=url,
                transport=f"http:{resource_type}",
                context_symbol=context_symbol,
            )
            if result.candles_found > 0:
                self.catalog.add(url, candle_hits=result.candles_found)
                LOGGER.info(
                    "candles_captured transport=http:%s symbol_context=%s found=%s written=%s url=%s",
                    resource_type,
                    context_symbol,
                    result.candles_found,
                    result.candles_written,
                    url[:240],
                )
        except asyncio.TimeoutError:
            LOGGER.debug("response_body_timeout url=%s", url[:240])
        except Exception as exc:
            LOGGER.debug("response_process_failed url=%s error=%s", url[:240], exc)

    async def _on_ws_frame(self, payload: str | bytes, source_url: str, context_symbol: str, transport: str) -> None:
        try:
            raw_len = len(payload) if isinstance(payload, bytes) else len(str(payload).encode("utf-8", errors="ignore"))
            if raw_len > self.config.max_payload_bytes:
                return
            has_hint = self._payload_or_url_has_kline_hint(payload, source_url)
            if self.config.save_ws_frame_audit and (has_hint or "bitradex" in source_url.lower()):
                self._append_ws_frame_audit(payload, source_url, context_symbol, transport, has_hint)
            if not has_hint and not self.config.process_all_public_ws_frames:
                return
            result = self.processor.process_payload(
                payload,
                source_url=source_url,
                transport=transport,
                context_symbol=context_symbol,
            )
            if result.candles_found > 0:
                self.catalog.add(source_url, candle_hits=result.candles_found)
                LOGGER.info(
                    "candles_captured transport=%s symbol_context=%s found=%s written=%s url=%s",
                    transport,
                    context_symbol,
                    result.candles_found,
                    result.candles_written,
                    source_url[:240],
                )
        except Exception as exc:
            LOGGER.debug("websocket_frame_process_failed url=%s error=%s", source_url[:240], exc)


    def _append_network_audit(self, payload: bytes, source_url: str, context_symbol: str, transport: str, content_type: str) -> None:
        try:
            text = payload.decode("utf-8", errors="ignore")
            record = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "transport": transport,
                "symbol_context": context_symbol,
                "source_url": source_url,
                "content_type": content_type,
                "payload_bytes": len(payload),
                "payload_preview": text[:6000],
            }
            with self.config.network_audit_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            LOGGER.debug("network_audit_failed error=%s", exc)

    def _append_ws_frame_audit(self, payload: str | bytes, source_url: str, context_symbol: str, transport: str, has_hint: bool) -> None:
        try:
            text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else str(payload)
            if not text:
                return
            record = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "transport": transport,
                "symbol_context": context_symbol,
                "source_url": source_url,
                "has_kline_hint": bool(has_hint),
                "payload_preview": text[:4000],
            }
            with self.config.ws_frame_audit_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            LOGGER.debug("ws_frame_audit_failed error=%s", exc)

    async def _main_loop(self) -> None:
        start = time.monotonic()
        self._last_export_monotonic = start
        self._last_heartbeat_monotonic = start
        while not self.stop_event.is_set():
            await self._stimulate_chart_loading()
            if self.config.enable_endpoint_replay and self.config.replay_backfill_rounds > 0:
                await self._replay_discovered_endpoints()
            await self._periodic_export_and_heartbeat()
            if self.config.capture_seconds > 0 and time.monotonic() - start >= self.config.capture_seconds:
                LOGGER.info("capture_time_reached seconds=%s", self.config.capture_seconds)
                break
            await asyncio.sleep(1.0)

    async def _stimulate_chart_loading(self) -> None:
        for page in list(self._pages):
            if page.is_closed():
                continue
            try:
                await page.bring_to_front()
                await page.mouse.move(self.config.viewport_width // 2, self.config.viewport_height // 2)
                for _ in range(max(0, self.config.scroll_rounds)):
                    await page.mouse.wheel(-1_500, 0)
                    await page.keyboard.press("ArrowLeft")
                    await page.wait_for_timeout(int(self.config.scroll_pause_seconds * 1000))
                    if self.stop_event.is_set():
                        return
            except Exception as exc:
                LOGGER.debug("chart_stimulation_failed url=%s error=%s", page.url, exc)

    async def _replay_discovered_endpoints(self) -> None:
        if not self._context:
            return
        candidates = self.catalog.replay_candidates()
        if not candidates:
            return
        now = datetime.now(timezone.utc)
        for candidate in candidates[:20]:
            for round_idx in range(self.config.replay_backfill_rounds):
                if self.stop_event.is_set():
                    return
                window_end = now - timedelta(days=round_idx)
                window_start = window_end - timedelta(days=1)
                replay_url = self._rewrite_time_window(candidate, window_start, window_end)
                if not replay_url:
                    continue
                try:
                    response = await self._context.request.get(replay_url, timeout=self.config.page_timeout_ms)
                    if not response.ok:
                        continue
                    body = await response.body()
                    result = self.processor.process_payload(
                        body,
                        source_url=replay_url,
                        transport="http:replay",
                    )
                    if result.candles_found:
                        LOGGER.info(
                            "replay_candles_captured found=%s written=%s url=%s",
                            result.candles_found,
                            result.candles_written,
                            replay_url[:240],
                        )
                except Exception as exc:
                    LOGGER.debug("endpoint_replay_failed url=%s error=%s", replay_url[:240], exc)

    def _rewrite_time_window(self, url: str, start: datetime, end: datetime) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if not query:
            return None
        start_sec = int(start.timestamp())
        end_sec = int(end.timestamp())
        start_ms = start_sec * 1000
        end_ms = end_sec * 1000
        updated = False
        for key in list(query.keys()):
            lowered = key.lower()
            if lowered in {"from", "start", "start_time", "starttime"}:
                original = query[key][0] if query[key] else ""
                query[key] = [str(start_ms if len(str(original)) >= 13 else start_sec)]
                updated = True
            elif lowered in {"to", "end", "end_time", "endtime"}:
                original = query[key][0] if query[key] else ""
                query[key] = [str(end_ms if len(str(original)) >= 13 else end_sec)]
                updated = True
            elif lowered in {"limit", "size"}:
                query[key] = ["1500"]
                updated = True
        if not updated:
            return None
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    async def _periodic_export_and_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_export_monotonic >= self.config.export_every_seconds:
            try:
                summary = self.processor.export_all()
                self.catalog.save()
                LOGGER.info("export_completed summary=%s stats=%s", summary, self.processor.stats())
            except Exception as exc:
                LOGGER.warning("export_failed error=%s", exc)
            self._last_export_monotonic = now
        if now - self._last_heartbeat_monotonic >= self.config.heartbeat_seconds:
            LOGGER.info("heartbeat stats=%s", self.processor.stats())
            self._last_heartbeat_monotonic = now

    async def _shutdown(self) -> None:
        if self._response_tasks:
            await asyncio.gather(*list(self._response_tasks), return_exceptions=True)
        try:
            summary = self.processor.export_all()
            self.catalog.save()
            LOGGER.info("final_export_completed summary=%s stats=%s", summary, self.processor.stats())
        except Exception as exc:
            LOGGER.warning("final_export_failed error=%s", exc)
        for page in list(self._pages):
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.stop_event.set)
        except NotImplementedError:
            # Windows event loop may not support add_signal_handler.
            pass

    @staticmethod
    def _url_has_kline_hint(url: str) -> bool:
        lowered = url.lower()
        return any(hint in lowered for hint in KLINE_URL_HINTS)

    @staticmethod
    def _payload_or_url_has_kline_hint(payload: str | bytes, url: str) -> bool:
        if ScraperHandler._url_has_kline_hint(url):
            return True
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else str(payload)
        lowered = text.lower()[:4000]
        return any(hint in lowered for hint in KLINE_URL_HINTS) or all(token in lowered for token in ('"open"', '"high"', '"low"', '"close"'))
