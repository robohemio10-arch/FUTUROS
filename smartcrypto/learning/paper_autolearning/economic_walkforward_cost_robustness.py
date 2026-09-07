"""Economic walk-forward robustness with incremental execution-cost stress.

Research-only and fail-closed. ``net_pnl`` remains the authoritative realized result;
only an additional execution stress is applied. No model, runtime, risk or order state
is changed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autolearning_economic_walkforward_cost_robustness_v1"
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
DEFAULT_SELECTOR_FIELD = "paper_candidate_filter_decision"

FOLD_COUNT = 3
INITIAL_CONTEXT_FRACTION = 0.40
MIN_TOTAL_TRADES = 300
MIN_INITIAL_CONTEXT_TRADES = 120
MIN_FOLD_TRADES = 50
MIN_SELECTED_TRADES_PER_FOLD = 20
MIN_SELECTOR_COVERAGE = 0.95
MIN_STRESS_NOTIONAL_COVERAGE = 0.90
MIN_CAPITAL_HOUR_COVERAGE = 0.80
DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS = 5.0
MIN_TREATMENT_PROFIT_FACTOR = 1.10
MIN_POSITIVE_FOLDS = 2
MAX_DRAWDOWN_RATIO_TO_BASELINE = 1.05

_ALLOWED_SELECTOR_VALUES = {
    "1",
    "true",
    "allow",
    "allowed",
    "pass",
    "passed",
    "approve",
    "approved",
    "permit",
    "permitted",
}


@dataclass(frozen=True)
class FoldSpec:
    fold_id: str
    test_start: int
    test_end: int


def build_paper_autolearning_economic_walkforward_cost_robustness_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    outcome_path: str | Path | None = None,
    selector_field: str = DEFAULT_SELECTOR_FIELD,
    additional_execution_stress_bps: float = DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
) -> dict[str, Any]:
    """Evaluate selector economics across three chronological forward folds."""

    stress_bps = float(additional_execution_stress_bps)
    if not math.isfinite(stress_bps) or stress_bps < 0:
        raise ValueError("additional_execution_stress_bps_must_be_finite_and_non_negative")

    root = Path(project_root).resolve()
    source = _resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)
    input_rows = [dict(row) for row in rows] if rows is not None else _read_rows(source)
    normalized, invalid_time_count = _normalize_rows(input_rows)

    blockers: list[str] = []
    if not normalized:
        blockers.append("no_valid_closed_outcomes")
    if invalid_time_count:
        blockers.append("unparseable_close_time_detected")
    if len(normalized) < MIN_TOTAL_TRADES:
        blockers.append("min_total_trades_not_met")

    selector_coverage = _coverage_ratio(
        normalized,
        lambda row: _has_selector_value(row.get(selector_field)),
    )
    if normalized and selector_coverage < MIN_SELECTOR_COVERAGE:
        blockers.append("selector_coverage_not_met")

    fold_specs = _build_fold_specs(len(normalized))
    if len(fold_specs) != FOLD_COUNT:
        blockers.append("walkforward_fold_count_not_met")

    fold_results: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []
    evaluation_selected: list[dict[str, Any]] = []

    for spec in fold_specs:
        train_context = normalized[: spec.test_start]
        test_rows = normalized[spec.test_start : spec.test_end]
        selected_rows = [
            row for row in test_rows if _is_selected(row.get(selector_field))
        ]
        evaluation_rows.extend(test_rows)
        evaluation_selected.extend(selected_rows)

        baseline_metrics = _metrics(test_rows, stress_bps)
        treatment_metrics = _metrics(selected_rows, stress_bps)
        fold_blockers = _fold_blockers(
            fold_id=spec.fold_id,
            train_context=train_context,
            test_rows=test_rows,
            selected_rows=selected_rows,
            baseline_metrics=baseline_metrics,
            treatment_metrics=treatment_metrics,
        )
        blockers.extend(fold_blockers)

        delta_net_pnl = (
            treatment_metrics["stressed_net_pnl_total"]
            - baseline_metrics["stressed_net_pnl_total"]
        )
        expectancy_uplift = (
            treatment_metrics["stressed_expectancy"]
            - baseline_metrics["stressed_expectancy"]
        )
        capital_efficiency_uplift = _difference_or_none(
            treatment_metrics["stressed_net_pnl_per_capital_hour"],
            baseline_metrics["stressed_net_pnl_per_capital_hour"],
        )

        fold_results.append(
            {
                "fold_id": spec.fold_id,
                "train_context_trade_count": len(train_context),
                "test_trade_count": len(test_rows),
                "selected_trade_count": len(selected_rows),
                "train_context_end_utc": (
                    _time_value(train_context[-1]) if train_context else None
                ),
                "test_start_utc": _time_value(test_rows[0]) if test_rows else None,
                "test_end_utc": _time_value(test_rows[-1]) if test_rows else None,
                "chronological_boundary_valid": _boundary_is_valid(
                    train_context,
                    test_rows,
                ),
                "baseline_metrics": _public_metrics(baseline_metrics),
                "treatment_metrics": _public_metrics(treatment_metrics),
                "delta_stressed_net_pnl": round(delta_net_pnl, 10),
                "delta_stressed_expectancy": round(expectancy_uplift, 10),
                "delta_stressed_net_pnl_per_capital_hour": (
                    None
                    if capital_efficiency_uplift is None
                    else round(capital_efficiency_uplift, 10)
                ),
                "blockers": sorted(set(fold_blockers)),
            }
        )

    aggregate_baseline = _metrics(evaluation_rows, stress_bps)
    aggregate_treatment = _metrics(evaluation_selected, stress_bps)
    aggregate_delta_net_pnl = (
        aggregate_treatment["stressed_net_pnl_total"]
        - aggregate_baseline["stressed_net_pnl_total"]
    )
    aggregate_expectancy_uplift = (
        aggregate_treatment["stressed_expectancy"]
        - aggregate_baseline["stressed_expectancy"]
    )
    aggregate_capital_efficiency_uplift = _difference_or_none(
        aggregate_treatment["stressed_net_pnl_per_capital_hour"],
        aggregate_baseline["stressed_net_pnl_per_capital_hour"],
    )
    aggregate_drawdown_ratio = _safe_ratio(
        aggregate_treatment["stressed_max_drawdown_abs"],
        aggregate_baseline["stressed_max_drawdown_abs"],
    )

    positive_delta_net_pnl_fold_count = sum(
        1 for item in fold_results if item["delta_stressed_net_pnl"] > 0
    )
    positive_expectancy_uplift_fold_count = sum(
        1 for item in fold_results if item["delta_stressed_expectancy"] > 0
    )
    positive_treatment_net_pnl_fold_count = sum(
        1
        for item in fold_results
        if item["treatment_metrics"]["stressed_net_pnl_total"] > 0
    )

    blockers.extend(
        _aggregate_blockers(
            baseline_metrics=aggregate_baseline,
            treatment_metrics=aggregate_treatment,
            delta_net_pnl=aggregate_delta_net_pnl,
            expectancy_uplift=aggregate_expectancy_uplift,
            capital_efficiency_uplift=aggregate_capital_efficiency_uplift,
            drawdown_ratio=aggregate_drawdown_ratio,
            positive_delta_net_pnl_fold_count=positive_delta_net_pnl_fold_count,
            positive_expectancy_uplift_fold_count=(
                positive_expectancy_uplift_fold_count
            ),
            positive_treatment_net_pnl_fold_count=(
                positive_treatment_net_pnl_fold_count
            ),
        )
    )

    unique_blockers = sorted(set(blockers))
    robust = not unique_blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if robust else "blocked",
        "reason": (
            "walkforward_cost_robust_economic_edge_research_only"
            if robust
            else unique_blockers[0]
        ),
        "decision": (
            "WALKFORWARD_COST_ROBUST_RESEARCH_ONLY"
            if robust
            else "MANTER_EM_RESEARCH"
        ),
        "source_path": str(source),
        "selector_field": selector_field,
        "input_row_count": len(input_rows),
        "valid_closed_outcome_count": len(normalized),
        "invalid_close_time_count": invalid_time_count,
        "selector_coverage": selector_coverage,
        "fold_count": len(fold_results),
        "walkforward_method": "expanding_context_three_chronological_test_folds",
        "initial_context_fraction": INITIAL_CONTEXT_FRACTION,
        "additional_execution_stress_bps": stress_bps,
        "pnl_basis": "authoritative_net_pnl_plus_incremental_execution_stress_only",
        "fees_and_funding_reconstructed": False,
        "fees_or_funding_double_counted": False,
        "treatment_semantics": (
            "selected_trades_execute_blocked_trades_zero_pnl_zero_capital"
        ),
        "folds": fold_results,
        "aggregate_baseline_metrics": _public_metrics(aggregate_baseline),
        "aggregate_treatment_metrics": _public_metrics(aggregate_treatment),
        "aggregate_delta_stressed_net_pnl": round(aggregate_delta_net_pnl, 10),
        "aggregate_delta_stressed_expectancy": round(
            aggregate_expectancy_uplift,
            10,
        ),
        "aggregate_delta_stressed_net_pnl_per_capital_hour": (
            None
            if aggregate_capital_efficiency_uplift is None
            else round(aggregate_capital_efficiency_uplift, 10)
        ),
        "aggregate_drawdown_ratio_to_baseline": aggregate_drawdown_ratio,
        "positive_delta_net_pnl_fold_count": positive_delta_net_pnl_fold_count,
        "positive_expectancy_uplift_fold_count": positive_expectancy_uplift_fold_count,
        "positive_treatment_net_pnl_fold_count": positive_treatment_net_pnl_fold_count,
        "thresholds": {
            "fold_count": FOLD_COUNT,
            "min_total_trades": MIN_TOTAL_TRADES,
            "min_initial_context_trades": MIN_INITIAL_CONTEXT_TRADES,
            "min_fold_trades": MIN_FOLD_TRADES,
            "min_selected_trades_per_fold": MIN_SELECTED_TRADES_PER_FOLD,
            "min_selector_coverage": MIN_SELECTOR_COVERAGE,
            "min_stress_notional_coverage": MIN_STRESS_NOTIONAL_COVERAGE,
            "min_capital_hour_coverage": MIN_CAPITAL_HOUR_COVERAGE,
            "min_treatment_profit_factor": MIN_TREATMENT_PROFIT_FACTOR,
            "min_positive_folds": MIN_POSITIVE_FOLDS,
            "max_drawdown_ratio_to_baseline": MAX_DRAWDOWN_RATIO_TO_BASELINE,
        },
        "blockers": unique_blockers,
        "anti_leakage": True,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_runtime": False,
        "write_performed": False,
    }


def _fold_blockers(
    *,
    fold_id: str,
    train_context: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    selected_rows: Sequence[Mapping[str, Any]],
    baseline_metrics: Mapping[str, Any],
    treatment_metrics: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if len(train_context) < MIN_INITIAL_CONTEXT_TRADES:
        blockers.append(f"{fold_id}:min_initial_context_trades_not_met")
    if len(test_rows) < MIN_FOLD_TRADES:
        blockers.append(f"{fold_id}:min_fold_trades_not_met")
    if len(selected_rows) < MIN_SELECTED_TRADES_PER_FOLD:
        blockers.append(f"{fold_id}:min_selected_trades_not_met")
    if not _boundary_is_valid(train_context, test_rows):
        blockers.append(f"{fold_id}:chronological_boundary_invalid")
    if baseline_metrics["stress_notional_coverage"] < MIN_STRESS_NOTIONAL_COVERAGE:
        blockers.append(f"{fold_id}:baseline_stress_notional_coverage_not_met")
    if treatment_metrics["stress_notional_coverage"] < MIN_STRESS_NOTIONAL_COVERAGE:
        blockers.append(f"{fold_id}:treatment_stress_notional_coverage_not_met")
    if baseline_metrics["capital_hour_coverage"] < MIN_CAPITAL_HOUR_COVERAGE:
        blockers.append(f"{fold_id}:baseline_capital_hour_coverage_not_met")
    if treatment_metrics["capital_hour_coverage"] < MIN_CAPITAL_HOUR_COVERAGE:
        blockers.append(f"{fold_id}:treatment_capital_hour_coverage_not_met")
    return blockers


def _aggregate_blockers(
    *,
    baseline_metrics: Mapping[str, Any],
    treatment_metrics: Mapping[str, Any],
    delta_net_pnl: float,
    expectancy_uplift: float,
    capital_efficiency_uplift: float | None,
    drawdown_ratio: float | None,
    positive_delta_net_pnl_fold_count: int,
    positive_expectancy_uplift_fold_count: int,
    positive_treatment_net_pnl_fold_count: int,
) -> list[str]:
    blockers: list[str] = []
    treatment_pf = treatment_metrics["stressed_profit_factor_numeric"]

    if treatment_metrics["stressed_net_pnl_total"] <= 0:
        blockers.append("aggregate_positive_treatment_net_pnl_not_met")
    if treatment_metrics["stressed_expectancy"] <= 0:
        blockers.append("aggregate_positive_treatment_expectancy_not_met")
    if treatment_pf is None or treatment_pf < MIN_TREATMENT_PROFIT_FACTOR:
        blockers.append("aggregate_treatment_profit_factor_not_met")
    if delta_net_pnl <= 0:
        blockers.append("aggregate_delta_net_pnl_not_met")
    if expectancy_uplift <= 0:
        blockers.append("aggregate_expectancy_uplift_not_met")
    if capital_efficiency_uplift is None or capital_efficiency_uplift <= 0:
        blockers.append("aggregate_capital_efficiency_uplift_not_met")

    baseline_drawdown = baseline_metrics["stressed_max_drawdown_abs"]
    treatment_drawdown = treatment_metrics["stressed_max_drawdown_abs"]
    if baseline_drawdown == 0 and treatment_drawdown > 0:
        blockers.append("aggregate_drawdown_worse_than_zero_drawdown_baseline")
    elif drawdown_ratio is not None and drawdown_ratio > MAX_DRAWDOWN_RATIO_TO_BASELINE:
        blockers.append("aggregate_drawdown_ratio_to_baseline_exceeded")

    if positive_delta_net_pnl_fold_count < MIN_POSITIVE_FOLDS:
        blockers.append("positive_delta_net_pnl_fold_count_not_met")
    if positive_expectancy_uplift_fold_count < MIN_POSITIVE_FOLDS:
        blockers.append("positive_expectancy_uplift_fold_count_not_met")
    if positive_treatment_net_pnl_fold_count < MIN_POSITIVE_FOLDS:
        blockers.append("positive_treatment_net_pnl_fold_count_not_met")
    return blockers


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        raise ValueError(f"unsupported_outcome_format:{path.suffix.lower()}")
    return frame.to_dict(orient="records")


def _normalize_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    normalized: list[dict[str, Any]] = []
    invalid_time_count = 0
    for raw in rows:
        row = dict(raw)
        if row.get("is_closed") is False:
            continue
        pnl = _to_float(row.get("net_pnl"))
        if pnl is None or not math.isfinite(pnl):
            continue
        close_time = _parse_time(row.get("close_time_utc") or row.get("close_time"))
        if close_time is None:
            invalid_time_count += 1
            continue
        row["net_pnl"] = pnl
        row["__economic_close_time"] = close_time
        normalized.append(row)
    normalized.sort(key=lambda row: row["__economic_close_time"])
    return normalized, invalid_time_count


def _build_fold_specs(count: int) -> list[FoldSpec]:
    if count <= 0:
        return []
    context_count = max(
        MIN_INITIAL_CONTEXT_TRADES,
        math.floor(count * INITIAL_CONTEXT_FRACTION),
    )
    if context_count >= count:
        return []
    evaluation_count = count - context_count
    base_size, remainder = divmod(evaluation_count, FOLD_COUNT)
    specs: list[FoldSpec] = []
    start = context_count
    for index in range(FOLD_COUNT):
        size = base_size + (1 if index < remainder else 0)
        end = start + size
        specs.append(
            FoldSpec(
                fold_id=f"fold_{index + 1:02d}",
                test_start=start,
                test_end=end,
            )
        )
        start = end
    return specs


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    stress_bps: float,
) -> dict[str, Any]:
    observed_pnls: list[float] = []
    stressed_pnls: list[float] = []
    stress_cost_total = 0.0
    stress_covered_count = 0
    capital_hour_count = 0
    capital_hours_total = 0.0
    capital_hour_stressed_net = 0.0
    trading_fee_sum = 0.0
    funding_fee_sum = 0.0

    for row in rows:
        observed = float(row["net_pnl"])
        notional = _notional(row)
        stress_cost = 0.0
        if notional is not None:
            stress_covered_count += 1
            stress_cost = notional * stress_bps / 10_000.0
        stressed = observed - stress_cost

        observed_pnls.append(observed)
        stressed_pnls.append(stressed)
        stress_cost_total += stress_cost
        trading_fee_sum += _to_float(row.get("trading_fee")) or 0.0
        funding_fee_sum += _to_float(row.get("funding_fee")) or 0.0

        capital_hours = _capital_hours(row, notional)
        if capital_hours is not None:
            capital_hour_count += 1
            capital_hours_total += capital_hours
            capital_hour_stressed_net += stressed

    count = len(rows)
    stressed_rate = (
        capital_hour_stressed_net / capital_hours_total
        if capital_hours_total > 0
        else None
    )
    return {
        "trade_count": count,
        "observed_net_pnl_total": round(sum(observed_pnls), 10),
        "observed_expectancy": (
            round(sum(observed_pnls) / count, 10) if count else 0.0
        ),
        "observed_profit_factor_numeric": _profit_factor(observed_pnls),
        "observed_max_drawdown_abs": round(_max_drawdown_abs(observed_pnls), 10),
        "additional_execution_stress_cost_total": round(stress_cost_total, 10),
        "stress_notional_coverage": (
            round(stress_covered_count / count, 10) if count else 0.0
        ),
        "stressed_net_pnl_total": round(sum(stressed_pnls), 10),
        "stressed_expectancy": (
            round(sum(stressed_pnls) / count, 10) if count else 0.0
        ),
        "stressed_profit_factor_numeric": _profit_factor(stressed_pnls),
        "stressed_max_drawdown_abs": round(_max_drawdown_abs(stressed_pnls), 10),
        "capital_hour_coverage": (
            round(capital_hour_count / count, 10) if count else 0.0
        ),
        "capital_hours_total": round(capital_hours_total, 10),
        "capital_hour_covered_stressed_net_pnl": round(
            capital_hour_stressed_net,
            10,
        ),
        "stressed_net_pnl_per_capital_hour": (
            None if stressed_rate is None else round(stressed_rate, 10)
        ),
        "observed_trading_fee_sum": round(trading_fee_sum, 10),
        "observed_funding_fee_sum": round(funding_fee_sum, 10),
    }


def _public_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": metrics["trade_count"],
        "observed_net_pnl_total": metrics["observed_net_pnl_total"],
        "observed_expectancy": metrics["observed_expectancy"],
        "observed_profit_factor": _public_profit_factor(
            metrics["observed_profit_factor_numeric"]
        ),
        "observed_max_drawdown_abs": metrics["observed_max_drawdown_abs"],
        "additional_execution_stress_cost_total": metrics[
            "additional_execution_stress_cost_total"
        ],
        "stress_notional_coverage": metrics["stress_notional_coverage"],
        "stressed_net_pnl_total": metrics["stressed_net_pnl_total"],
        "stressed_expectancy": metrics["stressed_expectancy"],
        "stressed_profit_factor": _public_profit_factor(
            metrics["stressed_profit_factor_numeric"]
        ),
        "stressed_max_drawdown_abs": metrics["stressed_max_drawdown_abs"],
        "capital_hour_coverage": metrics["capital_hour_coverage"],
        "capital_hours_total": metrics["capital_hours_total"],
        "capital_hour_covered_stressed_net_pnl": metrics[
            "capital_hour_covered_stressed_net_pnl"
        ],
        "stressed_net_pnl_per_capital_hour": metrics[
            "stressed_net_pnl_per_capital_hour"
        ],
        "observed_trading_fee_sum": metrics["observed_trading_fee_sum"],
        "observed_funding_fee_sum": metrics["observed_funding_fee_sum"],
    }


def _profit_factor(pnls: Sequence[float]) -> float | None:
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss_abs = abs(sum(value for value in pnls if value < 0))
    if gross_loss_abs == 0:
        return float("inf") if gross_profit > 0 else None
    return gross_profit / gross_loss_abs


def _public_profit_factor(value: Any) -> float | str | None:
    if value is None:
        return None
    numeric = float(value)
    return "inf" if math.isinf(numeric) else round(numeric, 10)


def _max_drawdown_abs(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _notional(row: Mapping[str, Any]) -> float | None:
    notional = _to_float(row.get("notional"))
    if notional is not None and math.isfinite(notional) and abs(notional) > 0:
        return abs(notional)

    quantity = _to_float(row.get("quantity"))
    entry_price = _to_float(row.get("entry_price"))
    if (
        quantity is None
        or entry_price is None
        or not math.isfinite(quantity)
        or not math.isfinite(entry_price)
        or quantity == 0
        or entry_price <= 0
    ):
        return None
    return abs(quantity * entry_price)


def _capital_hours(
    row: Mapping[str, Any],
    notional: float | None,
) -> float | None:
    if notional is None:
        return None
    leverage = _to_float(row.get("leverage"))
    if leverage is None or not math.isfinite(leverage) or leverage <= 0:
        return None
    duration_hours = _duration_hours(row)
    if duration_hours is None or duration_hours <= 0:
        return None
    return (notional / leverage) * duration_hours


def _duration_hours(row: Mapping[str, Any]) -> float | None:
    duration_seconds = _to_float(row.get("duration_seconds"))
    if (
        duration_seconds is not None
        and math.isfinite(duration_seconds)
        and duration_seconds > 0
    ):
        return duration_seconds / 3600.0

    open_time = _parse_time(row.get("open_time_utc") or row.get("open_time"))
    close_time = row.get("__economic_close_time")
    if open_time is None or not isinstance(close_time, datetime):
        return None
    seconds = (close_time - open_time).total_seconds()
    return seconds / 3600.0 if seconds > 0 else None


def _parse_time(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _boundary_is_valid(
    train_context: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
) -> bool:
    if not train_context or not test_rows:
        return False
    return train_context[-1]["__economic_close_time"] <= test_rows[0][
        "__economic_close_time"
    ]


def _time_value(row: Mapping[str, Any]) -> str | None:
    value = row.get("__economic_close_time")
    return value.isoformat() if isinstance(value, datetime) else None


def _is_selected(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 1:
        return True
    return str(value).strip().lower() in _ALLOWED_SELECTOR_VALUES


def _has_selector_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _coverage_ratio(
    rows: Sequence[Mapping[str, Any]],
    predicate: Callable[[Mapping[str, Any]], bool],
) -> float:
    if not rows:
        return 0.0
    covered = sum(1 for row in rows if predicate(row))
    return round(covered / len(rows), 10)


def _difference_or_none(
    left: float | None,
    right: float | None,
) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 10)


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
