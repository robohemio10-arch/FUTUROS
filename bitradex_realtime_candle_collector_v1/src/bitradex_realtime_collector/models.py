from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_url: str
    transport: str
    captured_at: datetime
    raw_hash: str

    def normalized_key(self) -> tuple[str, str, str]:
        return (
            self.symbol.upper(),
            self.timeframe.lower(),
            self.timestamp.astimezone(timezone.utc).isoformat(),
        )

    def as_record(self) -> dict[str, Any]:
        ts = self.timestamp.astimezone(timezone.utc)
        captured = self.captured_at.astimezone(timezone.utc)
        return {
            "symbol": self.symbol.upper(),
            "timeframe": self.timeframe.lower(),
            "timestamp": ts.isoformat(),
            "timestamp_ms": int(ts.timestamp() * 1000),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
            "source_url": self.source_url,
            "transport": self.transport,
            "captured_at": captured.isoformat(),
            "raw_hash": self.raw_hash,
        }
