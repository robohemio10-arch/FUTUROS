"""Immutable contracts for the research-only shadow opportunity engine."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
VALID_SIDES = frozenset({"LONG", "SHORT"})
TIMEFRAME_SECONDS = {"15s": 15, "1m": 60, "5m": 300}


def utc_iso(value: Any) -> str | None:
    """Return a canonical UTC timestamp or None for invalid input."""

    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_seconds(value: Any) -> float | None:
    normalized = utc_iso(value)
    if normalized is None:
        return None
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()


def finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def normalize_symbol(value: Any) -> str:
    raw = str(value or "").upper().strip()
    raw = raw.replace(":USDT", "").replace("/", "").replace("-", "")
    return raw


def normalize_side(value: Any) -> str | None:
    normalized = str(value or "").upper().strip()
    aliases = {"BUY": "LONG", "SELL": "SHORT"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in VALID_SIDES else None


def valid_sha256(value: Any) -> bool:
    return bool(SHA256_PATTERN.fullmatch(str(value or "").strip()))


def stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        dict(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(rendered).hexdigest()}"


@dataclass(frozen=True)
class MarketEvidence:
    symbol: str
    timeframe: str
    candle_timestamp_utc: str | None
    available_at_utc: str | None
    generated_at_utc: str | None
    observed_at_utc: str | None
    source_hash: str | None
    source_row_identity: str | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    market_regime: str | None = None
    regime_method: str | None = None
    regime_lookback: str | None = None

    def lineage(self) -> dict[str, Any]:
        candle = epoch_seconds(self.candle_timestamp_utc)
        available = epoch_seconds(self.available_at_utc)
        generated = epoch_seconds(self.generated_at_utc)
        observed = epoch_seconds(self.observed_at_utc)
        timestamps = {
            "candle": candle,
            "available": available,
            "generated": generated,
            "observed": observed,
        }
        missing = sorted(key for key, value in timestamps.items() if value is None)
        errors: list[str] = []
        if missing:
            errors.append("market_lineage_timestamp_missing_or_invalid")
        elif not (
            candle is not None
            and available is not None
            and generated is not None
            and observed is not None
            and candle <= available <= observed
            and candle <= generated <= observed
        ):
            errors.append("future_or_out_of_order_market_evidence")
        if self.timeframe not in TIMEFRAME_SECONDS:
            errors.append("unsupported_timeframe")
        if not valid_sha256(self.source_hash):
            errors.append("market_source_hash_missing_or_invalid")
        if not str(self.source_row_identity or "").strip():
            errors.append("market_source_row_identity_missing")
        return {
            "status": "ok" if not errors else "blocked",
            "valid": not errors,
            "errors": errors,
            "timeframe": self.timeframe,
            "source_hash": self.source_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "lineage": self.lineage()}


@dataclass(frozen=True)
class PositionSnapshot:
    trade_id: int
    pair: str
    symbol: str
    side: str
    open_date: str
    stake_amount: float
    leverage: float
    open_rate: float
    max_rate: float | None
    min_rate: float | None
    position_age_seconds: float
    capital_locked_usdt: float
    capital_hours: float
    estimated_notional_usdt: float
    remaining_position_ev: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "capital_hours_basis": "stake_amount_usdt_times_position_age_hours",
            "estimated_notional_basis": "stake_amount_usdt_times_leverage",
            "remaining_position_ev_status": (
                "AVAILABLE" if self.remaining_position_ev is not None else "SOURCE_MISSING"
            ),
        }


@dataclass(frozen=True)
class CandidateObservation:
    candidate_id: str
    observed_at_utc: str
    symbol: str
    side: str
    source_hash: str
    source_row_identity: str
    candidate_integrity_valid: bool
    lineage_status: str
    candidate_actionable_shadow: bool
    market_lineage_valid: bool
    score_lineage_valid: bool
    regime_lineage_valid: bool
    ranking_score: float | None
    ranking_score_source_field: str | None
    prob_up: float | None
    qlib_score: float | None
    signal_confidence: float | None
    candidate_ev: float | None
    candidate_ev_status: str
    model_version: str | None
    score_generated_at_utc: str | None
    score_available_at_utc: str | None
    engine_observed_at_utc: str
    regime: str | None
    regime_method: str | None
    regime_lookback: str | None
    regime_generated_at_utc: str | None
    regime_available_at_utc: str | None
    regime_source_hash: str | None
    regime_source_timeframe: str | None
    lineage_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "lineage_errors": list(self.lineage_errors),
            "ranking_score_semantics": "NON_FINANCIAL_ORDINAL",
        }
