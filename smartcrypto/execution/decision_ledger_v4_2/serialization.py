"""Canonical JSON serialization and hashing for decision-ledger payload 4.2."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel


def canonical_payload_mapping(
    payload: BaseModel | Mapping[str, Any],
    *,
    include_payload_hash: bool,
) -> dict[str, Any]:
    """Return a recursively normalized mapping suitable for canonical JSON."""

    raw: Mapping[str, Any]
    if isinstance(payload, BaseModel):
        raw = payload.model_dump(mode="python")
    else:
        raw = payload

    normalized = _normalize_value(dict(raw))
    if not isinstance(normalized, dict):
        raise TypeError("payload_must_normalize_to_mapping")

    if not include_payload_hash:
        normalized.pop("payload_sha256", None)
    return normalized


def canonical_json_bytes(
    payload: BaseModel | Mapping[str, Any],
    *,
    include_payload_hash: bool = True,
) -> bytes:
    """Serialize with deterministic key order, separators, UTF-8, and no NaN."""

    normalized = canonical_payload_mapping(
        payload,
        include_payload_hash=include_payload_hash,
    )
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8")


def compute_payload_sha256(payload: BaseModel | Mapping[str, Any]) -> str:
    """Hash the canonical payload while excluding the payload_sha256 field."""

    return hashlib.sha256(
        canonical_json_bytes(payload, include_payload_hash=False)
    ).hexdigest()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_must_be_timezone_aware_utc")
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]

    return value
