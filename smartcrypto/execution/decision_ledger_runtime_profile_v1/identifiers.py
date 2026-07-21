"""Deterministic identifiers and idempotency keys for P0.4B projections."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def canonical_mapping_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _normalize(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def decision_idempotency_key(payload: Mapping[str, Any]) -> str:
    digest = canonical_mapping_sha256(payload)
    return f"decision:{digest}"


def trade_link_idempotency_key(
    *,
    decision_event_id: str,
    trade_id: int,
    source_row_fingerprint: str,
) -> str:
    digest = canonical_mapping_sha256(
        {
            "decision_event_id": decision_event_id,
            "trade_id": trade_id,
            "source_row_fingerprint": source_row_fingerprint,
        }
    )
    return f"trade-link:{digest}"


def event_id_from_idempotency_key(
    idempotency_key: str,
    *,
    prefix: str,
) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(idempotency_key):
        raise ValueError("idempotency_key_invalid")
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    event_id = f"{prefix}:{digest[:40]}"
    if not _IDENTIFIER_PATTERN.fullmatch(event_id):
        raise ValueError("event_id_invalid")
    return event_id


def normalize_symbol(pair: str) -> str:
    symbol = (
        pair.replace("/", "")
        .replace(":USDT", "")
        .replace(":USD", "")
        .replace("-", "")
        .upper()
    )
    if not symbol or not _IDENTIFIER_PATTERN.fullmatch(symbol):
        raise ValueError("normalized_symbol_invalid")
    return symbol


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_must_be_timezone_aware")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value
