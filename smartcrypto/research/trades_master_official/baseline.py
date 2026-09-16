"""Reproducible WQ1 economic baseline for the official trades master."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import (
    compute_financial_metrics,
    json_safe,
)

from .contracts import (
    CANONICAL_BASELINE_CONTRACT,
    OfficialBaselineContract,
    contract_sha256,
)
from .loader import OfficialMasterData


class OfficialBaselineValidationError(ValueError):
    """Raised when WQ1 does not reproduce its frozen economic baseline."""

    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _fail(
    code: str,
    **details: Any,
) -> None:
    raise OfficialBaselineValidationError(
        code,
        details=details,
    )


def _numeric(
    frame: pd.DataFrame,
    column: str,
) -> pd.Series:
    if column not in frame.columns:
        _fail("missing_required_column", column=column)

    series = pd.to_numeric(
        frame[column],
        errors="coerce",
    )

    if series.isna().any():
        _fail(
            "numeric_column_invalid",
            column=column,
            invalid_count=int(series.isna().sum()),
        )

    values = series.to_numpy(dtype=float)

    if not np.isfinite(values).all():
        _fail(
            "numeric_column_non_finite",
            column=column,
        )

    return series.astype(float)


def _assert_close(
    name: str,
    actual: float,
    expected: float,
    tolerance: float,
) -> None:
    if not math.isclose(
        actual,
        expected,
        rel_tol=0.0,
        abs_tol=tolerance,
    ):
        _fail(
            "baseline_metric_mismatch",
            metric=name,
            expected=expected,
            actual=actual,
            tolerance=tolerance,
        )


def _metrics_for_pnl(
    pnl: pd.Series,
) -> dict[str, Any]:
    metrics = compute_financial_metrics(
        pd.DataFrame({"__pnl": pnl})
    )
    return {
        "trades": metrics["trades"],
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "win_rate": metrics["win_rate"],
        "net_pnl": metrics["total_pnl"],
        "expectancy_mean": metrics["expectancy"],
        "expectancy_median": metrics["median_return"],
        "gross_profit": metrics["gross_profit"],
        "gross_loss": metrics["gross_loss"],
        "profit_factor": metrics["profit_factor"],
        "profit_factor_status": metrics["profit_factor_status"],
        "avg_win": metrics["avg_win"],
        "avg_loss": metrics["avg_loss"],
        "payoff_ratio": metrics["payoff_ratio"],
        "max_drawdown_magnitude": metrics["max_drawdown"],
        "longest_win_streak": metrics["consecutive_wins"],
        "longest_loss_streak": metrics["consecutive_losses"],
    }


def _group_metrics(
    frame: pd.DataFrame,
    group_columns: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    grouped = frame.groupby(
        group_columns,
        dropna=False,
        sort=True,
        observed=True,
    )

    for key, group in grouped:
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = {
            column: (
                "UNKNOWN"
                if pd.isna(value)
                else str(value)
            )
            for column, value in zip(
                group_columns,
                key_tuple,
                strict=True,
            )
        }
        row.update(
            _metrics_for_pnl(
                group["economic_net_pnl"]
            )
        )
        rows.append(row)

    return rows


def _trade_rows(
    frame: pd.DataFrame,
    *,
    ascending: bool,
    count: int,
) -> list[dict[str, Any]]:
    columns = [
        "trade_sequence",
        "order_id_raw",
        "symbol",
        "side",
        "horario_fechamento",
        "economic_net_pnl",
    ]

    selected = (
        frame.sort_values(
            "economic_net_pnl",
            ascending=ascending,
            kind="stable",
        )
        .head(count)
        .loc[:, columns]
    )

    rows: list[dict[str, Any]] = []

    for record in selected.to_dict(orient="records"):
        rows.append(
            {
                "trade_sequence": int(record["trade_sequence"]),
                "order_id_raw": str(record["order_id_raw"]),
                "symbol": str(record["symbol"]),
                "side": str(record["side"]),
                "horario_fechamento": str(record["horario_fechamento"]),
                "economic_net_pnl": float(record["economic_net_pnl"]),
            }
        )

    return rows


def _validate_safety_columns(
    frame: pd.DataFrame,
) -> None:
    canonicalized = _numeric(
        frame,
        "economic_pnl_canonicalized",
    )
    dedup_keep = _numeric(
        frame,
        "final_dedup_keep",
    )
    dedup_excluded = _numeric(
        frame,
        "final_dedup_excluded",
    )
    write_allowed = _numeric(
        frame,
        "trades_master_write_allowed",
    )

    if not canonicalized.eq(1.0).all():
        _fail("economic_pnl_not_fully_canonicalized")

    if not dedup_keep.eq(1.0).all():
        _fail("final_dedup_keep_not_all_true")

    if not dedup_excluded.eq(0.0).all():
        _fail("final_dedup_excluded_not_all_false")

    if not write_allowed.eq(0.0).all():
        _fail("master_write_allowed_flag_not_zero")

    for column in (
        "fee_semantics_gate",
        "final_dedup_gate",
    ):
        values = (
            frame[column]
            .astype("string")
            .str.strip()
        )
        if not values.eq("PASS").all():
            _fail(
                "canonical_gate_not_pass",
                column=column,
            )


def build_official_trades_master_baseline(
    master: OfficialMasterData,
    *,
    contract: OfficialBaselineContract = CANONICAL_BASELINE_CONTRACT,
) -> dict[str, Any]:
    frame = master.frame.copy(deep=True)

    if master.audit.status != "ok":
        _fail(
            "source_audit_not_ok",
            status=master.audit.status,
        )

    if len(frame) != contract.expected_rows:
        _fail(
            "baseline_row_count_mismatch",
            expected=contract.expected_rows,
            actual=len(frame),
        )

    required_columns = (
        "trade_sequence",
        "order_id_raw",
        "symbol",
        "side",
        "horario_abertura",
        "horario_fechamento",
        "fee_semantics_regime",
        "reported_pnl",
        "economic_fee_adjustment",
        "economic_net_pnl",
        "cumulative_economic_net_pnl",
        "running_peak_economic_net_pnl",
        "economic_drawdown_pnl",
        "economic_pnl_canonicalized",
        "final_dedup_keep",
        "final_dedup_excluded",
        "fee_semantics_gate",
        "final_dedup_gate",
        "trades_master_write_allowed",
    )

    missing = [
        column
        for column in required_columns
        if column not in frame.columns
    ]

    if missing:
        _fail(
            "baseline_required_columns_missing",
            columns=missing,
        )

    frame["trade_sequence"] = _numeric(
        frame,
        "trade_sequence",
    )
    frame["reported_pnl"] = _numeric(
        frame,
        "reported_pnl",
    )
    frame["economic_fee_adjustment"] = _numeric(
        frame,
        "economic_fee_adjustment",
    )
    frame["economic_net_pnl"] = _numeric(
        frame,
        "economic_net_pnl",
    )
    frame["cumulative_economic_net_pnl"] = _numeric(
        frame,
        "cumulative_economic_net_pnl",
    )
    frame["running_peak_economic_net_pnl"] = _numeric(
        frame,
        "running_peak_economic_net_pnl",
    )
    frame["economic_drawdown_pnl"] = _numeric(
        frame,
        "economic_drawdown_pnl",
    )

    expected_sequence: np.ndarray = np.arange(
        1,
        len(frame) + 1,
        dtype=np.int64,
    )

    actual_sequence = (
        frame["trade_sequence"]
        .round()
        .astype(np.int64)
        .to_numpy()
    )

    if not np.array_equal(
        actual_sequence,
        expected_sequence,
    ):
        _fail("baseline_trade_sequence_drift")

    _validate_safety_columns(frame)

    formula_error = (
        frame["economic_net_pnl"]
        - (
            frame["reported_pnl"]
            + frame["economic_fee_adjustment"]
        )
    ).abs()

    formula_max_error = float(
        formula_error.max()
    )

    if (
        formula_max_error
        > master.audit.economic_identity_max_abs_error
        + 1e-12
    ):
        _fail(
            "economic_identity_changed_after_source_audit",
            source_audit_error=(
                master.audit.economic_identity_max_abs_error
            ),
            baseline_error=formula_max_error,
        )

    expected_cumulative = (
        frame["economic_net_pnl"].cumsum()
    )

    cumulative_error = (
        frame["cumulative_economic_net_pnl"]
        - expected_cumulative
    ).abs()

    cumulative_max_error = float(
        cumulative_error.max()
    )

    if (
        cumulative_max_error
        > contract.cumulative_abs_tolerance
    ):
        _fail(
            "cumulative_economic_pnl_drift",
            max_abs_error=cumulative_max_error,
        )

    expected_peak = expected_cumulative.cummax()

    peak_error = (
        frame["running_peak_economic_net_pnl"]
        - expected_peak
    ).abs()

    peak_max_error = float(
        peak_error.max()
    )

    if (
        peak_max_error
        > contract.cumulative_abs_tolerance
    ):
        _fail(
            "running_peak_drift",
            max_abs_error=peak_max_error,
        )

    expected_drawdown = (
        expected_cumulative - expected_peak
    )

    drawdown_error = (
        frame["economic_drawdown_pnl"]
        - expected_drawdown
    ).abs()

    drawdown_max_error = float(
        drawdown_error.max()
    )

    if (
        drawdown_max_error
        > contract.cumulative_abs_tolerance
    ):
        _fail(
            "economic_drawdown_series_drift",
            max_abs_error=drawdown_max_error,
        )

    raw_opening = (
        frame["horario_abertura"]
        .astype("string")
        .fillna("")
        .str.strip()
    )

    sentinel_mask = raw_opening.eq(
        contract.legacy_opening_sentinel
    )

    opening_candidate = raw_opening.mask(
        sentinel_mask,
        pd.NA,
    )

    opening_ts = pd.to_datetime(
        opening_candidate,
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    unexpected_invalid_opening = (
        ~sentinel_mask.fillna(False)
        & opening_ts.isna()
    )

    if unexpected_invalid_opening.any():
        _fail(
            "unexpected_invalid_opening_timestamp",
            count=int(
                unexpected_invalid_opening.sum()
            ),
        )

    close_ts = pd.to_datetime(
        frame["horario_fechamento"],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    if close_ts.isna().any():
        _fail(
            "invalid_close_timestamp",
            count=int(close_ts.isna().sum()),
        )

    opening_available = int(
        opening_ts.notna().sum()
    )
    opening_unavailable = int(
        opening_ts.isna().sum()
    )

    regimes = (
        frame["fee_semantics_regime"]
        .astype("string")
        .str.strip()
    )

    standard_mask = regimes.eq("STANDARD")
    legacy_mask = regimes.eq("LEGACY")

    standard_count = int(
        standard_mask.fillna(False).sum()
    )
    legacy_count = int(
        legacy_mask.fillna(False).sum()
    )

    standard_flags = (
        standard_mask
        .fillna(False)
        .to_numpy(dtype=bool)
    )
    legacy_flags = (
        legacy_mask
        .fillna(False)
        .to_numpy(dtype=bool)
    )
    opening_flags = (
        opening_ts
        .notna()
        .to_numpy(dtype=bool)
    )
    sentinel_flags = (
        sentinel_mask
        .fillna(False)
        .to_numpy(dtype=bool)
    )

    standard_mismatch_count = int(
        np.count_nonzero(
            standard_flags != opening_flags
        )
    )
    legacy_mismatch_count = int(
        np.count_nonzero(
            legacy_flags != sentinel_flags
        )
    )

    if (
        standard_mismatch_count != 0
        or legacy_mismatch_count != 0
    ):
        _fail(
            "opening_fee_regime_alignment_drift",
            standard_count=standard_count,
            legacy_count=legacy_count,
            opening_available=opening_available,
            opening_unavailable=opening_unavailable,
            standard_mismatch_count=(
                standard_mismatch_count
            ),
            legacy_mismatch_count=(
                legacy_mismatch_count
            ),
        )

    duration_seconds = (
        close_ts - opening_ts
    ).dt.total_seconds()

    valid_duration = duration_seconds.dropna()

    if (valid_duration < 0).any():
        _fail(
            "negative_trade_duration",
            count=int(
                (valid_duration < 0).sum()
            ),
        )

    frame["__close_ts"] = close_ts
    frame["__open_ts"] = opening_ts
    frame["__duration_seconds"] = (
        duration_seconds
    )

    frame["month"] = (
        close_ts.dt.strftime("%Y-%m")
    )
    frame["day"] = (
        close_ts.dt.strftime("%Y-%m-%d")
    )
    frame["weekday"] = (
        close_ts.dt.day_name()
    )
    frame["close_hour_utc"] = (
        close_ts.dt.hour.astype(int)
    )

    duration_labels = list(
        contract.duration_bucket_labels
    )

    frame["duration_bucket"] = pd.cut(
        frame["__duration_seconds"],
        bins=list(
            contract.duration_bucket_edges_seconds
        ),
        labels=duration_labels,
        right=False,
    )

    metrics = compute_financial_metrics(
        pd.DataFrame(
            {
                "__pnl": (
                    frame["economic_net_pnl"]
                ),
            }
        )
    )

    reported_total = float(
        frame["reported_pnl"].sum()
    )
    fee_total = float(
        frame["economic_fee_adjustment"].sum()
    )
    economic_total = float(
        frame["economic_net_pnl"].sum()
    )

    profit_factor = metrics["profit_factor"]
    win_rate = metrics["win_rate"]

    if (
        profit_factor is None
        or win_rate is None
    ):
        _fail(
            "global_financial_metrics_incomplete"
        )

    signed_max_drawdown = float(
        frame["economic_drawdown_pnl"].min()
    )

    _assert_close(
        "reported_pnl_total",
        reported_total,
        contract.expected_reported_pnl_total,
        contract.financial_abs_tolerance,
    )
    _assert_close(
        "economic_fee_adjustment_total",
        fee_total,
        contract.expected_fee_adjustment_total,
        contract.financial_abs_tolerance,
    )
    _assert_close(
        "economic_net_pnl_total",
        economic_total,
        contract.expected_economic_net_pnl_total,
        contract.financial_abs_tolerance,
    )
    _assert_close(
        "profit_factor",
        float(profit_factor),
        contract.expected_profit_factor,
        contract.profit_factor_abs_tolerance,
    )
    _assert_close(
        "win_rate",
        float(win_rate),
        contract.expected_win_rate,
        contract.win_rate_abs_tolerance,
    )
    _assert_close(
        "max_drawdown_signed",
        signed_max_drawdown,
        contract.expected_max_drawdown_signed,
        contract.financial_abs_tolerance,
    )

    if (
        opening_available
        != contract.expected_opening_available
    ):
        _fail(
            "opening_available_mismatch",
            expected=(
                contract.expected_opening_available
            ),
            actual=opening_available,
        )

    if (
        opening_unavailable
        != contract.expected_opening_unavailable
    ):
        _fail(
            "opening_unavailable_mismatch",
            expected=(
                contract.expected_opening_unavailable
            ),
            actual=opening_unavailable,
        )

    if (
        standard_count
        != contract.expected_standard_count
    ):
        _fail(
            "standard_fee_regime_count_mismatch",
            expected=(
                contract.expected_standard_count
            ),
            actual=standard_count,
        )

    if (
        legacy_count
        != contract.expected_legacy_count
    ):
        _fail(
            "legacy_fee_regime_count_mismatch",
            expected=(
                contract.expected_legacy_count
            ),
            actual=legacy_count,
        )

    module_dd = metrics["max_drawdown"]

    if module_dd is None:
        _fail(
            "module_max_drawdown_missing"
        )

    _assert_close(
        "module_max_drawdown_magnitude",
        float(module_dd),
        abs(signed_max_drawdown),
        contract.financial_abs_tolerance,
    )

    avg_win = metrics["avg_win"]
    avg_loss = metrics["avg_loss"]

    break_even_win_rate = None
    win_rate_margin_vs_break_even = None

    if (
        avg_win is not None
        and avg_loss is not None
        and avg_win > 0
        and avg_loss < 0
    ):
        loss_magnitude = abs(
            float(avg_loss)
        )

        denominator = (
            float(avg_win)
            + loss_magnitude
        )

        if denominator > 0:
            break_even_win_rate = (
                loss_magnitude / denominator
            )
            win_rate_margin_vs_break_even = (
                float(win_rate)
                - break_even_win_rate
            )

    top_10 = frame.nlargest(
        10,
        "economic_net_pnl",
    )
    bottom_10 = frame.nsmallest(
        10,
        "economic_net_pnl",
    )

    top_5_count = max(
        1,
        int(
            math.ceil(
                len(frame) * 0.05
            )
        ),
    )

    top_5 = frame.nlargest(
        top_5_count,
        "economic_net_pnl",
    )

    top_10_pnl = float(
        top_10[
            "economic_net_pnl"
        ].sum()
    )
    bottom_10_pnl = float(
        bottom_10[
            "economic_net_pnl"
        ].sum()
    )
    top_5_pnl = float(
        top_5[
            "economic_net_pnl"
        ].sum()
    )

    duration_metrics = {
        "available": opening_available,
        "unavailable": opening_unavailable,
        "coverage_rate": (
            opening_available / len(frame)
        ),
        "mean_seconds": float(
            valid_duration.mean()
        ),
        "median_seconds": float(
            valid_duration.median()
        ),
        "p90_seconds": float(
            valid_duration.quantile(0.90)
        ),
        "p95_seconds": float(
            valid_duration.quantile(0.95)
        ),
        "max_seconds": float(
            valid_duration.max()
        ),
    }

    global_metrics = {
        **_metrics_for_pnl(
            frame["economic_net_pnl"]
        ),
        "reported_pnl_total": (
            reported_total
        ),
        "economic_fee_adjustment_total": (
            fee_total
        ),
        "economic_net_pnl_total": (
            economic_total
        ),
        "max_drawdown_signed": (
            signed_max_drawdown
        ),
        "break_even_win_rate": (
            break_even_win_rate
        ),
        "win_rate_margin_vs_break_even": (
            win_rate_margin_vs_break_even
        ),
        "top_10_pnl": top_10_pnl,
        "bottom_10_pnl": bottom_10_pnl,
        "top_5_percent_trade_count": (
            top_5_count
        ),
        "top_5_percent_pnl": (
            top_5_pnl
        ),
        "top_10_share_of_net": (
            top_10_pnl / economic_total
            if economic_total != 0
            else None
        ),
        "top_5_percent_share_of_net": (
            top_5_pnl / economic_total
            if economic_total != 0
            else None
        ),
    }

    duration_frame = frame.loc[
        frame["duration_bucket"].notna()
    ].copy()

    payload: dict[str, Any] = {
        "schema_version": (
            "official_trades_master_baseline_v1"
        ),
        "engineering_status": "PASS",
        "master_source_status": "OK",
        "official_baseline_status": "PASS",
        "quant_edge_status": (
            "NOT_EVALUATED_WQ1"
        ),
        "decision": "BASELINE_REPRODUCED",
        "master_path": str(master.path),
        "master_sha256": (
            master.audit.master_sha256
        ),
        "source_contract_sha256": (
            master.audit.source_contract_sha256
        ),
        "baseline_contract_sha256": (
            contract_sha256(contract)
        ),
        "rows": len(frame),
        "columns": master.audit.columns,
        "sheets": list(
            master.audit.sheets
        ),
        "trades_dimension": (
            master.audit.trades_dimension
        ),
        "formula_count": (
            master.audit.formula_count
        ),
        "invalid_order_id_count": (
            master.audit.invalid_order_id_count
        ),
        "economic_identity_max_abs_error": (
            formula_max_error
        ),
        "cumulative_max_abs_error": (
            cumulative_max_error
        ),
        "running_peak_max_abs_error": (
            peak_max_error
        ),
        "drawdown_max_abs_error": (
            drawdown_max_error
        ),
        "opening_time": {
            "available": (
                opening_available
            ),
            "unavailable": (
                opening_unavailable
            ),
            "legacy_sentinel": (
                contract.legacy_opening_sentinel
            ),
        },
        "fee_regime_counts": {
            "STANDARD": standard_count,
            "LEGACY": legacy_count,
        },
        "global_metrics": (
            global_metrics
        ),
        "duration_metrics": (
            duration_metrics
        ),
        "top_trades": _trade_rows(
            frame,
            ascending=False,
            count=10,
        ),
        "bottom_trades": _trade_rows(
            frame,
            ascending=True,
            count=10,
        ),
        "by_symbol": _group_metrics(
            frame,
            ["symbol"],
        ),
        "by_side": _group_metrics(
            frame,
            ["side"],
        ),
        "by_symbol_side": (
            _group_metrics(
                frame,
                ["symbol", "side"],
            )
        ),
        "by_month": _group_metrics(
            frame,
            ["month"],
        ),
        "by_day": _group_metrics(
            frame,
            ["day"],
        ),
        "by_weekday": (
            _group_metrics(
                frame,
                ["weekday"],
            )
        ),
        "by_close_hour_utc": (
            _group_metrics(
                frame,
                ["close_hour_utc"],
            )
        ),
        "by_duration_bucket": (
            _group_metrics(
                duration_frame,
                ["duration_bucket"],
            )
        ),
        "by_fee_regime": (
            _group_metrics(
                frame,
                ["fee_semantics_regime"],
            )
        ),
        "read_only": True,
        "writes_master": False,
        "writes_runtime_trading_state": (
            False
        ),
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "operational_authority": False,
    }

    return json_safe(payload)


__all__ = [
    "OfficialBaselineValidationError",
    "build_official_trades_master_baseline",
]
