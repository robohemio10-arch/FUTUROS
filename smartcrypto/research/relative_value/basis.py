"""Spot-perpetual basis calculations for W7 research."""

from __future__ import annotations

from typing import Literal

from .contracts import BasisScenario


def basis_bps(spot_price: float, perp_price: float) -> float:
    if spot_price <= 0 or perp_price <= 0:
        raise ValueError("basis_prices_must_be_positive")
    return (perp_price / spot_price - 1.0) * 10_000.0


def convergence_direction(
    value_bps: float,
) -> Literal["LONG_SPOT_SHORT_PERP", "SHORT_SPOT_LONG_PERP"]:
    return "LONG_SPOT_SHORT_PERP" if value_bps >= 0 else "SHORT_SPOT_LONG_PERP"


def convergence_gross_bps(scenario: BasisScenario) -> float:
    return abs(basis_bps(scenario.spot.price, scenario.perp.price)) * scenario.convergence_capture_fraction


def directional_convergence_bps(
    *,
    basis_value_bps: float,
    trade_direction: Literal["LONG_SPOT_SHORT_PERP", "SHORT_SPOT_LONG_PERP"],
    capture_fraction: float,
) -> float:
    if not 0.0 <= capture_fraction <= 1.0:
        raise ValueError("capture_fraction_out_of_range")
    natural_direction = convergence_direction(basis_value_bps)
    magnitude = abs(basis_value_bps) * capture_fraction
    return magnitude if trade_direction == natural_direction else -magnitude
