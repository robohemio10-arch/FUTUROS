"""Point-in-time correlation lookup for W5 allocator constraints."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .contracts import CorrelationObservation


def _correlation_key(symbol_a: str, symbol_b: str) -> tuple[str, str]:
    """Return a deterministic two-symbol key with a precise static type."""

    if symbol_a <= symbol_b:
        return symbol_a, symbol_b
    return symbol_b, symbol_a


def build_correlation_lookup(
    observations: Iterable[CorrelationObservation],
    decision_time_utc: datetime,
) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for observation in observations:
        if observation.point_in_time_errors(decision_time_utc):
            continue
        key = _correlation_key(observation.symbol_a, observation.symbol_b)
        existing = lookup.get(key)
        if existing is not None and abs(existing - observation.correlation) > 1e-12:
            raise ValueError(f"conflicting_correlation_observation:{key[0]}:{key[1]}")
        lookup[key] = observation.correlation
    return lookup


def correlation_for(
    symbol_a: str,
    symbol_b: str,
    lookup: dict[tuple[str, str], float],
) -> float | None:
    if symbol_a == symbol_b:
        return 1.0
    return lookup.get(_correlation_key(symbol_a, symbol_b))
