"""Authoritative paper closeout with optional score and point-in-time regime evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from smartcrypto.analysis.paper_financial_performance import (
    compute_financial_metrics,
    read_table,
)
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.calibration import (
    calibration_report,
)
from smartcrypto.learning.walkforward.leakage_audit import interval_intersects
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
)


SCHEMA_VERSION = "paper_edge_foundation_v1"
DEFAULT_REPORT = Path("data/reports/paper_edge_foundation_v1.json")
DEFAULT_CERTIFIED_CUT = "2026-07-17T00:00:00Z"
DEFAULT_EMBARGO_SECONDS = 86_400
DEFAULT_MIN_REGIME_SAMPLE = 20

TRADE_REQUIRED_COLUMNS = {
    "id",
    "is_open",
    "pair",
    "is_short",
    "open_date",
    "close_date",
    "close_profit_abs",
    "close_profit",
    "stake_amount",
    "open_rate",
    "max_rate",
    "min_rate",
}
ORDER_REQUIRED_COLUMNS = {
    "id",
    "ft_trade_id",
    "ft_order_side",
    "ft_is_open",
    "status",
    "filled",
    "remaining",
    "order_id",
}
SCORE_COLUMNS = (
    "financial_win_probability",
    "prob_up",
    "qlib_score",
    "signal_confidence",
)
FINANCIAL_PROBABILITY_SCORE_COLUMNS = {"financial_win_probability"}
REGIME_COLUMNS = ("entry_market_regime", "market_regime", "regime", "trend_regime")
LINEAGE_TIMESTAMP_COLUMNS = (
    "lineage_timestamp_utc",
    "decision_timestamp_utc",
    "feature_timestamp_utc",
    "signal_timestamp_utc",
    "entry_timestamp_utc",
)
CALIBRATION_MAX_BRIER = 0.25
CALIBRATION_MAX_ECE = 0.15
CALIBRATION_MIN_AUC = 0.55
EXTERNAL_IDENTITY_COLUMNS = ("trade_id", "order_id")
HISTORICAL_TRADE_ID_PATTERN = re.compile(r"^(?:freqtrade-paper-)?(?P<id>\d+)$", re.IGNORECASE)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only": True,
    "operational_authority": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "writes_active_model": False,
    "writes_active_signals": False,
    "changes_risk": False,
    "changes_strategy": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
}


class SourceIntegrityError(RuntimeError):
    """Controlled failure raised when the authoritative source is not usable."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sqlite_readonly_uri(path: Path) -> str:
    return f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(sqlite_readonly_uri(path), uri=True)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def read_authoritative_paper_source(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.exists() or not source.is_file():
        raise SourceIntegrityError("paper_db_missing", str(source))
    if source.is_symlink():
        raise SourceIntegrityError("paper_db_symlink_not_allowed", str(source))

    hash_before = file_sha256(source)
    connection: sqlite3.Connection | None = None
    try:
        connection = open_sqlite_readonly(source)
        connection.execute("PRAGMA query_only=ON")
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0]) if integrity_row else "missing_integrity_result"
        if integrity.lower() != "ok":
            raise SourceIntegrityError("sqlite_integrity_check_failed", integrity)

        trade_columns = _table_columns(connection, "trades")
        order_columns = _table_columns(connection, "orders")
        missing_trade = sorted(TRADE_REQUIRED_COLUMNS - trade_columns)
        missing_order = sorted(ORDER_REQUIRED_COLUMNS - order_columns)
        if missing_trade or missing_order:
            detail = json.dumps(
                {"missing_trade_columns": missing_trade, "missing_order_columns": missing_order},
                sort_keys=True,
            )
            raise SourceIntegrityError("sqlite_required_columns_missing", detail)

        trades = pd.read_sql_query("SELECT * FROM trades ORDER BY id ASC", connection)
        orders = pd.read_sql_query("SELECT * FROM orders ORDER BY id ASC", connection)
    except sqlite3.Error as exc:
        raise SourceIntegrityError("sqlite_read_failed", str(exc)) from exc
    finally:
        if connection is not None:
            connection.close()

    hash_after = file_sha256(source)
    if hash_before != hash_after:
        raise SourceIntegrityError("paper_db_changed_during_read")
    return {
        "path": source,
        "sha256_before": hash_before,
        "sha256_after": hash_after,
        "sqlite_integrity_check": "ok",
        "trades": trades,
        "orders": orders,
    }


def _finite_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def prepare_closed_trades(trades: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    is_open = _finite_numeric(trades["is_open"])
    if is_open.isna().any() or not is_open.isin([0, 1]).all():
        raise SourceIntegrityError("invalid_is_open_values")
    closed = trades.loc[is_open.eq(0)].copy()
    open_count = int(is_open.eq(1).sum())
    closed["open_date"] = pd.to_datetime(closed["open_date"], utc=True, errors="coerce")
    closed["close_date"] = pd.to_datetime(closed["close_date"], utc=True, errors="coerce")
    null_close_ids = [int(value) for value in closed.loc[closed["close_date"].isna(), "id"].tolist()]
    invalid_open_ids = [int(value) for value in closed.loc[closed["open_date"].isna(), "id"].tolist()]
    pnl = _finite_numeric(closed["close_profit_abs"])
    invalid_pnl_ids = [int(value) for value in closed.loc[pnl.isna(), "id"].tolist()]
    if null_close_ids:
        raise SourceIntegrityError("closed_trade_missing_close_date", json.dumps(null_close_ids))
    if invalid_open_ids:
        raise SourceIntegrityError("closed_trade_missing_open_date", json.dumps(invalid_open_ids))
    if invalid_pnl_ids:
        raise SourceIntegrityError("closed_trade_missing_authoritative_pnl", json.dumps(invalid_pnl_ids))

    closed["close_profit_abs"] = pnl
    is_short = _finite_numeric(closed["is_short"])
    invalid_side_ids = [
        int(value)
        for value in closed.loc[is_short.isna() | ~is_short.isin([0, 1]), "id"].tolist()
    ]
    if invalid_side_ids:
        raise SourceIntegrityError("closed_trade_invalid_is_short", json.dumps(invalid_side_ids))
    closed["side"] = np.where(is_short.eq(1), "SHORT", "LONG")
    closed["duration_minutes"] = (
        (closed["close_date"] - closed["open_date"]).dt.total_seconds() / 60.0
    )
    if (closed["duration_minutes"] < 0).any():
        bad = [int(value) for value in closed.loc[closed["duration_minutes"] < 0, "id"].tolist()]
        raise SourceIntegrityError("closed_trade_negative_duration", json.dumps(bad))
    closed = closed.sort_values(["close_date", "id"], kind="mergesort").reset_index(drop=True)
    return closed, {
        "total_trade_rows": int(len(trades)),
        "closed_trade_count": int(len(closed)),
        "open_trade_count": open_count,
        "max_trade_id": int(_finite_numeric(trades["id"]).max()) if not trades.empty else None,
        "closed_trade_missing_close_date_count": 0,
    }


def _drawdown_metrics(pnl: pd.Series) -> tuple[float, float | None]:
    values = _finite_numeric(pnl).dropna().to_numpy(dtype=float)
    if not len(values):
        return 0.0, None
    equity = np.concatenate(([0.0], np.cumsum(values)))
    running_peak = np.maximum.accumulate(equity)
    max_drawdown = float(np.max(running_peak - equity))
    return max_drawdown, None


def _trade_risk_metrics(closed: pd.DataFrame) -> dict[str, Any]:
    returns = _finite_numeric(closed["close_profit"]).dropna().to_numpy(dtype=float)
    if not len(returns):
        return {
            "risk_metric_basis": "close_profit_trade_return_ratio",
            "tail_risk_sign_convention": "positive_loss_magnitude",
            "trade_return_count": 0,
            "trade_sharpe": None,
            "trade_sortino": None,
            "trade_var_95": None,
            "trade_var_99": None,
            "trade_cvar_95": None,
            "trade_cvar_99": None,
            "calmar": None,
            "calmar_status": "insufficient_equity_return_basis",
        }

    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    downside = np.minimum(returns, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))

    def tail_metrics(confidence: float) -> tuple[float, float]:
        quantile = float(np.quantile(returns, 1.0 - confidence))
        tail = returns[returns <= quantile]
        var = max(0.0, -quantile)
        cvar = max(0.0, -float(np.mean(tail))) if len(tail) else var
        return var, cvar

    var_95, cvar_95 = tail_metrics(0.95)
    var_99, cvar_99 = tail_metrics(0.99)
    return {
        "risk_metric_basis": "close_profit_trade_return_ratio",
        "tail_risk_sign_convention": "positive_loss_magnitude",
        "trade_return_count": int(len(returns)),
        "trade_sharpe": mean / std if std > 0 else None,
        "trade_sortino": mean / downside_deviation if downside_deviation > 0 else None,
        "trade_var_95": var_95,
        "trade_var_99": var_99,
        "trade_cvar_95": cvar_95,
        "trade_cvar_99": cvar_99,
        "calmar": None,
        "calmar_status": "insufficient_equity_return_basis",
    }


def _percentile(series: pd.Series, value: float) -> float | None:
    numeric = _finite_numeric(series).dropna()
    return float(numeric.quantile(value)) if not numeric.empty else None


def _mfe_mae_metrics(closed: pd.DataFrame) -> dict[str, Any]:
    open_rate = _finite_numeric(closed["open_rate"])
    close_rate = _finite_numeric(closed["close_rate"])
    max_rate = _finite_numeric(closed["max_rate"])
    min_rate = _finite_numeric(closed["min_rate"])
    valid = open_rate.gt(0) & max_rate.notna() & min_rate.notna()
    price_give_back_valid = valid & close_rate.gt(0)
    long_mask = closed["side"].eq("LONG")
    mfe = pd.Series(np.nan, index=closed.index, dtype=float)
    mae = pd.Series(np.nan, index=closed.index, dtype=float)
    mfe.loc[valid & long_mask] = max_rate.loc[valid & long_mask] / open_rate.loc[valid & long_mask] - 1.0
    mae.loc[valid & long_mask] = min_rate.loc[valid & long_mask] / open_rate.loc[valid & long_mask] - 1.0
    mfe.loc[valid & ~long_mask] = 1.0 - min_rate.loc[valid & ~long_mask] / open_rate.loc[valid & ~long_mask]
    mae.loc[valid & ~long_mask] = 1.0 - max_rate.loc[valid & ~long_mask] / open_rate.loc[valid & ~long_mask]
    realized_price_move = pd.Series(np.nan, index=closed.index, dtype=float)
    realized_price_move.loc[price_give_back_valid & long_mask] = (
        close_rate.loc[price_give_back_valid & long_mask]
        / open_rate.loc[price_give_back_valid & long_mask]
        - 1.0
    )
    realized_price_move.loc[price_give_back_valid & ~long_mask] = (
        1.0
        - close_rate.loc[price_give_back_valid & ~long_mask]
        / open_rate.loc[price_give_back_valid & ~long_mask]
    )
    price_give_back = mfe - realized_price_move
    invalid_ids = [int(value) for value in closed.loc[~valid, "id"].tolist()]
    price_give_back_invalid_ids = [
        int(value) for value in closed.loc[~price_give_back_valid, "id"].tolist()
    ]
    return {
        "mfe_mae_basis": "open_rate_max_rate_min_rate_ratio_by_side",
        "price_give_back_basis": "mfe_price_ratio_minus_realized_open_to_close_price_ratio",
        "valid_trade_count": int(valid.sum()),
        "invalid_trade_count": int((~valid).sum()),
        "invalid_trade_ids": invalid_ids,
        "price_give_back_valid_trade_count": int(price_give_back_valid.sum()),
        "price_give_back_invalid_trade_count": int((~price_give_back_valid).sum()),
        "price_give_back_invalid_trade_ids": price_give_back_invalid_ids,
        "mfe_mean": float(mfe.dropna().mean()) if mfe.notna().any() else None,
        "mfe_median": float(mfe.dropna().median()) if mfe.notna().any() else None,
        "mae_mean": float(mae.dropna().mean()) if mae.notna().any() else None,
        "mae_median": float(mae.dropna().median()) if mae.notna().any() else None,
        "price_give_back_mean": (
            float(price_give_back.dropna().mean()) if price_give_back.notna().any() else None
        ),
        "price_give_back_median": (
            float(price_give_back.dropna().median()) if price_give_back.notna().any() else None
        ),
    }


def _metrics_for_frame(frame: pd.DataFrame) -> dict[str, Any]:
    prepared = pd.DataFrame({"__pnl": _finite_numeric(frame["close_profit_abs"])})
    base = compute_financial_metrics(prepared)
    return {
        "trades": base["trades"],
        "wins": base["wins"],
        "losses": base["losses"],
        "breakeven_trades": int(_finite_numeric(frame["close_profit_abs"]).eq(0).sum()),
        "win_rate": base["win_rate"],
        "net_pnl": base["total_pnl"],
        "gross_profit": base["gross_profit"],
        "gross_loss": base["gross_loss"],
        "profit_factor": base["profit_factor"],
        "expectancy": base["expectancy"],
        "median_pnl": base["median_return"],
        "avg_win": base["avg_win"],
        "avg_loss": base["avg_loss"],
        "payoff_ratio": base["payoff_ratio"],
    }


def _segment(closed: pd.DataFrame, columns: Sequence[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    grouped = closed.groupby(list(columns), dropna=False, sort=True, observed=True)
    for key, group in grouped:
        values = key if isinstance(key, tuple) else (key,)
        labels = {
            column: "UNKNOWN" if pd.isna(value) else str(value)
            for column, value in zip(columns, values, strict=True)
        }
        output.append({**labels, **_metrics_for_frame(group)})
    return output


def build_segmentations(closed: pd.DataFrame, certified_cut: str) -> dict[str, Any]:
    frame = closed.copy()
    frame["month"] = frame["open_date"].dt.strftime("%Y-%m")
    iso = frame["open_date"].dt.isocalendar()
    frame["iso_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    frame["weekday_utc"] = frame["open_date"].dt.day_name()
    frame["hour_utc"] = frame["open_date"].dt.hour
    frame["duration_bucket"] = pd.cut(
        frame["duration_minutes"],
        bins=[-np.inf, 15, 30, 60, 180, 360, np.inf],
        labels=["<15m", "15-30m", "30-60m", "1-3h", "3-6h", ">6h"],
        right=False,
    )
    p2_start = pd.Timestamp("2026-06-10T00:00:00Z")
    p3_start = pd.Timestamp("2026-07-02T00:00:00Z")
    frame["epoch"] = np.select(
        [frame["open_date"].lt(p2_start), frame["open_date"].lt(p3_start)],
        ["P1", "P2"],
        default="P3",
    )
    certified = pd.Timestamp(certified_cut)
    frame["certified_cut_cohort"] = np.where(
        frame["open_date"].lt(certified), "pre_certified_cut", "post_certified_cut"
    )
    return {
        "temporal_cohort_basis": "open_date_utc",
        "epoch_boundaries": {
            "P1": "open_date < 2026-06-10T00:00:00Z",
            "P2": "2026-06-10T00:00:00Z <= open_date < 2026-07-02T00:00:00Z",
            "P3": "open_date >= 2026-07-02T00:00:00Z",
            "certified_cut": certified.isoformat(),
        },
        "pair": _segment(frame, ["pair"]),
        "side": _segment(frame, ["side"]),
        "pair_side": _segment(frame, ["pair", "side"]),
        "month": _segment(frame, ["month"]),
        "iso_week": _segment(frame, ["iso_week"]),
        "weekday_utc": _segment(frame, ["weekday_utc"]),
        "hour_utc": _segment(frame, ["hour_utc"]),
        "exit_reason": _segment(frame, ["exit_reason"]),
        "duration_bucket": _segment(frame, ["duration_bucket"]),
        "strategy": _segment(frame, ["strategy"]),
        "enter_tag": _segment(frame, ["enter_tag"]),
        "leverage": _segment(frame, ["leverage"]),
        "epoch": _segment(frame, ["epoch"]),
        "certified_cut_cohort": _segment(frame, ["certified_cut_cohort"]),
    }


def build_order_diagnostics(orders: pd.DataFrame, closed: pd.DataFrame) -> dict[str, Any]:
    cohort = orders.loc[orders["ft_trade_id"].isin(set(closed["id"].astype(int)))].copy()
    trade_context = closed[["id", "is_short", "exit_reason"]].rename(columns={"id": "ft_trade_id"})
    cohort = cohort.merge(trade_context, on="ft_trade_id", how="left", validate="many_to_one")
    side = cohort["ft_order_side"].astype(str).str.lower()
    expected_entry_side = np.where(_finite_numeric(cohort["is_short"]).eq(1), "sell", "buy")
    cohort["order_role"] = np.where(side.eq(expected_entry_side), "ENTRY", "EXIT")
    status = cohort["status"].fillna("UNKNOWN").astype(str).str.lower()
    filled = _finite_numeric(cohort["filled"])
    remaining = _finite_numeric(cohort["remaining"])
    status_breakdown = {
        str(key): int(value) for key, value in status.value_counts(dropna=False).sort_index().items()
    }
    tag_column = "ft_order_tag" if "ft_order_tag" in cohort.columns else None
    tag_diagnostics: list[dict[str, Any]] = []
    if tag_column:
        grouped = cohort.loc[cohort["order_role"].eq("EXIT")].groupby(
            ["exit_reason", tag_column], dropna=False, sort=True
        )
        for (exit_reason, order_tag), group in grouped:
            tag_diagnostics.append(
                {
                    "exit_reason": "UNKNOWN" if pd.isna(exit_reason) else str(exit_reason),
                    "order_tag": "UNKNOWN" if pd.isna(order_tag) else str(order_tag),
                    "count": int(len(group)),
                }
            )
    return {
        "orders_total_closed_cohort": int(len(cohort)),
        "entry_order_count": int(cohort["order_role"].eq("ENTRY").sum()),
        "exit_order_count": int(cohort["order_role"].eq("EXIT").sum()),
        "cancelled_order_count": int(status.isin({"canceled", "cancelled", "expired"}).sum()),
        "open_order_count_for_closed_trades": int(_finite_numeric(cohort["ft_is_open"]).eq(1).sum()),
        "partial_fill_count": int((filled.gt(0) & remaining.gt(0)).sum()),
        "zero_fill_count": int(filled.eq(0).sum()),
        "filled_amount_sum": float(filled.dropna().sum()),
        "order_status_breakdown": status_breakdown,
        "exit_reason_order_tag_diagnostics": tag_diagnostics,
        "execution_pnl_inferred_from_orders": False,
    }


def build_financial_closeout(closed: pd.DataFrame, orders: pd.DataFrame, certified_cut: str) -> dict[str, Any]:
    metrics = _metrics_for_frame(closed)
    max_drawdown, max_drawdown_pct = _drawdown_metrics(closed["close_profit_abs"])
    metrics["max_drawdown"] = max_drawdown
    metrics["max_drawdown_pct"] = max_drawdown_pct
    metrics["max_drawdown_pct_status"] = "insufficient_equity_series"
    metrics["recovery_factor"] = (
        float(metrics["net_pnl"]) / max_drawdown if max_drawdown > 0 else None
    )
    pnl = _finite_numeric(closed["close_profit_abs"])
    metrics["max_consecutive_wins"] = _max_consecutive(pnl.gt(0).to_numpy())
    metrics["max_consecutive_losses"] = _max_consecutive(pnl.lt(0).to_numpy())
    metrics["fees_open_total"] = _sum_numeric(closed, "fee_open_cost")
    metrics["fees_close_total"] = _sum_numeric(closed, "fee_close_cost")
    metrics["fees_total"] = metrics["fees_open_total"] + metrics["fees_close_total"]
    funding = (
        _finite_numeric(closed["funding_fees"]).dropna()
        if "funding_fees" in closed.columns
        else pd.Series(dtype=float)
    )
    funding_net_revenue = float(funding.sum())
    metrics["funding_fees_total"] = funding_net_revenue
    metrics["funding_positive_revenue_total"] = float(funding.loc[funding.gt(0)].sum())
    metrics["funding_negative_cost_magnitude_total"] = float(
        -funding.loc[funding.lt(0)].sum()
    )
    metrics["funding_net_revenue_total"] = funding_net_revenue
    metrics["funding_net_cost_total"] = -funding_net_revenue
    metrics["funding_sign_convention"] = "source_positive_revenue_negative_cost"
    metrics["funding_net_semantics"] = (
        "funding_fees_total_is_signed_net_revenue_and_is_already_reflected_in_close_profit_abs"
    )
    metrics["pnl_basis"] = "trades.close_profit_abs_reported_authoritative_realized_pnl"
    metrics["fee_funding_treatment"] = "reported_separately_not_subtracted_again"
    metrics["accounting_reconciliation_status"] = "not_assumed_without_proven_identity"
    duration = _finite_numeric(closed["duration_minutes"])
    metrics.update(
        {
            "average_duration_minutes": float(duration.mean()),
            "median_duration_minutes": float(duration.median()),
            "p25_duration_minutes": _percentile(duration, 0.25),
            "p75_duration_minutes": _percentile(duration, 0.75),
            "p90_duration_minutes": _percentile(duration, 0.90),
        }
    )
    stake = _finite_numeric(closed["stake_amount"])
    capital_hours = float((stake * duration / 60.0).dropna().sum())
    metrics["capital_hours"] = capital_hours
    metrics["pnl_per_capital_hour"] = (
        float(metrics["net_pnl"]) / capital_hours if capital_hours > 0 else None
    )
    metrics["risk_metrics"] = _trade_risk_metrics(closed)
    metrics["mfe_mae"] = _mfe_mae_metrics(closed)
    metrics["orders"] = build_order_diagnostics(orders, closed)
    metrics["segmentations"] = build_segmentations(closed, certified_cut)
    return metrics


def _max_consecutive(mask: np.ndarray) -> int:
    current = 0
    maximum = 0
    for value in mask:
        current = current + 1 if bool(value) else 0
        maximum = max(maximum, current)
    return int(maximum)


def _sum_numeric(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    return float(_finite_numeric(frame[column]).dropna().sum())


def _parse_trade_id(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    match = HISTORICAL_TRADE_ID_PATTERN.fullmatch(str(value).strip())
    return int(match.group("id")) if match else None


def _match_external_rows(
    source: pd.DataFrame,
    closed: pd.DataFrame,
    orders: pd.DataFrame,
) -> pd.DataFrame:
    frame = source.copy().reset_index(drop=True)
    frame["__source_row"] = frame.index
    frame["__trade_id"] = pd.Series([None] * len(frame), dtype="object")
    frame["__identity_method"] = None
    frame["__rejection_reason"] = None
    frame["__lineage_timestamp_column"] = None
    frame["__lineage_timestamp_utc"] = pd.Series(
        pd.NaT,
        index=frame.index,
        dtype="datetime64[ns, UTC]",
    )

    trade_ids = (
        frame["trade_id"].map(_parse_trade_id)
        if "trade_id" in frame.columns
        else pd.Series([None] * len(frame), index=frame.index, dtype="object")
    )
    malformed_trade_id = pd.Series(False, index=frame.index)
    if "trade_id" in frame.columns:
        supplied_trade_id = frame["trade_id"].notna() & frame["trade_id"].astype(str).str.strip().ne("")
        malformed_trade_id = supplied_trade_id & trade_ids.isna()

    order_ids = pd.Series([None] * len(frame), index=frame.index, dtype="object")
    unknown_order_id = pd.Series(False, index=frame.index)
    if "order_id" in frame.columns:
        mapping_frame = orders[["order_id", "ft_trade_id"]].dropna().copy()
        mapping_frame["__order_key"] = mapping_frame["order_id"].astype(str).str.strip()
        unique_counts = mapping_frame.groupby("__order_key")["ft_trade_id"].nunique()
        allowed_order_ids = set(unique_counts.loc[unique_counts.eq(1)].index)
        order_mapping = {
            str(order_key): int(trade_id)
            for order_key, trade_id in zip(
                mapping_frame["__order_key"],
                mapping_frame["ft_trade_id"],
                strict=True,
            )
            if str(order_key) in allowed_order_ids
        }
        supplied_order_id = frame["order_id"].notna() & frame["order_id"].astype(str).str.strip().ne("")
        order_ids = frame["order_id"].map(
            lambda value: order_mapping.get(str(value).strip()) if pd.notna(value) else None
        )
        unknown_order_id = supplied_order_id & order_ids.isna()

    conflict = trade_ids.notna() & order_ids.notna() & trade_ids.ne(order_ids)
    resolved = trade_ids.where(trade_ids.notna(), order_ids)
    frame.loc[trade_ids.notna() & ~conflict, "__identity_method"] = "trade_id"
    frame.loc[trade_ids.isna() & order_ids.notna() & ~conflict, "__identity_method"] = "order_id"
    frame.loc[trade_ids.notna() & order_ids.notna() & ~conflict, "__identity_method"] = (
        "trade_id_and_order_id"
    )
    frame.loc[malformed_trade_id, "__rejection_reason"] = "malformed_trade_id"
    frame.loc[frame["__rejection_reason"].isna() & unknown_order_id, "__rejection_reason"] = (
        "unknown_or_ambiguous_order_id"
    )
    frame.loc[frame["__rejection_reason"].isna() & conflict, "__rejection_reason"] = (
        "conflicting_external_identity"
    )
    frame.loc[frame["__rejection_reason"].isna() & resolved.isna(), "__rejection_reason"] = (
        "explicit_external_identity_missing"
    )

    closed_context = closed.set_index(closed["id"].astype(int))["open_date"]
    closed_ids = set(closed_context.index)
    unknown_trade = resolved.notna() & ~resolved.isin(closed_ids)
    frame.loc[frame["__rejection_reason"].isna() & unknown_trade, "__rejection_reason"] = (
        "trade_id_not_in_closed_cohort"
    )

    lineage_column = next(
        (column for column in LINEAGE_TIMESTAMP_COLUMNS if column in frame.columns),
        None,
    )
    if lineage_column is None:
        frame.loc[frame["__rejection_reason"].isna(), "__rejection_reason"] = (
            "lineage_timestamp_column_missing"
        )
    else:
        lineage = pd.to_datetime(frame[lineage_column], utc=True, errors="coerce")
        frame["__lineage_timestamp_column"] = lineage_column
        frame["__lineage_timestamp_utc"] = lineage
        invalid_lineage = lineage.isna()
        frame.loc[frame["__rejection_reason"].isna() & invalid_lineage, "__rejection_reason"] = (
            "lineage_timestamp_invalid_or_missing"
        )
        open_dates = resolved.map(closed_context)
        future_lineage = lineage.notna() & open_dates.notna() & lineage.gt(open_dates)
        frame.loc[frame["__rejection_reason"].isna() & future_lineage, "__rejection_reason"] = (
            "future_lineage_timestamp"
        )

    valid = frame["__rejection_reason"].isna()
    duplicate_ids = set(resolved.loc[valid].value_counts().loc[lambda values: values.gt(1)].index)
    duplicated = valid & resolved.isin(duplicate_ids)
    frame.loc[duplicated, "__rejection_reason"] = "duplicate_external_trade_identity"
    frame.loc[frame["__rejection_reason"].isna(), "__trade_id"] = resolved
    return frame


def _external_match_audit(frame: pd.DataFrame) -> dict[str, Any]:
    reasons = frame["__rejection_reason"].fillna("accepted_point_in_time")
    rejected_count = int(frame["__trade_id"].isna().sum())
    lineage_violation_count = int(
        reasons.isin(
            {
                "future_lineage_timestamp",
                "lineage_timestamp_column_missing",
                "lineage_timestamp_invalid_or_missing",
            }
        ).sum()
    )
    reason_counts = {
        str(reason): int(count)
        for reason, count in reasons.value_counts(dropna=False).sort_index().items()
    }
    identity_methods = frame.loc[frame["__trade_id"].notna(), "__identity_method"]
    timestamp_columns = sorted(
        {
            str(value)
            for value in frame["__lineage_timestamp_column"].dropna().tolist()
            if str(value)
        }
    )
    return {
        "external_identity_contract": "explicit_trade_id_or_unambiguous_order_id_only",
        "external_identity_columns": list(EXTERNAL_IDENTITY_COLUMNS),
        "external_identity_contract_status": "ok" if rejected_count == 0 else "blocked",
        "external_rejected_row_count": rejected_count,
        "generic_id_accepted": False,
        "point_in_time_lineage_required": True,
        "point_in_time_lineage_status": (
            "ok" if lineage_violation_count == 0 else "blocked"
        ),
        "lineage_timestamp_columns_used": timestamp_columns,
        "point_in_time_valid_row_count": int(frame["__trade_id"].notna().sum()),
        "future_lineage_rejected_row_count": int(
            reasons.eq("future_lineage_timestamp").sum()
        ),
        "lineage_missing_or_invalid_row_count": int(
            reasons.isin(
                {"lineage_timestamp_column_missing", "lineage_timestamp_invalid_or_missing"}
            ).sum()
        ),
        "external_match_rejection_reason_counts": reason_counts,
        "external_identity_method_counts": {
            str(method): int(count)
            for method, count in identity_methods.value_counts().sort_index().items()
        },
    }


def _empty_score_calibration(status: str, source_path: Path | None, warning: str | None = None) -> dict[str, Any]:
    return {
        "score_source_status": "missing" if source_path is None else "unusable",
        "score_source_path": str(source_path) if source_path else None,
        "score_rows": 0,
        "matched_closed_trades": 0,
        "unmatched_score_rows": 0,
        "closed_trades_without_score": 0,
        "score_coverage_rate": 0.0,
        "financial_probability_matched_closed_trades": 0,
        "financial_probability_coverage_rate": 0.0,
        "calibration_gate": None,
        "external_identity_contract": "explicit_trade_id_or_unambiguous_order_id_only",
        "external_identity_columns": list(EXTERNAL_IDENTITY_COLUMNS),
        "external_identity_contract_status": "not_evaluated",
        "external_rejected_row_count": 0,
        "generic_id_accepted": False,
        "point_in_time_lineage_required": True,
        "point_in_time_lineage_status": "not_evaluated",
        "lineage_timestamp_columns_used": [],
        "point_in_time_valid_row_count": 0,
        "future_lineage_rejected_row_count": 0,
        "lineage_missing_or_invalid_row_count": 0,
        "external_match_rejection_reason_counts": {},
        "external_identity_method_counts": {},
        "score_status": status,
        "score_metrics": {},
        "warnings": [warning] if warning else [],
    }


def _score_deciles(frame: pd.DataFrame, score_column: str) -> list[dict[str, Any]]:
    working = frame[[score_column, "close_profit_abs"]].copy()
    working[score_column] = _finite_numeric(working[score_column])
    working = working.dropna()
    if working.empty:
        return []
    unique_scores = np.sort(working[score_column].unique())
    bucket_count = min(10, len(unique_scores))
    if bucket_count == 1:
        score_to_bucket = {float(unique_scores[0]): 1}
    else:
        score_to_bucket = {
            float(score): min(
                bucket_count,
                int(index * bucket_count / len(unique_scores)) + 1,
            )
            for index, score in enumerate(unique_scores)
        }
    working["decile"] = working[score_column].map(score_to_bucket)
    output: list[dict[str, Any]] = []
    for decile, group in working.groupby("decile", sort=True):
        metrics = _metrics_for_frame(group)
        output.append(
            {
                "decile": int(decile),
                "count": int(len(group)),
                "win_rate": metrics["win_rate"],
                "net_pnl": metrics["net_pnl"],
                "expectancy": metrics["expectancy"],
                "profit_factor": metrics["profit_factor"],
                "mean_score": float(group[score_column].mean()),
            }
        )
    return output


def build_score_calibration(
    source_path: str | Path | None,
    closed: pd.DataFrame,
    orders: pd.DataFrame,
) -> dict[str, Any]:
    if source_path is None:
        report = _empty_score_calibration("SOURCE_MISSING", None)
        report["closed_trades_without_score"] = int(len(closed))
        return report
    path = Path(source_path).resolve()
    if not path.exists() or not path.is_file() or path.is_symlink():
        report = _empty_score_calibration("INSUFFICIENT_COVERAGE", path, "score_source_missing_or_unsafe")
        report["closed_trades_without_score"] = int(len(closed))
        return report
    try:
        source = read_table(path)
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        report = _empty_score_calibration("INSUFFICIENT_COVERAGE", path, f"score_source_read_failed:{type(exc).__name__}")
        report["closed_trades_without_score"] = int(len(closed))
        return report
    matched = _match_external_rows(source, closed, orders)
    match_audit = _external_match_audit(matched)
    matched_rows = matched.loc[matched["__trade_id"].notna()].copy()
    closed_context = closed[["id", "close_profit_abs"]].rename(columns={"id": "__trade_id"})
    matched_rows["__trade_id"] = matched_rows["__trade_id"].astype(int)
    matched_rows = matched_rows.merge(closed_context, on="__trade_id", how="inner", validate="one_to_one")
    score_columns = [column for column in SCORE_COLUMNS if column in matched_rows.columns]
    metrics_by_score: dict[str, Any] = {}
    rows_with_any_score: set[int] = set()
    financial_probability_rows: set[int] = set()
    financial_probability_gate: dict[str, Any] | None = None
    warnings: list[str] = []
    for column in score_columns:
        values = _finite_numeric(matched_rows[column])
        valid = values.notna()
        evaluation = matched_rows.loc[valid].copy()
        evaluation[column] = values.loc[valid]
        rows_with_any_score.update(evaluation["__trade_id"].astype(int))
        labels = evaluation["close_profit_abs"].gt(0).astype(int)
        auc = None
        if len(evaluation) >= 2 and labels.nunique() == 2:
            auc = float(roc_auc_score(labels, evaluation[column]))
        probability_semantics = column in FINANCIAL_PROBABILITY_SCORE_COLUMNS
        if probability_semantics:
            financial_probability_rows.update(evaluation["__trade_id"].astype(int))
        probability_valid = bool(
            probability_semantics
            and not evaluation.empty
            and evaluation[column].between(0.0, 1.0, inclusive="both").all()
        )
        if probability_valid:
            probability = calibration_report(
                evaluation[column].tolist(),
                labels.tolist(),
                evaluation["close_profit_abs"].tolist(),
                bin_count=10,
                min_bucket_rows=5,
                score_semantics="explicit_financial_probability_of_profitable_closed_trade",
            )
            probability_status = "financial_probability_evaluated"
        else:
            probability = None
            probability_status = (
                "invalid_financial_probability_range"
                if probability_semantics
                else "not_financial_win_probability_semantics"
            )
        deciles = _score_deciles(evaluation, column)
        expectancy = [row["expectancy"] for row in deciles if row["expectancy"] is not None]
        monotonic = bool(
            len(expectancy) >= 2
            and all(float(right) >= float(left) for left, right in zip(expectancy, expectancy[1:], strict=False))
        )
        unique_score_count = int(evaluation[column].nunique(dropna=True))
        calibration_gate = None
        if probability_valid and probability is not None:
            brier = probability["brier_score"]
            ece = probability["expected_calibration_error"]
            calibration_checks = {
                "brier_score_within_limit": bool(
                    brier is not None and float(brier) <= CALIBRATION_MAX_BRIER
                ),
                "expected_calibration_error_within_limit": bool(
                    ece is not None and float(ece) <= CALIBRATION_MAX_ECE
                ),
                "roc_auc_meets_minimum": bool(
                    auc is not None and float(auc) >= CALIBRATION_MIN_AUC
                ),
                "expectancy_monotonic_non_decreasing": monotonic,
                "multiple_distinct_score_buckets": len(deciles) >= 2,
            }
            calibration_gate = {
                "status": "passed" if all(calibration_checks.values()) else "failed",
                "passed": bool(all(calibration_checks.values())),
                "checks": calibration_checks,
                "thresholds": {
                    "max_brier_score": CALIBRATION_MAX_BRIER,
                    "max_expected_calibration_error": CALIBRATION_MAX_ECE,
                    "min_roc_auc": CALIBRATION_MIN_AUC,
                    "requires_expectancy_monotonic_non_decreasing": True,
                    "requires_multiple_distinct_score_buckets": True,
                },
            }
            financial_probability_gate = calibration_gate
        metrics_by_score[column] = {
            "valid_rows": int(len(evaluation)),
            "unique_score_count": unique_score_count,
            "decile_count": int(len(deciles)),
            "roc_auc": auc,
            "score_semantics": (
                "explicit_financial_probability_of_profitable_closed_trade"
                if probability_semantics
                else "ordinal_or_model_score_not_financial_win_probability"
            ),
            "is_financial_win_probability": probability_semantics,
            "probability_metrics_status": probability_status,
            "probability_metrics": probability,
            "calibration_gate": calibration_gate,
            "deciles": deciles,
            "expectancy_monotonic_non_decreasing": monotonic,
        }
    matched_closed = len(rows_with_any_score)
    coverage = matched_closed / len(closed) if len(closed) else 0.0
    financial_probability_matched = len(financial_probability_rows)
    financial_probability_coverage = (
        financial_probability_matched / len(closed) if len(closed) else 0.0
    )
    lineage_contract_violations = (
        int(match_audit["future_lineage_rejected_row_count"])
        + int(match_audit["lineage_missing_or_invalid_row_count"])
    )
    if lineage_contract_violations > 0:
        status = "UNCALIBRATED"
        warnings.append("point_in_time_lineage_contract_violated")
    elif not metrics_by_score:
        status = "UNCALIBRATED"
        warnings.append("no_registered_score_columns")
    elif "financial_win_probability" not in metrics_by_score:
        status = "UNCALIBRATED"
        warnings.append("explicit_financial_win_probability_missing")
    elif financial_probability_coverage < 0.5:
        status = "INSUFFICIENT_COVERAGE"
    elif financial_probability_gate is None or not financial_probability_gate["passed"]:
        status = "UNCALIBRATED"
        warnings.append("financial_probability_calibration_gate_failed")
    elif financial_probability_coverage < 0.95:
        status = "PARTIALLY_CALIBRATED"
    else:
        status = "CALIBRATED"
    return {
        "score_source_status": "loaded",
        "score_source_path": str(path),
        "score_rows": int(len(source)),
        "matched_closed_trades": int(matched_closed),
        "unmatched_score_rows": int(matched["__trade_id"].isna().sum()),
        "closed_trades_without_score": int(len(closed) - matched_closed),
        "score_coverage_rate": float(coverage),
        "financial_probability_matched_closed_trades": int(financial_probability_matched),
        "financial_probability_coverage_rate": float(financial_probability_coverage),
        "calibration_gate": financial_probability_gate,
        "score_status": status,
        "score_metrics": metrics_by_score,
        "warnings": warnings,
        **match_audit,
    }


def _alignment_state(raw: Any, side: str) -> str:
    normalized = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in {"ALIGNED", "COUNTER_TREND", "RANGE"}:
        return normalized
    if normalized in {"RANGE_BOUND", "RANGING", "SIDEWAYS"}:
        return "RANGE"
    if normalized in {"TREND_UP", "UPTREND", "BULL", "BULLISH"}:
        return "ALIGNED" if side == "LONG" else "COUNTER_TREND"
    if normalized in {"TREND_DOWN", "DOWNTREND", "BEAR", "BEARISH"}:
        return "ALIGNED" if side == "SHORT" else "COUNTER_TREND"
    return "UNKNOWN"


def _fold_metric(frame: pd.DataFrame, minimum: int) -> dict[str, Any]:
    if len(frame) < minimum:
        return {
            "status": "insufficient_sample",
            "sample_count": int(len(frame)),
            "net_pnl": None,
            "expectancy": None,
            "profit_factor": None,
            "win_rate": None,
            "max_drawdown": None,
        }
    metrics = _metrics_for_frame(frame)
    drawdown, _ = _drawdown_metrics(frame["close_profit_abs"])
    return {
        "status": "ok",
        "sample_count": int(len(frame)),
        "net_pnl": metrics["net_pnl"],
        "expectancy": metrics["expectancy"],
        "profit_factor": metrics["profit_factor"],
        "win_rate": metrics["win_rate"],
        "max_drawdown": drawdown,
    }


def _split_overlap_count(frame: pd.DataFrame, split: Mapping[str, Any]) -> int:
    count = 0
    for train_index in split["_train_indices"]:
        train_start = frame.loc[train_index, "open_time_utc"]
        train_end = frame.loc[train_index, "close_time_utc"]
        for test_index in [*split["_validation_indices"], *split["_test_indices"]]:
            if interval_intersects(
                train_start,
                train_end,
                frame.loc[test_index, "open_time_utc"],
                frame.loc[test_index, "close_time_utc"],
            ):
                count += 1
    return count


def _empty_regime(status: str, path: Path | None, warning: str | None = None) -> dict[str, Any]:
    return {
        "regime_source_status": "missing" if path is None else "unusable",
        "regime_source_path": str(path) if path else None,
        "regime_rows": 0,
        "matched_closed_trades": 0,
        "closed_trades_without_regime": 0,
        "regime_coverage_rate": 0.0,
        "regime_enum_status": "not_evaluated",
        "invalid_regime_row_count": 0,
        "invalid_regime_values": [],
        "external_identity_contract": "explicit_trade_id_or_unambiguous_order_id_only",
        "external_identity_columns": list(EXTERNAL_IDENTITY_COLUMNS),
        "external_identity_contract_status": "not_evaluated",
        "external_rejected_row_count": 0,
        "generic_id_accepted": False,
        "point_in_time_lineage_required": True,
        "point_in_time_lineage_status": "not_evaluated",
        "lineage_timestamp_columns_used": [],
        "point_in_time_valid_row_count": 0,
        "future_lineage_rejected_row_count": 0,
        "lineage_missing_or_invalid_row_count": 0,
        "external_match_rejection_reason_counts": {},
        "external_identity_method_counts": {},
        "regime_status": status,
        "walkforward_status": "not_run",
        "fold_count": 0,
        "folds": [],
        "aggregate_oos": {},
        "temporal_overlap_count": 0,
        "leakage_detected": False,
        "warnings": [warning] if warning else [],
    }


def build_regime_oos(
    source_path: str | Path | None,
    closed: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    embargo_seconds: int,
    minimum_sample: int,
) -> dict[str, Any]:
    if source_path is None:
        report = _empty_regime("SOURCE_MISSING", None)
        report["closed_trades_without_regime"] = int(len(closed))
        return report
    path = Path(source_path).resolve()
    if not path.exists() or not path.is_file() or path.is_symlink():
        report = _empty_regime("INSUFFICIENT_COVERAGE", path, "regime_source_missing_or_unsafe")
        report["closed_trades_without_regime"] = int(len(closed))
        return report
    try:
        source = read_table(path)
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        report = _empty_regime("INSUFFICIENT_COVERAGE", path, f"regime_source_read_failed:{type(exc).__name__}")
        report["closed_trades_without_regime"] = int(len(closed))
        return report
    matched = _match_external_rows(source, closed, orders)
    match_audit = _external_match_audit(matched)
    regime_column = next((column for column in REGIME_COLUMNS if column in matched.columns), None)
    if regime_column is None:
        report = _empty_regime("INSUFFICIENT_COVERAGE", path, "registered_regime_column_missing")
        report["regime_rows"] = int(len(source))
        report["closed_trades_without_regime"] = int(len(closed))
        return report
    context = closed[
        ["id", "pair", "side", "open_date", "close_date", "close_profit_abs"]
    ].rename(columns={"id": "__trade_id"})
    matched_rows = matched.loc[matched["__trade_id"].notna()].copy()
    matched_rows["__trade_id"] = matched_rows["__trade_id"].astype(int)
    matched_rows = matched_rows.merge(context, on="__trade_id", how="inner", validate="one_to_one")
    matched_rows["alignment_state"] = [
        _alignment_state(regime, side)
        for regime, side in zip(matched_rows[regime_column], matched_rows["side"], strict=True)
    ]
    invalid_regime = matched_rows["alignment_state"].eq("UNKNOWN")
    invalid_regime_values = sorted(
        {
            "<missing>" if pd.isna(value) else str(value)
            for value in matched_rows.loc[invalid_regime, regime_column].tolist()
        }
    )
    known = matched_rows.loc[matched_rows["alignment_state"].ne("UNKNOWN")].copy()
    coverage = len(known) / len(closed) if len(closed) else 0.0
    known = known.sort_values(["open_date", "close_date", "__trade_id"], kind="mergesort").reset_index(drop=True)
    known["open_time_utc"] = known["open_date"]
    known["close_time_utc"] = known["close_date"]
    splits = build_walkforward_splits(known, embargo_seconds=embargo_seconds)
    folds: list[dict[str, Any]] = []
    test_indices: list[int] = []
    overlap_count = 0
    for split in splits:
        overlap_count += _split_overlap_count(known, split)
        test = known.loc[split["_test_indices"]].copy()
        test_indices.extend(split["_test_indices"])
        fold = {
            "fold_id": split["split_id"],
            "train_start": split["train_start_utc"],
            "train_end": split["train_end_utc"],
            "test_start": split["test_start_utc"],
            "test_end": split["test_end_utc"],
            "train_count": split["train_row_count_after_purge"],
            "test_count": split["test_row_count"],
            "purged_row_count": split["purged_row_count"],
            "embargoed_row_count": split["embargoed_row_count"],
            "metrics": {"ALL": _fold_metric(test, 1)},
        }
        for state in ("ALIGNED", "COUNTER_TREND", "RANGE"):
            fold["metrics"][state] = _fold_metric(
                test.loc[test["alignment_state"].eq(state)], minimum_sample
            )
        folds.append(fold)
    oos = known.loc[sorted(set(test_indices))].copy() if test_indices else known.iloc[0:0].copy()
    aggregate = {"ALL": _fold_metric(oos, 1)}
    for state in ("ALIGNED", "COUNTER_TREND", "RANGE"):
        aggregate[state] = _fold_metric(
            oos.loc[oos["alignment_state"].eq(state)], minimum_sample
        )
    aligned = aggregate["ALIGNED"]
    positive_aligned_folds = sum(
        1
        for fold in folds
        if fold["metrics"]["ALIGNED"]["status"] == "ok"
        and float(fold["metrics"]["ALIGNED"]["expectancy"]) > 0
    )
    aligned_oos = oos.loc[oos["alignment_state"].eq("ALIGNED")]
    diversified = (
        aligned_oos["pair"].nunique() > 1 and aligned_oos["side"].nunique() > 1
        if len(aligned_oos) >= minimum_sample * 2
        else True
    )
    robust = bool(
        coverage >= 0.8
        and len(folds) > 1
        and aligned["status"] == "ok"
        and float(aligned["expectancy"]) > 0
        and aligned["profit_factor"] is not None
        and float(aligned["profit_factor"]) > 1
        and positive_aligned_folds > len(folds) / 2
        and overlap_count == 0
        and diversified
    )
    lineage_contract_violations = (
        int(match_audit["future_lineage_rejected_row_count"])
        + int(match_audit["lineage_missing_or_invalid_row_count"])
    )
    if invalid_regime.any() or lineage_contract_violations > 0:
        status = "INCONCLUSIVE"
    elif coverage < 0.5 or len(folds) < 2:
        status = "INSUFFICIENT_COVERAGE"
    elif aligned["status"] != "ok" or float(aligned["expectancy"]) <= 0:
        status = "NO_EDGE"
    elif robust:
        status = "ALIGNED_ROBUST_OOS"
    else:
        status = "ALIGNED_PROMISING"
    return {
        "regime_source_status": "loaded",
        "regime_source_path": str(path),
        "regime_rows": int(len(source)),
        "regime_column": regime_column,
        "regime_enum_status": "blocked" if invalid_regime.any() else "ok",
        "invalid_regime_row_count": int(invalid_regime.sum()),
        "invalid_regime_values": invalid_regime_values,
        "matched_closed_trades": int(len(known)),
        "closed_trades_without_regime": int(len(closed) - len(known)),
        "regime_coverage_rate": float(coverage),
        "regime_status": status,
        "walkforward_status": (
            "ok"
            if splits
            and overlap_count == 0
            and not invalid_regime.any()
            and lineage_contract_violations == 0
            else "blocked"
        ),
        "split_engine": "walkforward_anti_leakage_split_engine_v1",
        "embargo_seconds": int(embargo_seconds),
        "fold_count": int(len(folds)),
        "folds": folds,
        "aggregate_oos": aggregate,
        "positive_aligned_fold_count": int(positive_aligned_folds),
        "aligned_diversified_across_pair_and_side": bool(diversified),
        "temporal_overlap_count": int(overlap_count),
        "leakage_detected": bool(overlap_count),
        "warnings": (
            (["invalid_regime_enum_values"] if invalid_regime.any() else [])
            + (["point_in_time_lineage_contract_violated"] if lineage_contract_violations else [])
        ),
        **match_audit,
    }


def _decision(
    financial_status: str,
    score_status: str,
    regime_status: str,
    leakage_detected: bool,
) -> str:
    if financial_status != "ok":
        return "BLOCKED_SOURCE_INTEGRITY"
    if score_status in {"SOURCE_MISSING", "INSUFFICIENT_COVERAGE", "UNCALIBRATED"}:
        return "SCORE_RECALIBRATION_REQUIRED"
    if regime_status in {"SOURCE_MISSING", "INSUFFICIENT_COVERAGE", "NO_EDGE", "INCONCLUSIVE"}:
        return "PESQUISAR_ALIGNED"
    if (
        score_status in {"CALIBRATED", "PARTIALLY_CALIBRATED"}
        and regime_status == "ALIGNED_ROBUST_OOS"
        and not leakage_detected
    ):
        return "READY_FOR_SHADOW_OPPORTUNITY_ENGINE"
    return "MANTER_BASELINE"


def _safe_report_path(root: Path, output_report: str | Path | None) -> Path:
    target = Path(output_report) if output_report is not None else DEFAULT_REPORT
    target = target if target.is_absolute() else root / target
    target = target.resolve()
    allowed = (root / "data" / "reports").resolve()
    try:
        target.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("output_report_must_be_under_data_reports") from exc
    if target.suffix.lower() != ".json":
        raise ValueError("output_report_must_be_json")
    return target


def _blocked_report(
    *,
    paper_db: str | Path,
    reason: str,
    detail: str | None,
    write_requested: bool,
    report_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "status": "blocked",
        "reason": reason,
        "decision": "BLOCKED_SOURCE_INTEGRITY",
        "edge_status": "SOURCE_INTEGRITY_BLOCKED",
        "score_status": "SOURCE_MISSING",
        "regime_status": "SOURCE_MISSING",
        "source": {
            "paper_db_path": str(Path(paper_db).resolve()),
            "paper_db_sha256": None,
            "sqlite_integrity_check": None,
            "integrity_error_detail": detail,
            "total_trade_rows": 0,
            "closed_trade_count": 0,
            "open_trade_count": 0,
            "order_count": 0,
        },
        "financial_closeout": {},
        "score_calibration": _empty_score_calibration("SOURCE_MISSING", None),
        "regime_oos": _empty_regime("SOURCE_MISSING", None),
        "gates": {"financial_source_integrity": False, "ready_for_shadow_opportunity_engine": False},
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": bool(write_requested),
        "write_performed": False,
        "output_report": str(report_path),
    }


def build_paper_edge_foundation_v1(
    *,
    project_root: str | Path,
    paper_db: str | Path,
    score_source: str | Path | None = None,
    regime_source: str | Path | None = None,
    write_report: bool = False,
    output_report: str | Path | None = None,
    certified_cut: str = DEFAULT_CERTIFIED_CUT,
    embargo_seconds: int = DEFAULT_EMBARGO_SECONDS,
    minimum_regime_sample: int = DEFAULT_MIN_REGIME_SAMPLE,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    report_path = _safe_report_path(root, output_report)
    try:
        source = read_authoritative_paper_source(paper_db)
        closed, source_counts = prepare_closed_trades(source["trades"])
        financial = build_financial_closeout(closed, source["orders"], certified_cut)
    except SourceIntegrityError as exc:
        return _blocked_report(
            paper_db=paper_db,
            reason=exc.reason,
            detail=exc.detail,
            write_requested=write_report,
            report_path=report_path,
        )

    score = build_score_calibration(score_source, closed, source["orders"])
    regime = build_regime_oos(
        regime_source,
        closed,
        source["orders"],
        embargo_seconds=max(0, int(embargo_seconds)),
        minimum_sample=max(1, int(minimum_regime_sample)),
    )
    net_pnl = float(financial["net_pnl"])
    edge_status = "POSITIVE_REPORTED_REALIZED_PNL" if net_pnl > 0 else "NON_POSITIVE_REPORTED_REALIZED_PNL"
    decision = _decision(
        "ok",
        score["score_status"],
        regime["regime_status"],
        bool(regime["leakage_detected"]),
    )
    ready = decision == "READY_FOR_SHADOW_OPPORTUNITY_ENGINE"
    optional_missing = score["score_status"] == "SOURCE_MISSING" or regime["regime_status"] == "SOURCE_MISSING"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "status": "ok",
        "reason": (
            "financial_closeout_completed_optional_sources_missing"
            if optional_missing
            else "paper_edge_foundation_completed"
        ),
        "decision": decision,
        "edge_status": edge_status,
        "score_status": score["score_status"],
        "regime_status": regime["regime_status"],
        "source": {
            "paper_db_path": str(source["path"]),
            "paper_db_sha256": source["sha256_before"],
            "paper_db_sha256_after": source["sha256_after"],
            "source_hash_invariant": source["sha256_before"] == source["sha256_after"],
            "sqlite_integrity_check": source["sqlite_integrity_check"],
            **source_counts,
            "order_count": int(len(source["orders"])),
        },
        "financial_closeout": financial,
        "score_calibration": score,
        "regime_oos": regime,
        "gates": {
            "financial_source_integrity": True,
            "score_coverage_sufficient": score["score_status"] in {"CALIBRATED", "PARTIALLY_CALIBRATED"},
            "regime_coverage_sufficient": regime["regime_status"] not in {"SOURCE_MISSING", "INSUFFICIENT_COVERAGE"},
            "anti_leakage_clear": not bool(regime["leakage_detected"]),
            "ready_for_shadow_opportunity_engine": ready,
            "paper_strategy_change_authorized": False,
        },
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_report": str(report_path),
    }
    report = json_safe(report)
    if write_report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report["write_performed"] = True
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return report


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value
