"""Cross-asset relative-value calculations."""

from __future__ import annotations

import math
from typing import Literal

from .contracts import PairRelativeValueScenario


def log_return(anchor_price: float, current_price: float) -> float:
    if anchor_price <= 0 or current_price <= 0:
        raise ValueError("pair_prices_must_be_positive")
    return math.log(current_price / anchor_price)


def relative_value_spread_bps(scenario: PairRelativeValueScenario) -> float:
    ret_a = log_return(scenario.leg_a_anchor.price, scenario.leg_a_current.price)
    ret_b = log_return(scenario.leg_b_anchor.price, scenario.leg_b_current.price)
    return (ret_a - scenario.beta_a_to_b * ret_b) * 10_000.0


def relative_value_direction(
    spread_bps: float,
) -> Literal["LONG_A_SHORT_B", "SHORT_A_LONG_B"]:
    # Positive spread means A outperformed beta-adjusted B; convergence research
    # therefore studies short-A/long-B, and vice versa.
    return "SHORT_A_LONG_B" if spread_bps >= 0 else "LONG_A_SHORT_B"


def convergence_gross_bps(scenario: PairRelativeValueScenario) -> float:
    return abs(relative_value_spread_bps(scenario)) * scenario.convergence_capture_fraction
