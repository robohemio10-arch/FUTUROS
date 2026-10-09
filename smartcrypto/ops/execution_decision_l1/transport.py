"""Fixed public HTTPS GET with a killable child deadline, including DNS lookup."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .contracts import Symbol, require_utc

# Child has no project imports, proxy/credential discovery, redirects or private routes.
PUBLIC_FETCH_SCRIPT = r"""
import http.client, json, sys
from datetime import datetime, timezone
symbol, timeout = sys.argv[1], float(sys.argv[2])
if symbol not in ("BTCUSDT", "ETHUSDT"):
    raise SystemExit(2)
connection = http.client.HTTPSConnection("fapi.binance.com", timeout=timeout)
try:
    connection.request("GET", "/fapi/v1/ticker/bookTicker?symbol=" + symbol,
                       headers={"Accept": "application/json"})
    response = connection.getresponse()
    raw = response.read(16385)
    received = datetime.now(timezone.utc).isoformat()
    if response.status != 200:
        retry = response.getheader("Retry-After", "0")
        try:
            retry = max(0, min(float(retry), 3600))
        except ValueError:
            retry = 60
        print(json.dumps({"status": "failed", "reason": "http_" + str(response.status),
                          "retry_after_seconds": retry}))
    elif len(raw) > 16384:
        print(json.dumps({"status": "failed", "reason": "response_size_limit"}))
    else:
        print(json.dumps({"status": "ok", "raw_body": raw.decode("utf-8"),
                          "receive_time_utc": received}))
except (OSError, http.client.HTTPException, UnicodeError) as exc:
    print(json.dumps({"status": "failed", "reason": type(exc).__name__}))
finally:
    connection.close()
"""


@dataclass(frozen=True)
class ReceivedTicker:
    symbol: Symbol
    raw_body: str
    receive_time_utc: datetime


class FetchError(ValueError):
    def __init__(self, reason: str, retry_after_seconds: float = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


class PublicClient(Protocol):
    def fetch(self, symbol: Symbol, timeout: float) -> ReceivedTicker:
        """Only a public snapshot, with a bounded transport deadline."""


class PublicHTTPClient:
    def fetch(self, symbol: Symbol, timeout: float) -> ReceivedTicker:
        if symbol not in ("BTCUSDT", "ETHUSDT") or not 0 < timeout <= 10:
            raise FetchError("invalid_public_request")
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", PUBLIC_FETCH_SCRIPT, symbol, str(timeout)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError("public_request_deadline") from exc
        except OSError as exc:
            raise FetchError(f"public_client_{type(exc).__name__}") from exc
        if result.returncode != 0 or len(result.stdout) > 32768:
            raise FetchError("public_client_invalid_output")
        try:
            payload = json.loads(result.stdout)
            if payload["status"] != "ok":
                raise FetchError(
                    str(payload["reason"]), float(payload.get("retry_after_seconds", 0))
                )
            receive = require_utc(datetime.fromisoformat(payload["receive_time_utc"]))
            return ReceivedTicker(symbol, str(payload["raw_body"]), receive)
        except FetchError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise FetchError("public_client_schema_invalid") from exc
