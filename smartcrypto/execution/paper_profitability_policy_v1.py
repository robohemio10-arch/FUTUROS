"""Fail-closed directional and candidate policy for Paper profitability research."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, cast


Direction = Literal["long", "short", "no_trade"]

UP_REGIMES = frozenset({"trend_up", "trend_up_high_vol"})
DOWN_REGIMES = frozenset({"trend_down", "trend_down_high_vol"})


@dataclass(frozen=True)
class PaperCandidateProfileV1:
    profile_id: str = "paper-profitability-candidate-v1"
    long_probability: float = 0.55
    short_probability: float = 0.45
    regime_gate_enabled: bool = True
    cooldown_minutes: int = 0
    top_n_can_authorize_trade: bool = False
    decision_ledger_enabled: bool = True
    paper_only: bool = True
    live: bool = False
    canary: bool = False
    real_orders: bool = False
    exchange_private_access: bool = False
    model_promotion: bool = False
    changes_leverage: bool = False
    changes_stake: bool = False
    changes_roi: bool = False
    changes_stoploss: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DirectionDecisionV1:
    prob_up: float | None
    score: float | None
    confidence: float | None
    proposed_side: Direction
    status: Literal["ok", "blocked"]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidatePolicyDecisionV1:
    proposed_side: Direction
    market_regime: str
    market_regime_status: str
    regime_block: bool
    regime_block_reason: str | None
    cooldown_block: bool
    cooldown_block_reason: str | None
    final_decision: Literal["ALLOW_CANDIDATE", "BLOCK_CANDIDATE", "NO_TRADE"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_direction(
    prob_up: object,
    *,
    long_probability: float,
    short_probability: float,
) -> DirectionDecisionV1:
    """Resolve direction from the only permitted directional authority: ``prob_up``."""

    thresholds_valid = (
        _finite(long_probability)
        and _finite(short_probability)
        and 0.0 <= short_probability < 0.5 < long_probability <= 1.0
        and short_probability < long_probability
    )
    if not thresholds_valid:
        return DirectionDecisionV1(
            prob_up=None,
            score=None,
            confidence=None,
            proposed_side="no_trade",
            status="blocked",
            reason="invalid_probability_thresholds",
        )

    probability = _optional_probability(prob_up)
    if probability is None:
        return DirectionDecisionV1(
            prob_up=None,
            score=None,
            confidence=None,
            proposed_side="no_trade",
            status="blocked",
            reason="prob_up_missing_or_invalid",
        )

    if probability >= long_probability:
        side: Direction = "long"
        reason = "prob_up_at_or_above_long_threshold"
    elif probability <= short_probability:
        side = "short"
        reason = "prob_up_at_or_below_short_threshold"
    else:
        side = "no_trade"
        reason = "prob_up_inside_neutral_zone"

    return DirectionDecisionV1(
        prob_up=probability,
        score=(2.0 * probability) - 1.0,
        confidence=abs(probability - 0.5),
        proposed_side=side,
        status="ok",
        reason=reason,
    )


def evaluate_candidate_policy(
    *,
    proposed_side: str,
    market_regime: object,
    market_regime_status: object = "fresh",
    regime_gate_enabled: bool,
    observed_at: datetime,
    cooldown_until: datetime | None,
) -> CandidatePolicyDecisionV1:
    """Apply candidate-only regime and deterministic temporal cooldown gates."""

    side = str(proposed_side).strip().lower()
    regime = str(market_regime or "unknown").strip().lower() or "unknown"
    regime_status = str(market_regime_status or "unknown").strip().lower() or "unknown"
    observed = _as_utc(observed_at)
    resolved_cooldown = _as_utc(cooldown_until) if cooldown_until is not None else None

    if side not in {"long", "short"}:
        return CandidatePolicyDecisionV1(
            proposed_side="no_trade",
            market_regime=regime,
            market_regime_status=regime_status,
            regime_block=False,
            regime_block_reason=None,
            cooldown_block=False,
            cooldown_block_reason=None,
            final_decision="NO_TRADE",
        )

    regime_block = False
    regime_reason: str | None = None
    if regime_gate_enabled:
        if regime_status not in {"fresh", "point_in_time"} or regime in {"", "unknown"}:
            regime_block = True
            regime_reason = "market_regime_unknown_or_stale"
        elif side == "short" and regime in UP_REGIMES:
            regime_block = True
            regime_reason = "counter_trend_short_blocked"
        elif side == "long" and regime in DOWN_REGIMES:
            regime_block = True
            regime_reason = "counter_trend_long_blocked"

    cooldown_block = resolved_cooldown is not None and observed < resolved_cooldown
    cooldown_reason = "same_symbol_side_stoploss_cooldown_active" if cooldown_block else None
    blocked = regime_block or cooldown_block
    return CandidatePolicyDecisionV1(
        proposed_side=cast(Direction, side),
        market_regime=regime,
        market_regime_status=regime_status,
        regime_block=regime_block,
        regime_block_reason=regime_reason,
        cooldown_block=cooldown_block,
        cooldown_block_reason=cooldown_reason,
        final_decision="BLOCK_CANDIDATE" if blocked else "ALLOW_CANDIDATE",
    )


def cooldown_deadline(close_time: datetime, cooldown_minutes: int) -> datetime:
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes_must_be_non_negative")
    return _as_utc(close_time) + timedelta(minutes=cooldown_minutes)


def build_minimum_decision_ledger_context(
    signal: Mapping[str, Any],
    *,
    final_decision: str,
    risk_approved: bool,
) -> dict[str, Any]:
    """Build auditable minimum context carried into the existing ledger projection."""

    return {
        "timestamp": signal.get("generated_at"),
        "symbol": signal.get("symbol"),
        "prob_up": signal.get("prob_up"),
        "score": signal.get("score"),
        "confidence": signal.get("confidence"),
        "proposed_side": signal.get("proposed_side"),
        "market_regime": signal.get("market_regime", "unknown"),
        "regime_block": bool(signal.get("regime_block", False)),
        "cooldown_block": bool(signal.get("cooldown_block", False)),
        "risk_approved": bool(risk_approved),
        "final_decision": final_decision,
        "signal_id": signal.get("signal_id"),
        "decision_event_id": signal.get("decision_event_id"),
        "trade_id": signal.get("trade_id"),
    }


def _optional_probability(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return False


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp_must_be_datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)
