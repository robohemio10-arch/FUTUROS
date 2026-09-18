"""Research-only attribution of observable execution drag in canonical AIBOT PnL.

The official master exposes one execution-related component that can be
separated exactly: ``economic_fee_adjustment``. Spread, slippage, market impact,
queue position and latency are not inferred without causal execution telemetry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.aibot_parity.economic_benchmark import (
    EXPECTED_BASELINE,
    duration_bucket,
    normalize_side,
    normalize_symbol,
)
from smartcrypto.research.trades_master_official.loader import (
    OfficialMasterValidationError,
    load_official_trades_master,
    sha256_file,
)

SCHEMA_VERSION = "execution_intelligence_net_pnl_attribution_v1"
METHOD = "observed_fee_adjustment_attribution_v1"
MIN_SEGMENT_TRADES = 30

REQUIRED_COLUMNS = (
    "trade_sequence",
    "symbol",
    "side",
    "horario_abertura",
    "horario_fechamento",
    "reported_pnl",
    "economic_fee_adjustment",
    "economic_net_pnl",
    "taxa_lucros_perdas_fechados_pct",
    "fee_semantics_regime",
)

UNOBSERVABLE_EXECUTION_COMPONENTS: dict[str, str] = {
    "spread": "causal_arrival_bid_ask_unavailable_in_official_master",
    "slippage": "causal_arrival_mid_and_fill_path_unavailable_in_official_master",
    "market_impact": "order_book_and_fill_path_unavailable_in_official_master",
    "latency": "decision_submit_ack_fill_timestamps_unavailable_in_official_master",
    "maker_taker_role": "liquidity_role_unavailable_in_official_master",
}

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


class ExecutionAttributionError(RuntimeError):
    """Fail-closed error for execution attribution."""


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise ExecutionAttributionError(f"missing_required_column:{column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        raise ExecutionAttributionError(f"invalid_numeric_column:{column}")
    array = values.to_numpy(dtype=float)
    if not np.isfinite(array).all():
        raise ExecutionAttributionError(f"non_finite_numeric_column:{column}")
    return values.astype(float)


def _iso_z(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ExecutionAttributionError(
            "master_missing_columns:" + ",".join(missing)
        )

    reported = _numeric(frame, "reported_pnl")
    adjustment = _numeric(frame, "economic_fee_adjustment")
    net = _numeric(frame, "economic_net_pnl")
    return_pct = _numeric(frame, "taxa_lucros_perdas_fechados_pct")
    sequence = _numeric(frame, "trade_sequence")

    if return_pct.eq(0.0).any():
        raise ExecutionAttributionError("zero_return_pct_prevents_capital_proxy")

    identity_error = (net - (reported + adjustment)).abs()
    if float(identity_error.max()) > 1e-9:
        raise ExecutionAttributionError(
            "economic_identity_mismatch:"
            f"max_abs_error={float(identity_error.max())}"
        )

    open_time = pd.to_datetime(
        frame["horario_abertura"].replace(
            "SOURCE_NOT_AVAILABLE_FROM_PRINT",
            pd.NA,
        ),
        errors="coerce",
        utc=True,
    )
    close_time = pd.to_datetime(
        frame["horario_fechamento"],
        errors="coerce",
        utc=True,
    )
    if close_time.isna().any():
        raise ExecutionAttributionError("invalid_close_time")

    capital_proxy = (reported / (return_pct / 100.0)).abs()
    if not np.isfinite(capital_proxy.to_numpy(dtype=float)).all():
        raise ExecutionAttributionError("capital_proxy_non_finite")
    if capital_proxy.lt(0.0).any():
        raise ExecutionAttributionError("capital_proxy_negative")

    duration_seconds = (close_time - open_time).dt.total_seconds()
    if duration_seconds.dropna().lt(0.0).any():
        raise ExecutionAttributionError("negative_duration")

    fee_impact = -adjustment
    fee_bps = pd.Series(np.nan, index=frame.index, dtype=float)
    eligible_capital = capital_proxy.gt(0.0)
    fee_bps.loc[eligible_capital] = (
        fee_impact.loc[eligible_capital]
        / capital_proxy.loc[eligible_capital]
        * 10_000.0
    )

    output = pd.DataFrame(
        {
            "stable_order": sequence.astype(int),
            "symbol": frame["symbol"].map(normalize_symbol),
            "side": frame["side"].map(normalize_side),
            "fee_semantics_regime": (
                frame["fee_semantics_regime"].astype(str).str.strip()
            ),
            "open_time_utc": open_time,
            "close_time_utc": close_time,
            "duration_seconds": duration_seconds,
            "reported_pnl": reported,
            "economic_fee_adjustment": adjustment,
            "fee_impact_usdt": fee_impact,
            "economic_net_pnl": net,
            "capital_proxy_usdt": capital_proxy,
            "fee_impact_bps_on_capital_proxy": fee_bps,
        }
    )
    output["duration_bucket"] = output["duration_seconds"].map(duration_bucket)
    output["symbol_side"] = output["symbol"] + "|" + output["side"]

    if output["stable_order"].duplicated().any():
        raise ExecutionAttributionError("duplicate_trade_sequence")
    if output["fee_semantics_regime"].eq("").any():
        raise ExecutionAttributionError("empty_fee_semantics_regime")

    return output


def _drawdown(values: pd.Series) -> float | None:
    if values.empty:
        return None
    equity = values.cumsum()
    peaks = equity.cummax()
    return abs(float((equity - peaks).min()))


def _attribution_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "reported_pnl": 0.0,
            "economic_fee_adjustment": 0.0,
            "observable_fee_impact_usdt": 0.0,
            "economic_net_pnl": 0.0,
            "fee_impact_share_of_reported_pnl": None,
            "capital_proxy_total_usdt": 0.0,
            "weighted_fee_impact_bps_on_capital_proxy": None,
            "fee_bps_eligible_trade_count": 0,
            "fee_bps_coverage_rate": None,
            "fee_impact_bps_median": None,
            "fee_impact_bps_p95": None,
            "reported_winner_count": 0,
            "net_winner_count": 0,
            "reported_winner_to_net_nonwinner_count": 0,
            "net_expectancy": None,
            "net_profit_factor": None,
            "net_max_drawdown": None,
            "duration_eligible_trade_count": 0,
            "capital_hours_total": 0.0,
            "net_pnl_per_capital_hour": None,
        }

    ordered = frame.sort_values(
        ["close_time_utc", "stable_order"],
        kind="mergesort",
    )
    reported = ordered["reported_pnl"].astype(float)
    adjustment = ordered["economic_fee_adjustment"].astype(float)
    fee_impact = ordered["fee_impact_usdt"].astype(float)
    net = ordered["economic_net_pnl"].astype(float)
    capital = ordered["capital_proxy_usdt"].astype(float)

    gross_profit = float(net.loc[net > 0.0].sum())
    gross_loss_abs = float(abs(net.loc[net < 0.0].sum()))
    profit_factor = (
        gross_profit / gross_loss_abs if gross_loss_abs > 0.0 else None
    )

    fee_bps = ordered["fee_impact_bps_on_capital_proxy"].replace(
        [np.inf, -np.inf],
        np.nan,
    )
    fee_bps_eligible = fee_bps.dropna()
    capital_total = float(capital.sum())
    weighted_fee_bps = (
        float(fee_impact.sum()) / capital_total * 10_000.0
        if capital_total > 0.0
        else None
    )

    duration = ordered["duration_seconds"]
    duration_eligible = duration.notna() & duration.gt(0.0) & capital.gt(0.0)
    capital_hours = (
        capital.loc[duration_eligible]
        * duration.loc[duration_eligible]
        / 3600.0
    )
    capital_hours_total = float(capital_hours.sum())
    capital_hour_net = float(net.loc[duration_eligible].sum())

    reported_total = float(reported.sum())
    fee_total = float(fee_impact.sum())

    return {
        "trade_count": int(len(ordered)),
        "reported_pnl": reported_total,
        "economic_fee_adjustment": float(adjustment.sum()),
        "observable_fee_impact_usdt": fee_total,
        "economic_net_pnl": float(net.sum()),
        "fee_impact_share_of_reported_pnl": (
            fee_total / reported_total if reported_total != 0.0 else None
        ),
        "capital_proxy_total_usdt": capital_total,
        "weighted_fee_impact_bps_on_capital_proxy": weighted_fee_bps,
        "fee_bps_eligible_trade_count": int(len(fee_bps_eligible)),
        "fee_bps_coverage_rate": float(len(fee_bps_eligible) / len(ordered)),
        "fee_impact_bps_median": (
            float(fee_bps_eligible.median())
            if not fee_bps_eligible.empty
            else None
        ),
        "fee_impact_bps_p95": (
            float(fee_bps_eligible.quantile(0.95))
            if not fee_bps_eligible.empty
            else None
        ),
        "fee_cost_trade_count": int(adjustment.lt(0.0).sum()),
        "fee_credit_trade_count": int(adjustment.gt(0.0).sum()),
        "zero_fee_adjustment_trade_count": int(adjustment.eq(0.0).sum()),
        "reported_winner_count": int(reported.gt(0.0).sum()),
        "net_winner_count": int(net.gt(0.0).sum()),
        "reported_winner_to_net_nonwinner_count": int(
            (reported.gt(0.0) & net.le(0.0)).sum()
        ),
        "reported_nonwinner_to_net_winner_count": int(
            (reported.le(0.0) & net.gt(0.0)).sum()
        ),
        "net_expectancy": float(net.mean()),
        "net_profit_factor": profit_factor,
        "net_max_drawdown": _drawdown(net),
        "duration_eligible_trade_count": int(duration_eligible.sum()),
        "capital_hours_total": capital_hours_total,
        "net_pnl_per_capital_hour": (
            capital_hour_net / capital_hours_total
            if capital_hours_total > 0.0
            else None
        ),
    }


def _segment_rows(
    frame: pd.DataFrame,
    *,
    dimension: str,
) -> list[dict[str, Any]]:
    values = frame[dimension].fillna("UNAVAILABLE").astype(str)
    rows: list[dict[str, Any]] = []
    for bucket in sorted(values.unique().tolist()):
        metrics = _attribution_metrics(frame.loc[values.eq(bucket)])
        rows.append(
            {
                "dimension": dimension,
                "bucket": bucket,
                "rankable": metrics["trade_count"] >= MIN_SEGMENT_TRADES,
                **metrics,
            }
        )
    return rows


def _baseline_gate(common: pd.DataFrame, priority: pd.DataFrame) -> dict[str, Any]:
    metrics = _attribution_metrics(common)
    expected_count = int(EXPECTED_BASELINE["aibot_trade_count"])
    expected_net = float(EXPECTED_BASELINE["aibot_net_pnl"])
    expected_priority_count = int(EXPECTED_BASELINE["top_aibot_trade_count"])
    expected_priority_efficiency = float(
        EXPECTED_BASELINE["top_aibot_net_pnl_per_capital_hour"]
    )

    errors: list[str] = []
    if metrics["trade_count"] != expected_count:
        errors.append(
            f"common_trade_count:{metrics['trade_count']}!={expected_count}"
        )
    if abs(float(metrics["economic_net_pnl"]) - expected_net) > 1e-6:
        errors.append(
            "common_net_pnl:"
            f"{metrics['economic_net_pnl']}!={expected_net}"
        )

    priority_metrics = _attribution_metrics(priority)
    if priority_metrics["trade_count"] != expected_priority_count:
        errors.append(
            "priority_trade_count:"
            f"{priority_metrics['trade_count']}!={expected_priority_count}"
        )
    priority_efficiency = priority_metrics["net_pnl_per_capital_hour"]
    if (
        priority_efficiency is None
        or abs(priority_efficiency - expected_priority_efficiency) > 1e-9
    ):
        errors.append(
            "priority_net_pnl_per_capital_hour:"
            f"{priority_efficiency}!={expected_priority_efficiency}"
        )

    if errors:
        raise ExecutionAttributionError(
            "branch08_frozen_baseline_mismatch:" + "|".join(errors)
        )

    return {
        "status": "PASS",
        "common_trade_count": expected_count,
        "common_net_pnl": expected_net,
        "priority_dimension": EXPECTED_BASELINE["top_dimension"],
        "priority_bucket": EXPECTED_BASELINE["top_bucket"],
        "priority_trade_count": expected_priority_count,
        "priority_net_pnl_per_capital_hour": expected_priority_efficiency,
    }


def evaluate_execution_net_pnl_attribution(
    frame: pd.DataFrame,
    *,
    enforce_branch08_baseline: bool = False,
) -> dict[str, Any]:
    """Evaluate exact fee attribution and explicitly mark unobservable costs."""

    prepared = _prepare_frame(frame)

    common_start = pd.Timestamp(EXPECTED_BASELINE["common_start_utc"])
    common_end = pd.Timestamp(EXPECTED_BASELINE["common_end_utc"])
    common = prepared.loc[
        prepared["close_time_utc"].between(
            common_start,
            common_end,
            inclusive="both",
        )
    ].copy()

    if common.empty:
        raise ExecutionAttributionError("branch08_common_window_empty")

    priority = common.loc[
        common["duration_bucket"].eq(EXPECTED_BASELINE["top_bucket"])
    ].copy()

    baseline_gate = (
        _baseline_gate(common, priority)
        if enforce_branch08_baseline
        else {"status": "NOT_ENFORCED"}
    )

    segment_matrix: list[dict[str, Any]] = []
    for dimension in (
        "duration_bucket",
        "symbol",
        "side",
        "symbol_side",
        "fee_semantics_regime",
    ):
        segment_matrix.extend(
            _segment_rows(common, dimension=dimension)
        )

    ranked_fee_drag = sorted(
        (
            row
            for row in segment_matrix
            if row["rankable"]
            and row["weighted_fee_impact_bps_on_capital_proxy"] is not None
        ),
        key=lambda row: (
            -float(row["weighted_fee_impact_bps_on_capital_proxy"]),
            row["dimension"],
            row["bucket"],
        ),
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "observable_execution_fee_attribution_complete",
        "decision": "OBSERVE_EXECUTION_ATTRIBUTION_ONLY",
        "method": METHOD,
        "attribution_scope": "observable_fee_adjustment_only",
        "execution_simulator_invoked": False,
        "execution_simulator_reason": (
            "causal_market_path_not_available_in_official_master"
        ),
        "unobservable_actual_execution_components": dict(
            UNOBSERVABLE_EXECUTION_COMPONENTS
        ),
        "residual_execution_effects_may_be_embedded_in_reported_pnl": True,
        "capital_proxy_definition": (
            "abs(reported_pnl/(reported_return_pct/100)); "
            "same proxy used by Branch08 economic benchmark"
        ),
        "full_master": _attribution_metrics(prepared),
        "branch08_common_window": {
            "start_utc": _iso_z(common_start),
            "end_utc": _iso_z(common_end),
            **_attribution_metrics(common),
        },
        "priority_segment": {
            "dimension": EXPECTED_BASELINE["top_dimension"],
            "bucket": EXPECTED_BASELINE["top_bucket"],
            "diagnostic_only": True,
            "future_duration_used_for_decision": False,
            **_attribution_metrics(priority),
        },
        "segment_matrix": segment_matrix,
        "ranked_fee_drag_segments": ranked_fee_drag,
        "frozen_branch08_baseline_gate": baseline_gate,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }

    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def build_execution_intelligence_net_pnl_attribution_v1(
    *,
    master_path: str | Path,
) -> dict[str, Any]:
    """Load the canonical master read-only and build Branch 11 evidence."""

    try:
        master = load_official_trades_master(master_path)
        report = evaluate_execution_net_pnl_attribution(
            master.frame,
            enforce_branch08_baseline=True,
        )
        report["source"] = {
            "master_path": str(master.path),
            "master_sha256": sha256_file(master.path),
            "source_audit": master.audit.to_dict(),
        }
        json.dumps(report, sort_keys=True, allow_nan=False, default=str)
        return report
    except (
        ExecutionAttributionError,
        OfficialMasterValidationError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        if isinstance(exc, OfficialMasterValidationError):
            reason = f"official_master_validation:{exc.code}"
        elif isinstance(exc, ExecutionAttributionError):
            reason = str(exc)
        else:
            reason = f"execution_attribution_failed:{type(exc).__name__}"

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "EXECUTION_ATTRIBUTION_BLOCKED",
            "execution_simulator_invoked": False,
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "ExecutionAttributionError",
    "SCHEMA_VERSION",
    "build_execution_intelligence_net_pnl_attribution_v1",
    "evaluate_execution_net_pnl_attribution",
]
