from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m")


SYMBOL_TO_PATH_TOKEN: dict[str, str] = {
    "BTCUSDT": "btc_usdt",
    "ETHUSDT": "eth_usdt",
}


TIMEFRAME_SECONDS: dict[str, int] = {
    "15s": 15,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime configuration for the collector.

    The collector intentionally does not hold credentials and does not perform
    account/private calls. It only observes public UI traffic emitted by the
    Bitradex trading chart page.
    """

    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    base_urls: tuple[str, ...] = (
        # Prefer canonical www host. In V1, non-www could redirect to
        # https://www.bitradex.ai/ and silently lose the futures route.
        "https://www.bitradex.ai/en/futures/trade/{pair_token}",
        "https://bitradex.ai/en/futures/trade/{pair_token}",
        "https://www.bitradex.com/en/futures/trade/{pair_token}",
        "https://bitradex.com/en/futures/trade/{pair_token}",
        # Extra SPA fallbacks. They are accepted only if route validation confirms
        # that the page still contains the requested pair/futures context.
        "https://www.bitradex.ai/en/futures/{pair_token}",
        "https://www.bitradex.ai/futures/trade/{pair_token}",
    )
    output_dir: Path = Path("data/output")
    raw_dir: Path = Path("data/raw")
    runtime_dir: Path = Path("data/runtime")
    log_dir: Path = Path("logs")
    sqlite_path: Path = Path("data/output/bitradex_live_candles.sqlite")
    discovered_endpoints_path: Path = Path("data/runtime/discovered_endpoints.json")
    raw_payload_jsonl: Path = Path("data/raw/captured_payloads.jsonl")
    capture_seconds: int = 0
    export_every_seconds: int = 60
    heartbeat_seconds: int = 30
    page_timeout_ms: int = 45_000
    response_body_timeout_seconds: float = 8.0
    max_payload_bytes: int = 5_000_000
    scroll_rounds: int = 30
    scroll_pause_seconds: float = 0.35
    replay_backfill_rounds: int = 0
    headless: bool = True
    browser_channel: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    viewport_width: int = 1365
    viewport_height: int = 768
    locale: str = "en-US"
    timezone_id: str = "UTC"
    mirror_phase22_dir: Path | None = None
    enable_raw_payload_audit: bool = True
    enable_endpoint_replay: bool = True
    min_export_rows: int = 1
    enable_ticker_aggregation: bool = True
    allow_live_candle_updates: bool = True
    process_all_public_ws_frames: bool = True
    save_ws_frame_audit: bool = True
    ws_frame_audit_path: Path = Path("data/raw/captured_ws_frames.jsonl")
    network_audit_path: Path = Path("data/raw/network_audit.jsonl")
    audit_all_network: bool = False
    route_validation_required: bool = True
    direct_probe_days: int = 3
    direct_probe_limit: int = 1500
    direct_probe_concurrency: int = 6

    def ensure_directories(self) -> None:
        for path in (self.output_dir, self.raw_dir, self.runtime_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
        if self.sqlite_path.parent:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        if self.discovered_endpoints_path.parent:
            self.discovered_endpoints_path.parent.mkdir(parents=True, exist_ok=True)
        if self.raw_payload_jsonl.parent:
            self.raw_payload_jsonl.parent.mkdir(parents=True, exist_ok=True)
        if self.ws_frame_audit_path.parent:
            self.ws_frame_audit_path.parent.mkdir(parents=True, exist_ok=True)
        if self.network_audit_path.parent:
            self.network_audit_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mirror_phase22_dir:
            self.mirror_phase22_dir.mkdir(parents=True, exist_ok=True)

    def trading_urls(self) -> list[tuple[str, str]]:
        urls: list[tuple[str, str]] = []
        for symbol in self.symbols:
            token = SYMBOL_TO_PATH_TOKEN.get(symbol.upper())
            if not token:
                raise ValueError(f"Unsupported symbol for Bitradex path mapping: {symbol}")
            for template in self.base_urls:
                urls.append((symbol.upper(), template.format(pair_token=token)))
        return urls

    @staticmethod
    def normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(str(s).upper().replace("/", "").replace("_", "") for s in symbols)
        unsupported = [s for s in normalized if s not in SYMBOL_TO_PATH_TOKEN]
        if unsupported:
            raise ValueError(f"Unsupported symbols: {unsupported}. Supported: {sorted(SYMBOL_TO_PATH_TOKEN)}")
        return normalized

    @staticmethod
    def normalize_timeframes(timeframes: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(str(tf).lower().strip() for tf in timeframes)
        unsupported = [tf for tf in normalized if tf not in TIMEFRAME_SECONDS]
        if unsupported:
            raise ValueError(f"Unsupported timeframes: {unsupported}. Supported: {sorted(TIMEFRAME_SECONDS)}")
        return normalized
