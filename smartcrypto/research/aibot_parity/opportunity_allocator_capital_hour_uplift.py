"""Research-only opportunity allocator evaluated by realized capital-hour uplift.

The allocator ranks only entry-time-known ``symbol|side`` opportunity buckets.
Scores are estimated from purged historical train rows as realized net PnL per
capital-hour.  Test-period outcomes, realized holding duration and Branch 08's
``<15m`` diagnostic are never used to choose an opportunity.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.aibot_parity.economic_benchmark import (
    duration_bucket,
    normalize_side,
    normalize_symbol,
)
from smartcrypto.research.trades_master_official.contracts import (
    LEGACY_OPENING_SENTINEL,
    OFFICIAL_MASTER_SHA256,
)
from smartcrypto.research.trades_master_official.loader import (
    OfficialMasterValidationError,
    load_official_trades_master,
    sha256_file,
)
from smartcrypto.research.trades_master_official.walkforward import (
    WQ2_PERIODS,
    build_fold_plans,
)

SCHEMA_VERSION = "opportunity_allocator_capital_hour_uplift_v1"
METHOD = "purged_expanding_symbol_side_capital_hour_allocator_v1"
RANKING_METRIC = "historical_net_pnl_per_capital_hour"
TOP_N_OPPORTUNITIES = 2
MIN_TRAIN_TRADES_PER_OPPORTUNITY = 30

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_leverage": False,
    "changes_stake": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
    "execution_policy_paper_authorized": False,
}


class OpportunityAllocatorError(RuntimeError):
    """Fail-closed error for Branch 12 research."""


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise OpportunityAllocatorError(f"missing_required_column:{column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        raise OpportunityAllocatorError(f"invalid_numeric_column:{column}")
    array = values.to_numpy(dtype=float)
    if not np.isfinite(array).all():
        raise OpportunityAllocatorError(f"non_finite_numeric_column:{column}")
    return values.astype(float)


def _prepare_population(
    frame: pd.DataFrame,
    *,
    enforce_canonical_population: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "trade_sequence",
        "symbol",
        "side",
        "horario_abertura",
        "horario_fechamento",
        "reported_pnl",
        "taxa_lucros_perdas_fechados_pct",
        "economic_net_pnl",
        "fee_semantics_regime",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise OpportunityAllocatorError(
            "master_missing_columns:" + ",".join(missing)
        )

    raw_open = frame["horario_abertura"].astype("string").str.strip()
    sentinel = raw_open.eq(LEGACY_OPENING_SENTINEL).fillna(False)
    open_time = pd.to_datetime(
        raw_open.mask(sentinel, pd.NA),
        errors="coerce",
        utc=True,
    )
    close_time = pd.to_datetime(
        frame["horario_fechamento"],
        errors="coerce",
        utc=True,
    )
    if close_time.isna().any():
        raise OpportunityAllocatorError("invalid_close_time")
    unexpected_open = (~sentinel) & open_time.isna()
    if unexpected_open.any():
        raise OpportunityAllocatorError(
            f"unexpected_invalid_open_time:{int(unexpected_open.sum())}"
        )

    eligible = open_time.notna()
    excluded = ~eligible
    regimes = frame["fee_semantics_regime"].astype("string").str.strip()

    if enforce_canonical_population:
        if int(eligible.sum()) != 3760 or int(excluded.sum()) != 231:
            raise OpportunityAllocatorError("canonical_temporal_population_mismatch")
        if not regimes.loc[eligible].eq("STANDARD").all():
            raise OpportunityAllocatorError("eligible_population_not_standard")
        if not regimes.loc[excluded].eq("LEGACY").all():
            raise OpportunityAllocatorError("excluded_population_not_legacy")

    eligible_frame = frame.loc[eligible].copy()
    eligible_open = open_time.loc[eligible]
    eligible_close = close_time.loc[eligible]

    sequence = _numeric(eligible_frame, "trade_sequence")
    reported = _numeric(eligible_frame, "reported_pnl")
    return_pct = _numeric(
        eligible_frame,
        "taxa_lucros_perdas_fechados_pct",
    )
    net_pnl = _numeric(eligible_frame, "economic_net_pnl")

    if return_pct.eq(0.0).any():
        raise OpportunityAllocatorError("zero_return_pct_prevents_capital_proxy")

    capital = (reported / (return_pct / 100.0)).abs()
    duration = (
        eligible_close
        - eligible_open
    ).dt.total_seconds().astype(float)
    if duration.lt(0.0).any():
        raise OpportunityAllocatorError("negative_duration")

    working = pd.DataFrame(
        {
            "trade_sequence": sequence.astype(int).to_numpy(),
            "symbol": eligible_frame["symbol"].map(normalize_symbol).to_numpy(),
            "side": eligible_frame["side"].map(normalize_side).to_numpy(),
            "open_time_utc": eligible_open.to_numpy(),
            "close_time_utc": eligible_close.to_numpy(),
            "economic_net_pnl": net_pnl.to_numpy(dtype=float),
            "capital_proxy_usdt": capital.to_numpy(dtype=float),
            "duration_seconds": duration.to_numpy(dtype=float),
        }
    )
    working["capital_hours"] = (
        working["capital_proxy_usdt"]
        * working["duration_seconds"]
        / 3600.0
    )
    working["entry_hour_utc"] = working["open_time_utc"].dt.hour.astype(int)
    working["opportunity_key"] = (
        working["symbol"].astype(str)
        + "|"
        + working["side"].astype(str)
    )
    working["duration_bucket"] = working["duration_seconds"].map(duration_bucket)
    working["period"] = working["open_time_utc"].dt.strftime("%Y-%m")

    working = working.sort_values(
        ["open_time_utc", "close_time_utc", "trade_sequence"],
        kind="mergesort",
    ).reset_index(drop=True)

    observed_periods = tuple(sorted(working["period"].unique().tolist()))
    if enforce_canonical_population and observed_periods != WQ2_PERIODS:
        raise OpportunityAllocatorError(
            "canonical_period_geometry_mismatch:"
            f"{observed_periods}"
        )

    inventory = {
        "eligible_trade_count": int(len(working)),
        "excluded_missing_open_time_count": int(excluded.sum()),
        "opening_time_synthesis_allowed": False,
        "observed_periods": list(observed_periods),
        "selection_dimensions": ["symbol", "side"],
        "future_duration_used_for_selection": False,
        "entry_hour_used_for_selection": False,
    }
    return working, inventory


def _max_drawdown(values: pd.Series) -> float | None:
    if values.empty:
        return None
    cumulative = np.concatenate(
        [
            np.array([0.0], dtype=float),
            values.to_numpy(dtype=float).cumsum(),
        ]
    )
    peaks = np.maximum.accumulate(cumulative)
    return float(abs((cumulative - peaks).min()))


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "win_rate": None,
            "max_drawdown": None,
            "capital_hour_eligible_trade_count": 0,
            "capital_hour_coverage_rate": None,
            "capital_hours_total": 0.0,
            "capital_hour_net_pnl": 0.0,
            "net_pnl_per_capital_hour": None,
        }

    ordered = frame.sort_values(
        ["close_time_utc", "trade_sequence"],
        kind="mergesort",
    )
    pnl = ordered["economic_net_pnl"].astype(float)
    wins = pnl.loc[pnl > 0.0]
    losses = pnl.loc[pnl < 0.0]
    gross_profit = float(wins.sum())
    gross_loss_abs = float(abs(losses.sum()))
    profit_factor = (
        gross_profit / gross_loss_abs
        if gross_loss_abs > 0.0
        else None
    )

    capital_hours = pd.to_numeric(
        ordered["capital_hours"],
        errors="coerce",
    )
    eligible = (
        capital_hours.notna()
        & np.isfinite(capital_hours.to_numpy(dtype=float, na_value=np.nan))
        & capital_hours.gt(0.0)
    )
    eligible_frame = ordered.loc[eligible]
    capital_hours_total = float(
        eligible_frame["capital_hours"].sum()
    )
    capital_hour_net_pnl = float(
        eligible_frame["economic_net_pnl"].sum()
    )

    return {
        "trade_count": int(len(ordered)),
        "net_pnl": float(pnl.sum()),
        "expectancy": float(pnl.mean()),
        "profit_factor": profit_factor,
        "win_rate": float((pnl > 0.0).mean()),
        "max_drawdown": _max_drawdown(pnl),
        "capital_hour_eligible_trade_count": int(len(eligible_frame)),
        "capital_hour_coverage_rate": float(len(eligible_frame) / len(ordered)),
        "capital_hours_total": capital_hours_total,
        "capital_hour_net_pnl": capital_hour_net_pnl,
        "net_pnl_per_capital_hour": (
            capital_hour_net_pnl / capital_hours_total
            if capital_hours_total > 0.0
            else None
        ),
    }


def _historical_opportunity_scores(
    train: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for key, group in train.groupby(
        "opportunity_key",
        sort=True,
        observed=True,
    ):
        eligible = group.loc[group["capital_hours"].gt(0.0)]
        capital_hours = float(eligible["capital_hours"].sum())
        capital_hour_pnl = float(eligible["economic_net_pnl"].sum())
        eligible_count = int(len(eligible))
        score = (
            capital_hour_pnl / capital_hours
            if capital_hours > 0.0
            else None
        )
        rows.append(
            {
                "opportunity_key": str(key),
                "train_trade_count": int(len(group)),
                "capital_hour_eligible_trade_count": eligible_count,
                "capital_hours_total": capital_hours,
                "capital_hour_net_pnl": capital_hour_pnl,
                "historical_net_pnl_per_capital_hour": score,
                "eligible_for_selection": (
                    eligible_count >= MIN_TRAIN_TRADES_PER_OPPORTUNITY
                    and score is not None
                    and score > 0.0
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            not row["eligible_for_selection"],
            -(
                float(row["historical_net_pnl_per_capital_hour"])
                if row["historical_net_pnl_per_capital_hour"] is not None
                else float("-inf")
            ),
            row["opportunity_key"],
        )
    )
    return rows


def _selected_keys(scores: list[dict[str, Any]]) -> tuple[str, ...]:
    eligible = [
        row
        for row in scores
        if row["eligible_for_selection"]
    ]
    return tuple(
        row["opportunity_key"]
        for row in eligible[:TOP_N_OPPORTUNITIES]
    )


def _safe_delta(
    treatment: float | None,
    control: float | None,
) -> float | None:
    if treatment is None or control is None:
        return None
    return float(treatment - control)


def evaluate_opportunity_allocator(
    frame: pd.DataFrame,
    *,
    enforce_canonical_population: bool = False,
) -> dict[str, Any]:
    """Run the frozen past-only allocator over expanding WQ2 folds."""

    population, inventory = _prepare_population(
        frame,
        enforce_canonical_population=enforce_canonical_population,
    )
    plans = build_fold_plans(population, mode="expanding")

    folds: list[dict[str, Any]] = []
    control_parts: list[pd.DataFrame] = []
    treatment_parts: list[pd.DataFrame] = []
    selected_trade_sequences: set[int] = set()

    for plan in plans:
        train = population.loc[list(plan["train_indices"])].copy()
        test = population.loc[list(plan["test_indices"])].copy()

        if train["close_time_utc"].max() >= test["open_time_utc"].min():
            raise OpportunityAllocatorError(
                f"temporal_overlap_after_purge:{plan['test_period']}"
            )

        scores = _historical_opportunity_scores(train)
        selected_keys = _selected_keys(scores)
        treatment = test.loc[
            test["opportunity_key"].isin(selected_keys)
        ].copy()

        overlap = selected_trade_sequences.intersection(
            treatment["trade_sequence"].astype(int).tolist()
        )
        if overlap:
            raise OpportunityAllocatorError(
                "oos_trade_reused_across_folds"
            )
        selected_trade_sequences.update(
            treatment["trade_sequence"].astype(int).tolist()
        )

        control_metrics = _metrics(test)
        treatment_metrics = _metrics(treatment)
        uplift = _safe_delta(
            treatment_metrics["net_pnl_per_capital_hour"],
            control_metrics["net_pnl_per_capital_hour"],
        )

        folds.append(
            {
                "fold": int(plan["fold"]),
                "test_period": plan["test_period"],
                "train_trade_count": int(len(train)),
                "purged_trade_count": int(len(plan["purged_indices"])),
                "test_trade_count": int(len(test)),
                "selected_opportunity_keys": list(selected_keys),
                "selected_opportunity_count": len(selected_keys),
                "score_source": "purged_train_rows_only",
                "ranking_metric": RANKING_METRIC,
                "opportunity_scores": scores,
                "control": control_metrics,
                "treatment": treatment_metrics,
                "net_pnl_per_capital_hour_uplift": uplift,
                "uplift_positive": uplift is not None and uplift > 0.0,
            }
        )
        control_parts.append(test)
        treatment_parts.append(treatment)

    control_oos = pd.concat(control_parts, ignore_index=True)
    treatment_oos = (
        pd.concat(treatment_parts, ignore_index=True)
        if treatment_parts
        else population.iloc[0:0].copy()
    )

    if control_oos["trade_sequence"].duplicated().any():
        raise OpportunityAllocatorError("control_oos_trade_reuse")

    control_metrics = _metrics(control_oos)
    treatment_metrics = _metrics(treatment_oos)
    combined_uplift = _safe_delta(
        treatment_metrics["net_pnl_per_capital_hour"],
        control_metrics["net_pnl_per_capital_hour"],
    )

    priority_control = control_oos.loc[
        control_oos["duration_bucket"].eq("<15m")
    ]
    priority_treatment = treatment_oos.loc[
        treatment_oos["duration_bucket"].eq("<15m")
    ]

    positive_uplift_folds = sum(
        1 for fold in folds if fold["uplift_positive"]
    )
    evaluated_uplift_folds = sum(
        1
        for fold in folds
        if fold["net_pnl_per_capital_hour_uplift"] is not None
    )

    if (
        combined_uplift is not None
        and combined_uplift > 0.0
        and treatment_metrics["net_pnl"] > 0.0
        and evaluated_uplift_folds > 0
    ):
        decision = "CAPITAL_HOUR_UPLIFT_OBSERVED_RESEARCH_ONLY"
    else:
        decision = "CAPITAL_HOUR_UPLIFT_NOT_DEMONSTRATED"

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "opportunity_allocator_walkforward_complete",
        "decision": decision,
        "method": METHOD,
        "ranking_metric": RANKING_METRIC,
        "top_n_opportunities": TOP_N_OPPORTUNITIES,
        "minimum_train_trades_per_opportunity": (
            MIN_TRAIN_TRADES_PER_OPPORTUNITY
        ),
        "selection_dimensions": ["symbol", "side"],
        "selection_uses_current_trade_realized_duration": False,
        "historical_realized_duration_used_in_train_score": True,
        "selection_uses_current_trade_outcome_fields": False,
        "historical_outcomes_used_in_train_score": True,
        "selection_uses_test_period_metrics": False,
        "population": inventory,
        "fold_count": len(folds),
        "folds": folds,
        "combined_oos": {
            "control": control_metrics,
            "treatment": treatment_metrics,
            "net_pnl_per_capital_hour_uplift": combined_uplift,
            "positive_uplift_fold_count": positive_uplift_folds,
            "evaluated_uplift_fold_count": evaluated_uplift_folds,
            "treatment_trade_coverage_rate": (
                treatment_metrics["trade_count"]
                / control_metrics["trade_count"]
                if control_metrics["trade_count"] > 0
                else None
            ),
        },
        "priority_segment_duration_lt_15m": {
            "diagnostic_only": True,
            "used_for_selection": False,
            "used_for_training_key": False,
            "control": _metrics(priority_control),
            "treatment": _metrics(priority_treatment),
        },
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }

    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def build_opportunity_allocator_capital_hour_uplift_v1(
    *,
    master_path: str | Path,
) -> dict[str, Any]:
    """Load the canonical master read-only and build Branch 12 evidence."""

    try:
        master = load_official_trades_master(master_path)
        if master.audit.master_sha256 != OFFICIAL_MASTER_SHA256:
            raise OpportunityAllocatorError("master_sha256_not_frozen")

        report = evaluate_opportunity_allocator(
            master.frame,
            enforce_canonical_population=True,
        )
        report["source"] = {
            "master_path": str(master.path),
            "master_sha256": sha256_file(master.path),
            "source_audit": master.audit.to_dict(),
        }
        json.dumps(report, sort_keys=True, allow_nan=False, default=str)
        return report
    except (
        OpportunityAllocatorError,
        OfficialMasterValidationError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        if isinstance(exc, OfficialMasterValidationError):
            reason = f"official_master_validation:{exc.code}"
        elif isinstance(exc, OpportunityAllocatorError):
            reason = str(exc)
        else:
            reason = f"opportunity_allocator_failed:{type(exc).__name__}"

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "OPPORTUNITY_ALLOCATOR_BLOCKED",
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "MIN_TRAIN_TRADES_PER_OPPORTUNITY",
    "OpportunityAllocatorError",
    "RANKING_METRIC",
    "SCHEMA_VERSION",
    "TOP_N_OPPORTUNITIES",
    "build_opportunity_allocator_capital_hour_uplift_v1",
    "evaluate_opportunity_allocator",
]
