from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_symbol(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).upper().strip()
    text = text.replace(":USDT", "")
    text = text.replace("/", "")
    text = text.replace("-", "")
    return text


def normalize_pair(value: str | None) -> str:
    symbol = normalize_symbol(value)
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return str(value or "").strip()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent), suffix=".tmp") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
        tmp_name = fh.name
    os.replace(tmp_name, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def active_signals(payload: dict[str, Any] | None, now: datetime | None = None) -> list[dict[str, Any]]:
    if not payload:
        return []
    now = now or utc_now()
    raw = payload.get("signals", [])
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("risk_approved", False):
            continue
        valid_until = parse_dt(item.get("valid_until"))
        if valid_until and valid_until < now:
            continue
        pair = normalize_pair(item.get("pair") or item.get("symbol"))
        if not pair:
            continue
        item = dict(item)
        item["pair"] = pair
        item["symbol"] = normalize_symbol(pair)
        result.append(item)
    return result


def load_first_active(paths: list[Path]) -> tuple[dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    for path in paths:
        payload = read_json(path)
        signals = active_signals(payload)
        if signals:
            return payload, path, signals
    for path in paths:
        payload = read_json(path)
        if payload is not None:
            return payload, path, []
    return None, None, []


def pin_signal_contract(primary_path: Path, pinned_path: Path, *, validity_minutes: int = 30) -> dict[str, Any]:
    primary = read_json(primary_path)
    pinned = read_json(pinned_path)
    primary_signals = active_signals(primary)
    pinned_signals = active_signals(pinned)

    if primary_signals:
        payload = dict(primary or {})
        payload["signals"] = primary_signals
        payload["generated_at"] = payload.get("generated_at") or utc_now().isoformat()
        atomic_write_json(pinned_path, payload)
        return {
            "status": "ok",
            "action": "pinned_from_primary",
            "signals": len(primary_signals),
            "primary_path": str(primary_path),
            "pinned_path": str(pinned_path),
        }

    if pinned_signals:
        payload = dict(pinned or {})
        payload["signals"] = pinned_signals
        payload["generated_at"] = utc_now().isoformat()
        for signal in payload["signals"]:
            signal["valid_until"] = (utc_now() + timedelta(minutes=validity_minutes)).isoformat()
        atomic_write_json(primary_path, payload)
        atomic_write_json(pinned_path, payload)
        return {
            "status": "ok",
            "action": "restored_primary_from_pinned",
            "signals": len(payload["signals"]),
            "primary_path": str(primary_path),
            "pinned_path": str(pinned_path),
        }

    return {
        "status": "blocked",
        "action": "no_active_signals_to_pin",
        "signals": 0,
        "primary_path": str(primary_path),
        "pinned_path": str(pinned_path),
    }


@dataclass(frozen=True)
class SignalLookup:
    payload: dict[str, Any] | None
    path: Path | None
    signals: list[dict[str, Any]]

    def for_pair(self, pair: str) -> dict[str, Any] | None:
        wanted_pair = normalize_pair(pair)
        wanted_symbol = normalize_symbol(pair)
        for signal in self.signals:
            if normalize_pair(signal.get("pair")) == wanted_pair:
                return signal
            if normalize_symbol(signal.get("symbol")) == wanted_symbol:
                return signal
        return None


def load_signal_lookup(paths: list[Path]) -> SignalLookup:
    payload, path, signals = load_first_active(paths)
    return SignalLookup(payload=payload, path=path, signals=signals)
