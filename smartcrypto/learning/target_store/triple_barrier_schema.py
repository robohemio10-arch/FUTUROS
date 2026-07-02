"""Triple-barrier-compatible target schema for closed-trade derived labels."""

from __future__ import annotations

from typing import Any

TRIPLE_BARRIER_MODE = "closed_trade_derived_v1"
INTRABAR_PRICE_PATH_AVAILABLE = False
CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER = True

DEFAULT_TRIPLE_BARRIER_CONFIG: dict[str, Any] = {
    "mode": TRIPLE_BARRIER_MODE,
    "upper_barrier_pct": 0.01,
    "lower_barrier_pct": -0.01,
    "vertical_barrier_seconds": 86_400,
    "intrabar_price_path_available": INTRABAR_PRICE_PATH_AVAILABLE,
    "candle_path_required_for_full_triple_barrier": CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER,
    "full_triple_barrier_claimed": False,
}


def triple_barrier_config(
    *,
    upper_barrier_pct: float | None = None,
    lower_barrier_pct: float | None = None,
    vertical_barrier_seconds: int | None = None,
) -> dict[str, Any]:
    """Return deterministic triple-barrier schema configuration."""

    config = dict(DEFAULT_TRIPLE_BARRIER_CONFIG)
    if upper_barrier_pct is not None:
        config["upper_barrier_pct"] = float(upper_barrier_pct)
    if lower_barrier_pct is not None:
        config["lower_barrier_pct"] = float(lower_barrier_pct)
    if vertical_barrier_seconds is not None:
        config["vertical_barrier_seconds"] = int(vertical_barrier_seconds)
    return config
