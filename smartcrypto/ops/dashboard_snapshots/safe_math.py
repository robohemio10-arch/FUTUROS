from __future__ import annotations

import math
from collections.abc import Iterable
from numbers import Real


def _finite_values(values: Iterable[object] | None) -> list[float]:
    if values is None:
        return []
    output: list[float] = []
    for value in values:
        if value is None or isinstance(value, bool) or not isinstance(value, Real):
            continue
        number = float(value)
        if math.isfinite(number):
            output.append(number)
    return output


def safe_div(numerator: object, denominator: object, default: float = 0.0) -> float:
    values = _finite_values([numerator, denominator])
    if len(values) != 2 or values[1] == 0.0:
        return default
    result = values[0] / values[1]
    return result if math.isfinite(result) else default


def safe_mean(values: Iterable[object] | None, default: float = 0.0) -> float:
    clean = _finite_values(values)
    return sum(clean) / len(clean) if clean else default


def safe_quantile(values: Iterable[object] | None, q: object, default: float = 0.0) -> float:
    clean = sorted(_finite_values(values))
    quantiles = _finite_values([q])
    if not clean or not quantiles or not 0.0 <= quantiles[0] <= 1.0:
        return default
    position = (len(clean) - 1) * quantiles[0]
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1.0 - weight) + clean[upper] * weight


def safe_std(values: Iterable[object] | None, default: float = 0.0) -> float:
    clean = _finite_values(values)
    if not clean:
        return default
    mean = safe_mean(clean)
    return math.sqrt(sum((value - mean) ** 2 for value in clean) / len(clean))


def safe_sum(values: Iterable[object] | None, default: float = 0.0) -> float:
    clean = _finite_values(values)
    return sum(clean) if clean else default


def safe_min(values: Iterable[object] | None, default: float = 0.0) -> float:
    clean = _finite_values(values)
    return min(clean) if clean else default


def safe_max(values: Iterable[object] | None, default: float = 0.0) -> float:
    clean = _finite_values(values)
    return max(clean) if clean else default


def clamp(value: object, min_value: float, max_value: float) -> float:
    clean = _finite_values([value])
    if not clean:
        return min_value
    return min(max(clean[0], min_value), max_value)


def safe_pct(numerator: object, denominator: object, default: float = 0.0) -> float:
    return safe_div(numerator, denominator, default=default) * 100.0


def calculate_cvar(
    returns: Iterable[object] | None,
    alpha: float,
    equity: float = 1.0,
    default: float = 0.0,
) -> float:
    clean = _finite_values(returns)
    equity_values = _finite_values([equity])
    if not clean or not equity_values:
        return default
    quantile = safe_quantile(clean, alpha, default=math.nan)
    if not math.isfinite(quantile):
        return default
    tail = [value for value in clean if value <= quantile]
    return -safe_mean(tail, default=0.0) * equity_values[0]


def safe_cvar(
    returns: Iterable[object] | None,
    alpha: float,
    equity: float = 1.0,
    default: float = 0.0,
) -> float:
    return calculate_cvar(returns, alpha, equity, default)


def calculate_backoff_seconds(
    base_backoff_seconds: float,
    retry_count: int,
    max_backoff_seconds: float,
) -> float:
    if retry_count < 0:
        retry_count = 0
    return min(base_backoff_seconds * (2**retry_count), max_backoff_seconds)


def exponential_backoff_seconds(
    base_backoff_seconds: float,
    retry_count: int,
    max_backoff_seconds: float,
) -> float:
    return calculate_backoff_seconds(base_backoff_seconds, retry_count, max_backoff_seconds)


current_backoff_seconds = calculate_backoff_seconds
