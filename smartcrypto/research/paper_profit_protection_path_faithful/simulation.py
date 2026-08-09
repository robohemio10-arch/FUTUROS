"""Causal candle-by-candle profit-protection validation with untouched holdout."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from smartcrypto.research.paper_profit_maximization.metrics import (
    finite_or_none,
    prepare_profit_dataset,
    profit_metrics,
    sort_trades,
)
from smartcrypto.research.profit_research_dataset.candle_alignment import (
    timeframe_seconds,
)

from .contracts import (
    EXIT_SLIPPAGE_BPS,
    FIXED_PROTECTION_CANDIDATES,
    HOLDOUT_RATIO,
    INITIAL_DEVELOPMENT_TRAIN_RATIO,
    MIN_ELIGIBLE_TRADES,
    MIN_HOLDOUT_TRADES,
    MIN_WALKFORWARD_POSITIVE_FOLDS,
    WALKFORWARD_FOLD_COUNT,
)


def validate_path_faithful_candidates(
    frame: pd.DataFrame,
    *,
    paths_by_trade: Mapping[str, pd.DataFrame],
    timeframe: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Freeze candidate choice pre-holdout, then evaluate one champion on holdout."""
    prepared, preparation = prepare_profit_dataset(frame)
    eligible = sort_trades(
        prepared.loc[prepared["profit_optimization_eligible"]].copy()
    ).reset_index(drop=True)
    if len(eligible) < MIN_ELIGIBLE_TRADES:
        return prepared, _blocked_report(
            "insufficient_profit_eligible_trades",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    holdout_count = max(MIN_HOLDOUT_TRADES, int(math.ceil(len(eligible) * HOLDOUT_RATIO)))
    if holdout_count >= len(eligible):
        return prepared, _blocked_report(
            "insufficient_trades_for_untouched_holdout",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    development = eligible.iloc[:-holdout_count].copy().reset_index(drop=True)
    holdout = eligible.iloc[-holdout_count:].copy().reset_index(drop=True)
    folds = build_walkforward_folds(len(development))
    if len(folds) != WALKFORWARD_FOLD_COUNT:
        return prepared, _blocked_report(
            "walkforward_fold_construction_failed",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    candidate_reports = [
        _evaluate_development_candidate(
            development,
            paths_by_trade=paths_by_trade,
            timeframe=timeframe,
            candidate=candidate,
            folds=folds,
        )
        for candidate in FIXED_PROTECTION_CANDIDATES
    ]
    ranked = rank_development_candidates(candidate_reports)
    champion = next(
        (
            item
            for item in ranked
            if item.get("development_decision") == "FREEZE_FOR_HOLDOUT"
        ),
        None,
    )
    if champion is None:
        annotated = _annotate_partitions(prepared, development, holdout)
        return annotated, {
            "status": "ok",
            "reason": "no_candidate_passed_walkforward_freeze_gate",
            "eligible_trade_count": int(len(eligible)),
            "development_trade_count": int(len(development)),
            "holdout_trade_count": int(len(holdout)),
            "walkforward_folds": folds,
            "development_candidates": ranked,
            "frozen_champion": None,
            "holdout_evaluation": None,
            "path_faithful_validation_passed": False,
            "ready_for_paper_wiring": False,
            "preparation": preparation,
        }

    champion_config = _candidate_config(str(champion["candidate_id"]))
    holdout_simulated, holdout_diag = simulate_candidate_frame(
        holdout,
        paths_by_trade=paths_by_trade,
        timeframe=timeframe,
        trigger_mfe_pct=float(champion_config["trigger_mfe_pct"]),
        retention_fraction=float(champion_config["retention_fraction_of_mfe"]),
    )
    holdout_baseline_metrics = profit_metrics(holdout)
    holdout_candidate_metrics = profit_metrics(holdout_simulated)
    holdout_delta = float(holdout_candidate_metrics["net_pnl"]) - float(
        holdout_baseline_metrics["net_pnl"]
    )
    holdout_passed = _holdout_gate(
        holdout_candidate_metrics,
        baseline=holdout_baseline_metrics,
        delta_pnl=holdout_delta,
        path_coverage_ratio=float(holdout_diag["path_coverage_ratio"]),
    )
    annotated = _annotate_partitions(prepared, development, holdout)
    return annotated, {
        "status": "ok",
        "reason": "path_faithful_walkforward_holdout_completed",
        "eligible_trade_count": int(len(eligible)),
        "development_trade_count": int(len(development)),
        "holdout_trade_count": int(len(holdout)),
        "walkforward_folds": folds,
        "development_candidates": ranked,
        "frozen_champion": champion,
        "holdout_evaluation": {
            "candidate_id": champion["candidate_id"],
            "baseline_metrics": holdout_baseline_metrics,
            "candidate_metrics": holdout_candidate_metrics,
            "delta_pnl": holdout_delta,
            "path_diagnostics": holdout_diag,
            "holdout_passed": holdout_passed,
        },
        "path_faithful_validation_passed": holdout_passed,
        "ready_for_paper_wiring": holdout_passed,
        "selection_contract": {
            "candidate_count": len(FIXED_PROTECTION_CANDIDATES),
            "candidate_search_expanded": False,
            "selection_uses_holdout": False,
            "holdout_evaluated_after_champion_freeze": True,
            "holdout_ratio": HOLDOUT_RATIO,
            "walkforward_fold_count": WALKFORWARD_FOLD_COUNT,
            "required_positive_walkforward_folds": MIN_WALKFORWARD_POSITIVE_FOLDS,
        },
        "execution_model": {
            "running_mfe_is_causal": True,
            "intrabar_order": "adverse_first_then_favorable",
            "partial_entry_exit_candles_excluded": True,
            "gap_through_stop_fill": "candle_open_if_worse_than_floor",
            "observed_fees_charged": True,
            "positive_funding_cost_charged": True,
            "funding_credit_ignored": True,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "slippage_optimized": False,
        },
        "preparation": preparation,
    }


def build_walkforward_folds(development_count: int) -> list[dict[str, int]]:
    """Build expanding-history validation folds entirely before final holdout."""
    if development_count < WALKFORWARD_FOLD_COUNT + 2:
        return []
    initial_train = max(1, int(math.floor(development_count * INITIAL_DEVELOPMENT_TRAIN_RATIO)))
    remaining = development_count - initial_train
    if remaining < WALKFORWARD_FOLD_COUNT:
        return []
    folds: list[dict[str, int]] = []
    for fold_index in range(WALKFORWARD_FOLD_COUNT):
        validation_start = initial_train + int(
            math.floor(remaining * fold_index / WALKFORWARD_FOLD_COUNT)
        )
        validation_end = initial_train + int(
            math.floor(remaining * (fold_index + 1) / WALKFORWARD_FOLD_COUNT)
        )
        if validation_end <= validation_start:
            return []
        folds.append(
            {
                "fold_index": fold_index + 1,
                "train_start": 0,
                "train_end_exclusive": validation_start,
                "validation_start": validation_start,
                "validation_end_exclusive": validation_end,
            }
        )
    return folds


def rank_development_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank without any holdout information."""
    return sorted(
        [dict(item) for item in candidates],
        key=lambda item: (
            item.get("development_decision") != "FREEZE_FOR_HOLDOUT",
            -int(item.get("positive_walkforward_fold_count", 0)),
            -_sort_float(item.get("walkforward_total_delta_pnl")),
            -_sort_float(item.get("walkforward_candidate_net_pnl")),
            -_sort_float(item.get("walkforward_profit_factor")),
            _sort_float(item.get("walkforward_maximum_drawdown")),
            str(item.get("candidate_id")),
        ),
    )


def simulate_candidate_frame(
    frame: pd.DataFrame,
    *,
    paths_by_trade: Mapping[str, pd.DataFrame],
    timeframe: str,
    trigger_mfe_pct: float,
    retention_fraction: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply one fixed causal policy to a copy of the observed trade frame."""
    output = frame.copy()
    output["observed_net_pnl"] = pd.to_numeric(output["net_pnl"], errors="coerce")
    output["protection_exit_simulated"] = False
    output["protection_exit_price"] = pd.NA
    output["protection_path_complete"] = False
    output["protection_boundary_candles_excluded"] = 0
    output["protection_intrabar_ambiguous_count"] = 0
    output["protection_gap_through_count"] = 0

    aggregate = {
        "trade_count": int(len(output)),
        "path_complete_trade_count": 0,
        "path_missing_trade_count": 0,
        "stop_hit_trade_count": 0,
        "boundary_candles_excluded": 0,
        "intrabar_ambiguous_count": 0,
        "gap_through_stop_count": 0,
        "winner_to_loser_saved_count": 0,
        "winner_harmed_count": 0,
        "gross_pnl_improvement": 0.0,
    }

    for index, trade in output.iterrows():
        stable_id = str(trade.get("stable_trade_id"))
        path = paths_by_trade.get(stable_id)
        result = simulate_trade_path(
            trade,
            path=path,
            timeframe=timeframe,
            trigger_mfe_pct=trigger_mfe_pct,
            retention_fraction=retention_fraction,
        )
        observed = finite_or_none(trade.get("net_pnl"))
        candidate_net = result["candidate_net_pnl"]
        output.at[index, "net_pnl"] = candidate_net
        output.at[index, "protection_exit_simulated"] = result["stop_hit"]
        output.at[index, "protection_exit_price"] = result["exit_price"]
        output.at[index, "protection_path_complete"] = result["path_complete"]
        output.at[index, "protection_boundary_candles_excluded"] = result[
            "boundary_candles_excluded"
        ]
        output.at[index, "protection_intrabar_ambiguous_count"] = result[
            "intrabar_ambiguous_count"
        ]
        output.at[index, "protection_gap_through_count"] = result["gap_through_count"]

        if result["path_complete"]:
            aggregate["path_complete_trade_count"] += 1
        else:
            aggregate["path_missing_trade_count"] += 1
        if result["stop_hit"]:
            aggregate["stop_hit_trade_count"] += 1
        aggregate["boundary_candles_excluded"] += int(result["boundary_candles_excluded"])
        aggregate["intrabar_ambiguous_count"] += int(result["intrabar_ambiguous_count"])
        aggregate["gap_through_stop_count"] += int(result["gap_through_count"])
        if observed is not None:
            aggregate["gross_pnl_improvement"] += float(candidate_net) - observed
            if observed < 0 and candidate_net > observed:
                aggregate["winner_to_loser_saved_count"] += 1
            if observed > 0 and candidate_net < observed:
                aggregate["winner_harmed_count"] += 1

    aggregate["path_coverage_ratio"] = (
        float(aggregate["path_complete_trade_count"]) / len(output) if len(output) else 0.0
    )
    return output, aggregate


def simulate_trade_path(
    trade: pd.Series,
    *,
    path: pd.DataFrame | None,
    timeframe: str,
    trigger_mfe_pct: float,
    retention_fraction: float,
) -> dict[str, Any]:
    """Simulate one trailing policy with adverse-first OHLC ordering."""
    observed_net = finite_or_none(trade.get("net_pnl"))
    entry = finite_or_none(trade.get("entry_price"))
    quantity = finite_or_none(trade.get("quantity"))
    contract_size = finite_or_none(trade.get("contract_size"))
    open_time = _timestamp_or_none(trade.get("open_time_utc"))
    close_time = _timestamp_or_none(trade.get("close_time_utc"))
    side = str(trade.get("side") or "").casefold()
    if (
        observed_net is None
        or entry is None
        or entry <= 0
        or quantity is None
        or quantity <= 0
        or contract_size is None
        or contract_size <= 0
        or open_time is None
        or close_time is None
        or side not in {"long", "short"}
        or path is None
        or path.empty
    ):
        return _unchanged_trade(observed_net)

    seconds = timeframe_seconds(timeframe)
    candles = _fully_observed_candles(
        path,
        open_time=open_time,
        close_time=close_time,
        timeframe_seconds_value=seconds,
    )
    boundary_excluded = int(len(path) - len(candles))
    if candles.empty:
        result = _unchanged_trade(observed_net)
        result["boundary_candles_excluded"] = boundary_excluded
        return result

    multiplier = quantity * contract_size
    fee_cost = max(finite_or_none(trade.get("fees")) or 0.0, 0.0)
    funding_cost = max(finite_or_none(trade.get("funding")) or 0.0, 0.0)
    running_favorable = entry
    ambiguous_count = 0
    gap_count = 0

    for _, candle in candles.iterrows():
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        prior_mfe_gross, prior_mfe_pct = _running_mfe(
            entry=entry,
            favorable_price=running_favorable,
            multiplier=multiplier,
            side=side,
        )
        armed = prior_mfe_pct >= trigger_mfe_pct and prior_mfe_gross > 0.0
        if armed:
            reference_notional = entry * multiplier
            slippage_cost = reference_notional * EXIT_SLIPPAGE_BPS / 10000.0
            floor_gross = max(
                fee_cost + funding_cost + slippage_cost,
                prior_mfe_gross * retention_fraction,
            )
            floor_price = _floor_price(
                entry=entry,
                floor_gross=floor_gross,
                multiplier=multiplier,
                side=side,
            )
            adverse_cross = low <= floor_price if side == "long" else high >= floor_price
            favorable_new_peak = high > running_favorable if side == "long" else low < running_favorable
            if adverse_cross and favorable_new_peak:
                ambiguous_count += 1
            if adverse_cross:
                fill_price, gap = _conservative_fill(
                    open_price=open_price,
                    floor_price=floor_price,
                    side=side,
                )
                gap_count += int(gap)
                gross = _gross_pnl(
                    entry=entry,
                    exit_price=fill_price,
                    multiplier=multiplier,
                    side=side,
                )
                exit_slippage = abs(fill_price) * multiplier * EXIT_SLIPPAGE_BPS / 10000.0
                candidate_net = gross - fee_cost - funding_cost - exit_slippage
                return {
                    "candidate_net_pnl": float(candidate_net),
                    "stop_hit": True,
                    "exit_price": float(fill_price),
                    "path_complete": True,
                    "boundary_candles_excluded": boundary_excluded,
                    "intrabar_ambiguous_count": ambiguous_count,
                    "gap_through_count": gap_count,
                }

        if side == "long":
            running_favorable = max(running_favorable, high)
        else:
            running_favorable = min(running_favorable, low)

    return {
        "candidate_net_pnl": float(observed_net),
        "stop_hit": False,
        "exit_price": None,
        "path_complete": True,
        "boundary_candles_excluded": boundary_excluded,
        "intrabar_ambiguous_count": ambiguous_count,
        "gap_through_count": gap_count,
    }


def _evaluate_development_candidate(
    development: pd.DataFrame,
    *,
    paths_by_trade: Mapping[str, pd.DataFrame],
    timeframe: str,
    candidate: Mapping[str, float | str],
    folds: list[dict[str, int]],
) -> dict[str, Any]:
    trigger = float(candidate["trigger_mfe_pct"])
    retention = float(candidate["retention_fraction_of_mfe"])
    fold_reports: list[dict[str, Any]] = []
    candidate_validation_frames: list[pd.DataFrame] = []
    baseline_validation_frames: list[pd.DataFrame] = []

    for fold in folds:
        start = int(fold["validation_start"])
        end = int(fold["validation_end_exclusive"])
        validation = development.iloc[start:end].copy().reset_index(drop=True)
        simulated, diagnostics = simulate_candidate_frame(
            validation,
            paths_by_trade=paths_by_trade,
            timeframe=timeframe,
            trigger_mfe_pct=trigger,
            retention_fraction=retention,
        )
        baseline_metrics = profit_metrics(validation)
        candidate_metrics = profit_metrics(simulated)
        delta = float(candidate_metrics["net_pnl"]) - float(baseline_metrics["net_pnl"])
        fold_reports.append(
            {
                **fold,
                "baseline_metrics": baseline_metrics,
                "candidate_metrics": candidate_metrics,
                "delta_pnl": delta,
                "positive_delta": delta > 0,
                "path_diagnostics": diagnostics,
            }
        )
        candidate_validation_frames.append(simulated)
        baseline_validation_frames.append(validation)

    candidate_validation = pd.concat(candidate_validation_frames, ignore_index=True)
    baseline_validation = pd.concat(baseline_validation_frames, ignore_index=True)
    candidate_metrics = profit_metrics(candidate_validation)
    baseline_metrics = profit_metrics(baseline_validation)
    total_delta = float(candidate_metrics["net_pnl"]) - float(baseline_metrics["net_pnl"])
    positive_folds = sum(bool(item["positive_delta"]) for item in fold_reports)
    path_complete = sum(
        int(item["path_diagnostics"]["path_complete_trade_count"])
        for item in fold_reports
    )
    total_validation = sum(
        int(item["path_diagnostics"]["trade_count"])
        for item in fold_reports
    )
    coverage = float(path_complete / total_validation) if total_validation else 0.0
    freeze = _development_freeze_gate(
        candidate_metrics,
        total_delta=total_delta,
        positive_fold_count=positive_folds,
        path_coverage_ratio=coverage,
    )
    return {
        "candidate_id": str(candidate["candidate_id"]),
        "trigger_mfe_pct": trigger,
        "retention_fraction_of_mfe": retention,
        "walkforward_folds": fold_reports,
        "positive_walkforward_fold_count": int(positive_folds),
        "walkforward_fold_count": len(fold_reports),
        "walkforward_baseline_net_pnl": baseline_metrics["net_pnl"],
        "walkforward_candidate_net_pnl": candidate_metrics["net_pnl"],
        "walkforward_total_delta_pnl": total_delta,
        "walkforward_expectancy": candidate_metrics["expectancy"],
        "walkforward_profit_factor": candidate_metrics["profit_factor"],
        "walkforward_average_win": candidate_metrics["average_win"],
        "walkforward_average_loss": candidate_metrics["average_loss"],
        "walkforward_maximum_drawdown": candidate_metrics["maximum_drawdown"],
        "walkforward_path_coverage_ratio": coverage,
        "development_decision": "FREEZE_FOR_HOLDOUT" if freeze else "REJECT_PRE_HOLDOUT",
        "holdout_metrics_used_for_selection": False,
    }


def _development_freeze_gate(
    metrics: Mapping[str, Any],
    *,
    total_delta: float,
    positive_fold_count: int,
    path_coverage_ratio: float,
) -> bool:
    profit_factor = finite_or_none(metrics.get("profit_factor"))
    return bool(
        positive_fold_count >= MIN_WALKFORWARD_POSITIVE_FOLDS
        and float(metrics.get("net_pnl", 0.0)) > 0
        and float(metrics.get("expectancy", 0.0)) > 0
        and (profit_factor is None or profit_factor > 1.0)
        and total_delta > 0
        and path_coverage_ratio >= 0.80
    )


def _holdout_gate(
    metrics: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    delta_pnl: float,
    path_coverage_ratio: float,
) -> bool:
    profit_factor = finite_or_none(metrics.get("profit_factor"))
    return bool(
        float(metrics.get("net_pnl", 0.0)) > 0
        and float(metrics.get("expectancy", 0.0)) > 0
        and (profit_factor is None or profit_factor > 1.0)
        and delta_pnl > 0
        and float(metrics.get("maximum_drawdown", math.inf))
        <= float(baseline.get("maximum_drawdown", math.inf))
        and path_coverage_ratio >= 0.80
    )


def _fully_observed_candles(
    path: pd.DataFrame,
    *,
    open_time: pd.Timestamp,
    close_time: pd.Timestamp,
    timeframe_seconds_value: int,
) -> pd.DataFrame:
    timestamps = pd.to_datetime(path["ts"], utc=True, errors="coerce")
    candle_end = timestamps + pd.to_timedelta(timeframe_seconds_value, unit="s")
    mask = timestamps.ge(open_time) & candle_end.le(close_time)
    return path.loc[mask].copy().reset_index(drop=True)


def _running_mfe(
    *,
    entry: float,
    favorable_price: float,
    multiplier: float,
    side: str,
) -> tuple[float, float]:
    favorable_delta = favorable_price - entry if side == "long" else entry - favorable_price
    favorable_delta = max(favorable_delta, 0.0)
    return favorable_delta * multiplier, favorable_delta / entry


def _floor_price(
    *,
    entry: float,
    floor_gross: float,
    multiplier: float,
    side: str,
) -> float:
    delta = floor_gross / multiplier
    return entry + delta if side == "long" else entry - delta


def _conservative_fill(
    *,
    open_price: float,
    floor_price: float,
    side: str,
) -> tuple[float, bool]:
    if side == "long" and open_price < floor_price:
        return open_price, True
    if side == "short" and open_price > floor_price:
        return open_price, True
    return floor_price, False


def _gross_pnl(
    *,
    entry: float,
    exit_price: float,
    multiplier: float,
    side: str,
) -> float:
    return (
        (exit_price - entry) * multiplier
        if side == "long"
        else (entry - exit_price) * multiplier
    )


def _annotate_partitions(
    prepared: pd.DataFrame,
    development: pd.DataFrame,
    holdout: pd.DataFrame,
) -> pd.DataFrame:
    output = prepared.copy()
    output["path_faithful_partition"] = "excluded"
    stable = output["stable_trade_id"].astype("string")
    development_ids = set(development["stable_trade_id"].astype("string"))
    holdout_ids = set(holdout["stable_trade_id"].astype("string"))
    output.loc[stable.isin(development_ids), "path_faithful_partition"] = "development"
    output.loc[stable.isin(holdout_ids), "path_faithful_partition"] = "holdout"
    return output


def _candidate_config(candidate_id: str) -> Mapping[str, float | str]:
    for candidate in FIXED_PROTECTION_CANDIDATES:
        if str(candidate["candidate_id"]) == candidate_id:
            return candidate
    raise ValueError(f"unknown_fixed_candidate:{candidate_id}")


def _timestamp_or_none(value: Any) -> pd.Timestamp | None:
    if value is None or value is pd.NA:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed)


def _unchanged_trade(observed_net: float | None) -> dict[str, Any]:
    return {
        "candidate_net_pnl": float(observed_net or 0.0),
        "stop_hit": False,
        "exit_price": None,
        "path_complete": False,
        "boundary_candles_excluded": 0,
        "intrabar_ambiguous_count": 0,
        "gap_through_count": 0,
    }


def _blocked_report(
    reason: str,
    *,
    preparation: Mapping[str, Any],
    eligible_count: int,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "eligible_trade_count": int(eligible_count),
        "development_candidates": [],
        "frozen_champion": None,
        "holdout_evaluation": None,
        "path_faithful_validation_passed": False,
        "ready_for_paper_wiring": False,
        "preparation": dict(preparation),
    }


def _sort_float(value: Any) -> float:
    parsed = finite_or_none(value)
    return parsed if parsed is not None and math.isfinite(parsed) else -math.inf
