from __future__ import annotations

import math

import pytest

from smartcrypto.ops.dashboard_snapshots.safe_math import (
    calculate_backoff_seconds,
    calculate_cvar,
    safe_div,
    safe_mean,
    safe_quantile,
    safe_std,
    safe_sum,
)


def test_safe_div_handles_normal_zero_none_and_non_finite_values() -> None:
    assert safe_div(6, 3) == 2.0
    assert safe_div(6, 0, default=-1.0) == -1.0
    assert safe_div(None, 3, default=-2.0) == -2.0
    assert safe_div(math.inf, 2, default=-3.0) == -3.0


def test_safe_aggregations_ignore_invalid_values_and_handle_empty_inputs() -> None:
    values = [1.0, None, 3.0, math.nan, math.inf]
    assert safe_mean(values) == 2.0
    assert safe_sum(values) == 4.0
    assert safe_mean([], default=-1.0) == -1.0
    assert safe_sum([], default=-2.0) == -2.0
    assert safe_std([1, 2, 3]) == pytest.approx(math.sqrt(2 / 3))
    assert safe_std([], default=-3.0) == -3.0


def test_safe_quantile_uses_linear_interpolation_and_validates_q() -> None:
    assert safe_quantile([1, 2, 3, 4], 0.5) == 2.5
    assert safe_quantile([], 0.5, default=-1.0) == -1.0
    assert safe_quantile([1, 2], 2.0, default=-2.0) == -2.0


def test_cvar_uses_lower_tail_quantile_and_equity() -> None:
    returns = [-0.10, -0.05, 0.01, 0.04]
    assert calculate_cvar(returns, alpha=0.25, equity=1000.0) == pytest.approx(100.0)


def test_backoff_uses_power_of_two_and_respects_cap() -> None:
    assert calculate_backoff_seconds(5, retry_count=0, max_backoff_seconds=60) == 5
    assert calculate_backoff_seconds(5, retry_count=3, max_backoff_seconds=60) == 40
    assert calculate_backoff_seconds(5, retry_count=5, max_backoff_seconds=60) == 60
