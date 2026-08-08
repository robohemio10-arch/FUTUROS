"""Financial integrity, score enrichment and profit diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from .contracts import KNOWN_CORRUPT_PAPER_TRADE_IDS


def prepare_profit_dataset(
    frame: pd.DataFrame,
    *,
    score_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = _ensure_stable_identity(frame.copy())
    output["trade_id_numeric"] = output["stable_trade_id"].map(trade_id_from_identity)
    existing_eligible = (
        output["analysis_eligible"].eq(True).fillna(False).astype(bool)  # noqa: E712
        if "analysis_eligible" in output.columns
        else pd.Series(True, index=output.index, dtype=bool)
    )
    known_corrupt = output["trade_id_numeric"].isin(KNOWN_CORRUPT_PAPER_TRADE_IDS)
    accounting_bad = _accounting_invalid_mask(output)
    duplicate_identity = output["stable_trade_id"].duplicated(keep=False) | output[
        "stable_trade_id"
    ].isna()
    eligible = existing_eligible & ~known_corrupt & ~accounting_bad & ~duplicate_identity
    output["profit_optimization_eligible"] = eligible
    output["profit_optimization_exclusion_reason"] = [
        _exclusion_reason(
            existing_ok=bool(existing_eligible.iloc[index]),
            known_corrupt=bool(known_corrupt.iloc[index]),
            accounting_bad=bool(accounting_bad.iloc[index]),
            duplicate_identity=bool(duplicate_identity.iloc[index]),
            existing_reason=(
                output.iloc[index].get("rejection_reason")
                or output.iloc[index].get("analysis_block_reason")
            ),
        )
        for index in range(len(output))
    ]
    net = numeric_series(output, "net_pnl")
    mfe = numeric_series(output, "mfe_absolute")
    mae = numeric_series(output, "mae_absolute")
    output["winner_capture_ratio"] = np.where(
        (net > 0) & (mfe > 0), net / mfe, np.nan
    )
    output["winner_profit_left_on_table"] = np.where(
        (net > 0) & (mfe > 0), np.maximum(mfe - net, 0.0), np.nan
    )
    output["winner_giveback_ratio"] = np.where(
        (net > 0) & (mfe > 0), np.maximum(mfe - net, 0.0) / mfe, np.nan
    )
    output["mae_loss_pressure"] = np.where(net < 0, np.abs(mae), np.nan)
    output["loser_type"] = output.apply(_classify_loser, axis=1)
    score_map, score_report = normalize_score_rows(score_rows)
    output["qlib_score"] = output["stable_trade_id"].map(
        lambda value: score_map.get(str(value), {}).get("qlib_score")
    )
    output["ai_shadow_score"] = output["stable_trade_id"].map(
        lambda value: score_map.get(str(value), {}).get("ai_shadow_score")
    )
    qlib_numeric = pd.to_numeric(output["qlib_score"], errors="coerce")
    qlib_rank = pd.Series(np.nan, index=output.index, dtype=float)
    eligible_qlib = qlib_numeric.loc[eligible & qlib_numeric.notna()]
    if not eligible_qlib.empty:
        qlib_rank.loc[eligible_qlib.index] = eligible_qlib.rank(
            method="average", pct=True
        )
    output["qlib_rank_score"] = qlib_rank
    ai_numeric = pd.to_numeric(output["ai_shadow_score"], errors="coerce")
    output["ensemble_score"] = pd.concat(
        [output["qlib_rank_score"], ai_numeric.clip(lower=0.0, upper=1.0)], axis=1
    ).mean(axis=1, skipna=False)
    output.loc[~eligible, ["qlib_rank_score", "ensemble_score"]] = np.nan
    return output, {
        **score_report,
        "paper_rows_with_qlib_score": int(output["qlib_score"].notna().sum()),
        "paper_rows_with_ai_shadow_score": int(output["ai_shadow_score"].notna().sum()),
        "paper_rows_with_ensemble_score": int(output["ensemble_score"].notna().sum()),
    }


def normalize_score_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    values: dict[str, dict[str, list[float]]] = {}
    ignored = 0
    for row in rows:
        identity = identity_from_row(row)
        if identity is None:
            ignored += 1
            continue
        qlib = first_finite(
            row.get("qlib_score"),
            row.get("qlib_probability"),
            row.get("prediction_score"),
            row.get("model_score"),
        )
        shadow = first_finite(
            row.get("ai_shadow_probability"),
            row.get("probability_quality"),
            row.get("ai_shadow_score"),
            row.get("shadow_score"),
        )
        target = values.setdefault(identity, {"qlib_score": [], "ai_shadow_score": []})
        if qlib is not None:
            target["qlib_score"].append(qlib)
        if shadow is not None:
            target["ai_shadow_score"].append(clamp01(shadow))
    conflicts: list[str] = []
    normalized: dict[str, dict[str, float]] = {}
    for identity, fields in sorted(values.items()):
        item: dict[str, float] = {}
        for field, field_values in fields.items():
            if not field_values:
                continue
            unique = _unique_close(field_values)
            if len(unique) > 1:
                conflicts.append(f"{identity}:{field}")
                continue
            item[field] = float(unique[0])
        if item:
            normalized[identity] = item
    return normalized, {
        "score_input_row_count": len(rows),
        "score_identity_count": len(normalized),
        "score_rows_ignored_without_identity": ignored,
        "score_conflict_count": len(conflicts),
        "score_conflicts": conflicts[:50],
    }


def normalize_trader_master_rows(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        net = first_finite(row.get("net_pnl"), row.get("pnl_fechado"))
        if net is None:
            continue
        identity = identity_from_row(row) or f"trader-master-row-{index}"
        normalized.append(
            {
                "stable_trade_id": identity,
                "trade_id_numeric": trade_id_from_identity(identity),
                "symbol": str(row.get("symbol") or row.get("moeda") or "unknown").upper(),
                "side": str(row.get("side") or row.get("fechar_side") or "unknown").lower(),
                "open_time_utc": pd.to_datetime(
                    row.get("open_time") or row.get("horario_abertura"),
                    utc=True,
                    errors="coerce",
                ),
                "close_time_utc": pd.to_datetime(
                    row.get("close_time") or row.get("horario_fechamento"),
                    utc=True,
                    errors="coerce",
                ),
                "net_pnl": net,
            }
        )
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "stable_trade_id",
                "trade_id_numeric",
                "symbol",
                "side",
                "open_time_utc",
                "close_time_utc",
                "net_pnl",
                "profit_optimization_eligible",
                "profit_optimization_exclusion_reason",
            ]
        )
    duplicate = frame["stable_trade_id"].duplicated(keep=False)
    corrupt = frame["trade_id_numeric"].isin(KNOWN_CORRUPT_PAPER_TRADE_IDS)
    frame["profit_optimization_eligible"] = ~duplicate & ~corrupt
    frame["profit_optimization_exclusion_reason"] = np.select(
        [duplicate, corrupt],
        ["duplicate_trade_identity", "known_duplicate_full_exit_financial_corruption"],
        default=None,
    )
    return frame


def profit_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "net_pnl" not in frame.columns:
        return _empty_profit_metrics()
    ordered = sort_trades(frame)
    pnl = pd.to_numeric(ordered["net_pnl"], errors="coerce").dropna()
    if pnl.empty:
        return _empty_profit_metrics()
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss_abs = float(-losers.sum())
    cumulative = pnl.cumsum()
    peak = cumulative.cummax().clip(lower=0.0)
    drawdown = peak - cumulative
    capture = numeric_series(ordered, "winner_capture_ratio").dropna()
    left = numeric_series(ordered, "winner_profit_left_on_table").dropna()
    return {
        "trade_count": int(len(pnl)),
        "net_pnl": float(pnl.sum()),
        "expectancy": float(pnl.mean()),
        "profit_factor": gross_profit / gross_loss_abs if gross_loss_abs > 0 else None,
        "win_rate": float((pnl > 0).mean()),
        "average_win": float(winners.mean()) if not winners.empty else 0.0,
        "average_loss": float(losers.mean()) if not losers.empty else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": -gross_loss_abs,
        "maximum_drawdown": float(drawdown.max()),
        "winner_capture_ratio_mean": float(capture.mean()) if not capture.empty else None,
        "winner_capture_ratio_median": float(capture.median()) if not capture.empty else None,
        "profit_left_on_table_total": float(left.sum()) if not left.empty else None,
    }


def build_winner_capture_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    winners = frame.loc[pd.to_numeric(frame.get("net_pnl"), errors="coerce") > 0].copy()
    capture = numeric_series(winners, "winner_capture_ratio").dropna()
    left = numeric_series(winners, "winner_profit_left_on_table").dropna()
    mfe = numeric_series(winners, "mfe_absolute").dropna()
    realized = numeric_series(winners, "net_pnl").dropna()
    return {
        "winner_count": int(len(winners)),
        "winner_with_path_count": int(capture.notna().sum()),
        "average_winner_capture_ratio": float(capture.mean()) if not capture.empty else None,
        "median_winner_capture_ratio": float(capture.median()) if not capture.empty else None,
        "p25_winner_capture_ratio": float(capture.quantile(0.25)) if not capture.empty else None,
        "p75_winner_capture_ratio": float(capture.quantile(0.75)) if not capture.empty else None,
        "realized_positive_pnl": float(realized.sum()) if not realized.empty else 0.0,
        "observed_mfe_profit_potential": float(mfe.sum()) if not mfe.empty else None,
        "profit_left_on_table_total": float(left.sum()) if not left.empty else None,
        "low_capture_winner_count": int((capture < 0.50).sum()) if not capture.empty else 0,
    }


def build_loser_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    losers = frame.loc[pd.to_numeric(frame.get("net_pnl"), errors="coerce") < 0].copy()
    counts = value_counts(losers.get("loser_type", pd.Series(dtype="string")))
    pnl_by_type: dict[str, float] = {}
    if not losers.empty and "loser_type" in losers.columns:
        for loser_type, subset in losers.groupby("loser_type", dropna=False, sort=True):
            pnl_by_type[str(loser_type)] = float(
                pd.to_numeric(subset["net_pnl"], errors="coerce").sum()
            )
    recoverable = losers.loc[losers["loser_type"].eq("winner_to_loser")]
    return {
        "loser_count": int(len(losers)),
        "loser_type_counts": counts,
        "loser_type_net_pnl": pnl_by_type,
        "winner_to_loser_count": int(len(recoverable)),
        "winner_to_loser_net_pnl": float(numeric_series(recoverable, "net_pnl").sum()),
        "winner_to_loser_observed_mfe": float(
            numeric_series(recoverable, "mfe_absolute").sum()
        ),
    }


def identity_from_row(row: Mapping[str, Any]) -> str | None:
    for key in ("stable_trade_id", "order_id", "event_id", "trade_id", "source_trade_id"):
        value = row.get(key)
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        if raw.startswith("outcome_order_id_"):
            raw = raw.removeprefix("outcome_order_id_")
        if raw.startswith("freqtrade-paper-"):
            return raw
        numeric = finite_or_none(raw)
        if numeric is not None and float(numeric).is_integer():
            return f"freqtrade-paper-{int(numeric)}"
        return raw
    return None


def trade_id_from_identity(value: Any) -> float | None:
    if value is None or value is pd.NA:
        return None
    raw = str(value).strip()
    if raw.startswith("freqtrade-paper-"):
        raw = raw.removeprefix("freqtrade-paper-")
    numeric = finite_or_none(raw)
    return numeric if numeric is not None and float(numeric).is_integer() else None


def sort_trades(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.copy()
    columns = [column for column in ("close_time_utc", "stable_trade_id") if column in output]
    if "close_time_utc" in output:
        output["close_time_utc"] = pd.to_datetime(
            output["close_time_utc"], utc=True, errors="coerce"
        )
    if not columns:
        return output.reset_index(drop=True)
    return output.sort_values(columns, na_position="last").reset_index(drop=True)


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def first_finite(*values: Any) -> float | None:
    for value in values:
        parsed = finite_or_none(value)
        if parsed is not None:
            return parsed
    return None


def finite_or_none(value: Any) -> float | None:
    if value is None or value is pd.NA or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def value_counts(series: pd.Series) -> dict[str, int]:
    if series.empty:
        return {}
    values = series.dropna().astype(str)
    return {str(key): int(value) for key, value in values.value_counts().sort_index().items()}


def _accounting_invalid_mask(frame: pd.DataFrame) -> pd.Series:
    invalid = pd.Series(False, index=frame.index, dtype=bool)
    if "financial_decomposition_status" in frame.columns:
        status = frame["financial_decomposition_status"].astype("string")
        invalid |= status.notna() & ~status.isin(
            ["authoritative_reconciled", "<NA>", "unknown", "not_available"]
        )
    if "accounting_reconciled" in frame.columns:
        values = frame["accounting_reconciled"]
        invalid |= values.notna() & values.ne(True)  # noqa: E712
    return invalid


def _exclusion_reason(
    *,
    existing_ok: bool,
    known_corrupt: bool,
    accounting_bad: bool,
    duplicate_identity: bool,
    existing_reason: Any,
) -> str | None:
    if duplicate_identity:
        return "duplicate_trade_identity"
    if known_corrupt:
        return "known_duplicate_full_exit_financial_corruption"
    if accounting_bad:
        return "financial_accounting_not_reconciled"
    if not existing_ok:
        return str(existing_reason or "upstream_analysis_ineligible")
    return None


def _classify_loser(row: pd.Series) -> str:
    net = finite_or_none(row.get("net_pnl"))
    if net is None or net >= 0:
        return "not_loss"
    mfe = finite_or_none(row.get("mfe_absolute"))
    time_to_mfe = finite_or_none(row.get("time_to_mfe_seconds"))
    time_to_mae = finite_or_none(row.get("time_to_mae_seconds"))
    if mfe is not None and mfe > 0:
        return "winner_to_loser"
    if time_to_mae is not None and time_to_mae <= 300 and (
        time_to_mfe is None or time_to_mae <= time_to_mfe
    ):
        return "immediate_adverse"
    return "persistent_loss"


def _ensure_stable_identity(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    if "stable_trade_id" in output.columns:
        output["stable_trade_id"] = output["stable_trade_id"].astype("string")
    elif "trade_id" in output.columns:
        output["stable_trade_id"] = output["trade_id"].map(
            lambda value: (
                f"freqtrade-paper-{int(float(value))}"
                if finite_or_none(value) is not None
                else None
            )
        )
    else:
        output["stable_trade_id"] = pd.Series(
            [f"row-{index}" for index in range(len(output))],
            index=output.index,
            dtype="string",
        )
    return output


def _unique_close(values: Sequence[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or not math.isclose(value, result[-1], rel_tol=1e-9, abs_tol=1e-12):
            result.append(value)
    return result


def _empty_profit_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "net_pnl": 0.0,
        "expectancy": 0.0,
        "profit_factor": None,
        "win_rate": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "maximum_drawdown": 0.0,
        "winner_capture_ratio_mean": None,
        "winner_capture_ratio_median": None,
        "profit_left_on_table_total": None,
    }
