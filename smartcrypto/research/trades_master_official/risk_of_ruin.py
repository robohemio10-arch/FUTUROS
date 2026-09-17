"""Frozen WQ5 path-risk and conditional risk-of-ruin metrics.

This module implements the path-level risk semantics frozen for post-OCR WQ5.
It is PAPER / SHADOW / RESEARCH ONLY. It has no operational authority, does
not modify RiskManager, does not submit orders and does not write runtime state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NoReturn, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray


FloatArray: TypeAlias = NDArray[np.float64]
BoolArray: TypeAlias = NDArray[np.bool_]
IntArray: TypeAlias = NDArray[np.int32]

BUFFER_MULTIPLES: tuple[float, ...] = (
    1.0,
    1.5,
    2.0,
    3.0,
    5.0,
)


class OfficialRiskOfRuinValidationError(ValueError):
    """Fail-closed validation error for frozen WQ5 path-risk calculations."""

    def __init__(
        self,
        code: str,
        **details: Any,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _fail(
    code: str,
    **details: Any,
) -> NoReturn:
    raise OfficialRiskOfRuinValidationError(
        code,
        **details,
    )


def _float_array(
    value: object,
) -> FloatArray:
    return cast(
        FloatArray,
        np.asarray(
            value,
            dtype=np.float64,
        ),
    )


def _bool_array(
    value: object,
) -> BoolArray:
    return cast(
        BoolArray,
        np.asarray(
            value,
            dtype=np.bool_,
        ),
    )


def historical_max_drawdown(
    pnl: FloatArray,
) -> float:
    """Return absolute MDD from cumulative PnL with virtual initial equity zero."""

    values = _float_array(pnl)

    if values.ndim != 1:
        _fail(
            "historical_pnl_not_one_dimensional",
            shape=values.shape,
        )

    if values.size == 0:
        _fail(
            "historical_pnl_empty",
        )

    if not np.isfinite(values).all():
        _fail(
            "historical_pnl_nonfinite",
        )

    equity = _float_array(
        np.cumsum(
            values,
            dtype=np.float64,
        )
    )

    initial_zero = _float_array([0.0])

    running_peak = _float_array(
        np.maximum.accumulate(
            np.concatenate(
                (
                    initial_zero,
                    equity,
                )
            )
        )[1:]
    )

    drawdown = _float_array(running_peak - equity)

    return float(drawdown.max())


def longest_true_run(
    mask: BoolArray,
) -> IntArray:
    """Return longest consecutive True run independently for each path."""

    values = _bool_array(mask)

    if values.ndim != 2:
        _fail(
            "run_mask_not_two_dimensional",
            shape=values.shape,
        )

    current = cast(
        IntArray,
        np.zeros(
            values.shape[0],
            dtype=np.int32,
        ),
    )

    maximum = cast(
        IntArray,
        np.zeros(
            values.shape[0],
            dtype=np.int32,
        ),
    )

    for column in range(values.shape[1]):
        current = cast(
            IntArray,
            np.where(
                values[:, column],
                current + 1,
                0,
            ).astype(
                np.int32,
                copy=False,
            ),
        )

        maximum = cast(
            IntArray,
            np.maximum(
                maximum,
                current,
            ),
        )

    return maximum


def calculate_path_metrics(
    paths: FloatArray,
    *,
    buffer_anchor: float,
    buffer_multiples: Sequence[float] = BUFFER_MULTIPLES,
) -> dict[str, FloatArray]:
    """Calculate frozen WQ5 terminal, DD, streak, underwater and ruin metrics."""

    values = _float_array(paths)

    if values.ndim != 2 or values.shape[0] < 1 or values.shape[1] < 1:
        _fail(
            "paths_invalid_shape",
            shape=values.shape,
        )

    if not np.isfinite(values).all():
        _fail(
            "paths_nonfinite",
        )

    anchor = float(buffer_anchor)

    if not np.isfinite(anchor) or anchor <= 0.0:
        _fail(
            "buffer_anchor_invalid",
            value=anchor,
        )

    multiples = tuple(float(value) for value in buffer_multiples)

    if not multiples:
        _fail(
            "buffer_multiples_empty",
        )

    if any(not np.isfinite(value) or value <= 0.0 for value in multiples):
        _fail(
            "buffer_multiples_invalid",
            values=multiples,
        )

    equity = _float_array(
        np.cumsum(
            values,
            axis=1,
            dtype=np.float64,
        )
    )

    zero = _float_array(
        np.zeros(
            (
                values.shape[0],
                1,
            ),
            dtype=np.float64,
        )
    )

    peak = _float_array(
        np.maximum.accumulate(
            np.concatenate(
                (
                    zero,
                    equity,
                ),
                axis=1,
            ),
            axis=1,
        )[:, 1:]
    )

    drawdown = _float_array(peak - equity)

    minimum_equity = _float_array(equity.min(axis=1))

    losing_mask = _bool_array(values < 0.0)

    underwater_mask = _bool_array(equity < peak)

    losing_streak = _float_array(longest_true_run(losing_mask).astype(np.float64))

    time_under_water = _float_array(longest_true_run(underwater_mask).astype(np.float64))

    metrics: dict[
        str,
        FloatArray,
    ] = {
        "terminal": _float_array(equity[:, -1]),
        "max_drawdown": _float_array(drawdown.max(axis=1)),
        "losing_streak": (losing_streak),
        "time_under_water": (time_under_water),
    }

    for multiple in multiples:
        ruin_event = _float_array((minimum_equity <= -(anchor * multiple)).astype(np.float64))

        metrics[f"ruin_{multiple:g}"] = ruin_event

    return metrics


def _metric_vector(
    metrics: Mapping[
        str,
        FloatArray,
    ],
    key: str,
    *,
    expected_size: int | None = None,
) -> FloatArray:
    if key not in metrics:
        _fail(
            "path_metric_missing",
            metric=key,
        )

    values = _float_array(metrics[key])

    if values.ndim != 1:
        _fail(
            "path_metric_not_one_dimensional",
            metric=key,
            shape=values.shape,
        )

    if expected_size is not None and values.size != expected_size:
        _fail(
            "path_metric_size_mismatch",
            metric=key,
            expected=expected_size,
            actual=int(values.size),
        )

    if not np.isfinite(values).all():
        _fail(
            "path_metric_nonfinite",
            metric=key,
        )

    return values


def summarize_path_metrics(
    metrics: Mapping[
        str,
        FloatArray,
    ],
) -> dict[str, Any]:
    """Summarize frozen WQ5 path metrics using NumPy linear quantiles."""

    terminal = _metric_vector(
        metrics,
        "terminal",
    )

    if terminal.size < 1:
        _fail(
            "terminal_metrics_empty",
        )

    expected_size = int(terminal.size)

    drawdown = _metric_vector(
        metrics,
        "max_drawdown",
        expected_size=expected_size,
    )

    losing_streak = _metric_vector(
        metrics,
        "losing_streak",
        expected_size=expected_size,
    )

    time_under_water = _metric_vector(
        metrics,
        "time_under_water",
        expected_size=expected_size,
    )

    losses = _float_array(-terminal)

    var95 = float(
        np.quantile(
            losses,
            0.95,
            method="linear",
        )
    )

    var99 = float(
        np.quantile(
            losses,
            0.99,
            method="linear",
        )
    )

    tail95 = _float_array(losses[losses >= var95])

    tail99 = _float_array(losses[losses >= var99])

    ruin: dict[
        str,
        float,
    ] = {}

    for key in sorted(metrics):
        if not key.startswith("ruin_"):
            continue

        values = _metric_vector(
            metrics,
            key,
            expected_size=expected_size,
        )

        probability = float(values.mean())

        if not np.isfinite(probability) or probability < 0.0 or probability > 1.0:
            _fail(
                "ruin_probability_invalid",
                metric=key,
                value=probability,
            )

        ruin[key.removeprefix("ruin_")] = probability

    return {
        "simulation_count": (expected_size),
        "terminal_p01": float(
            np.quantile(
                terminal,
                0.01,
                method="linear",
            )
        ),
        "terminal_p05": float(
            np.quantile(
                terminal,
                0.05,
                method="linear",
            )
        ),
        "terminal_p50": float(
            np.quantile(
                terminal,
                0.50,
                method="linear",
            )
        ),
        "terminal_p95": float(
            np.quantile(
                terminal,
                0.95,
                method="linear",
            )
        ),
        "terminal_p99": float(
            np.quantile(
                terminal,
                0.99,
                method="linear",
            )
        ),
        "p_final_net_pnl_gt_zero": float(np.mean(terminal > 0.0)),
        "max_drawdown_p50": float(
            np.quantile(
                drawdown,
                0.50,
                method="linear",
            )
        ),
        "max_drawdown_p90": float(
            np.quantile(
                drawdown,
                0.90,
                method="linear",
            )
        ),
        "max_drawdown_p95": float(
            np.quantile(
                drawdown,
                0.95,
                method="linear",
            )
        ),
        "max_drawdown_p99": float(
            np.quantile(
                drawdown,
                0.99,
                method="linear",
            )
        ),
        "terminal_loss_cvar_95": float(tail95.mean() if tail95.size else var95),
        "terminal_loss_cvar_99": float(tail99.mean() if tail99.size else var99),
        "losing_streak_p95": float(
            np.quantile(
                losing_streak,
                0.95,
                method="linear",
            )
        ),
        "time_under_water_p95_trade_steps": float(
            np.quantile(
                time_under_water,
                0.95,
                method="linear",
            )
        ),
        "risk_of_ruin": ruin,
    }


def primary_ruin_probability(
    scenario_results: Mapping[
        str,
        Mapping[str, Any],
    ],
    *,
    multiple: str = "2",
) -> float:
    """Return max ruin probability across IID and B20 primary methods."""

    primary_methods = (
        "baseline_iid",
        "baseline_block_b20",
    )

    probabilities: list[float] = []

    for method in primary_methods:
        if method not in scenario_results:
            _fail(
                "primary_ruin_scenario_missing",
                scenario=method,
            )

        scenario = scenario_results[method]

        risk_raw = scenario.get("risk_of_ruin")

        if not isinstance(
            risk_raw,
            Mapping,
        ):
            _fail(
                "primary_ruin_grid_missing",
                scenario=method,
            )

        risk = cast(
            Mapping[
                str,
                Any,
            ],
            risk_raw,
        )

        if multiple not in risk:
            _fail(
                "primary_ruin_multiple_missing",
                scenario=method,
                multiple=multiple,
            )

        probability = float(risk[multiple])

        if not np.isfinite(probability) or probability < 0.0 or probability > 1.0:
            _fail(
                "primary_ruin_probability_invalid",
                scenario=method,
                multiple=multiple,
                value=probability,
            )

        probabilities.append(probability)

    return max(probabilities)


__all__ = [
    "BUFFER_MULTIPLES",
    "OfficialRiskOfRuinValidationError",
    "calculate_path_metrics",
    "historical_max_drawdown",
    "longest_true_run",
    "primary_ruin_probability",
    "summarize_path_metrics",
]
