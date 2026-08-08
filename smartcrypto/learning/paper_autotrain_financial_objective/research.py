"""Read-only financial evidence enrichment for the daily auto-training objective."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)
from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    prepare_microbatch,
)
from smartcrypto.research.profit_research.paper_analysis import financial_metrics

from .contracts import (
    DEFAULT_SCORE_SOURCES,
    DEFAULT_TRADER_MASTER,
    KNOWN_FINANCIAL_SAMPLE_INVALID_IDS,
)
from .utils import (
    _first_finite,
    _mean_numeric,
    _mean_or_none,
    _numeric_trade_id,
    _row_trade_key,
    _trade_key_series,
)


def _prepare_financial_microbatch(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize both quarantine and foundation daily microbatch schemas."""

    if frame.empty:
        return pd.DataFrame()
    normalized = frame.copy()
    if "target_profitable" not in normalized.columns:
        if "label_sign" in normalized.columns:
            label = pd.to_numeric(normalized["label_sign"], errors="coerce")
            normalized["target_profitable"] = np.where(
                label.notna(), (label > 0).astype(int), np.nan
            )
        elif "net_pnl" in normalized.columns:
            pnl = pd.to_numeric(normalized["net_pnl"], errors="coerce")
            normalized["target_profitable"] = np.where(
                pnl.notna(), (pnl > 0).astype(int), np.nan
            )
    return prepare_microbatch(normalized)


def _authoritative_profit_sources_exist(root: Path) -> bool:
    return all(
        path.is_file()
        for path in (
            root / "config/freqtrade_paper_closed_trades_source_profile_v2.json",
            root / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
            root / DEFAULT_TRADER_MASTER,
        )
    )


def _prepare_research_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    output = frame.copy()
    output["_trade_key"] = _trade_key_series(output)
    numeric_id = output["_trade_key"].map(_numeric_trade_id)
    output["financial_sample_invalid"] = numeric_id.isin(KNOWN_FINANCIAL_SAMPLE_INVALID_IDS)
    output["financial_sample_invalid_reason"] = np.where(
        output["financial_sample_invalid"], "known_double_exit", None
    )
    net = pd.to_numeric(output.get("net_pnl"), errors="coerce")
    mfe = pd.to_numeric(output.get("max_unrealized_profit"), errors="coerce")
    fees = pd.to_numeric(
        output.get("fees", pd.Series(0.0, index=output.index)), errors="coerce"
    ).fillna(0.0)
    funding = pd.to_numeric(
        output.get("funding", pd.Series(0.0, index=output.index)), errors="coerce"
    ).fillna(0.0)
    slippage = pd.to_numeric(
        output.get("slippage_cost", pd.Series(0.0, index=output.index)),
        errors="coerce",
    ).fillna(0.0)
    mfe_net = mfe - fees - funding - slippage
    output["maximum_favorable_excursion_net"] = mfe_net
    output["winner_capture_ratio"] = np.where(
        (net > 0) & (mfe_net > 0), net / mfe_net, np.nan
    )
    output["profit_left_on_table"] = np.where(
        (net > 0) & (mfe_net > 0), np.maximum(mfe_net - net, 0.0), np.nan
    )
    output["profit_left_on_table_ratio"] = np.where(
        (net > 0) & (mfe_net > 0),
        np.maximum(mfe_net - net, 0.0) / mfe_net,
        np.nan,
    )
    output["loss_path_classification"] = np.select(
        [net.lt(0) & mfe.gt(0), net.lt(0)],
        ["profit_protection_exit_candidate", "entry_filter_candidate"],
        default="not_a_loser",
    )
    exit_reason = output.get(
        "exit_reason", pd.Series("", index=output.index, dtype="string")
    ).astype("string")
    duration = pd.to_numeric(output.get("duration_seconds"), errors="coerce")
    output["time_to_stop_seconds"] = np.where(
        net.lt(0) & exit_reason.str.contains("stop", case=False, na=False),
        duration,
        np.nan,
    )
    output["mfe_before_stop"] = np.where(
        pd.notna(output["time_to_stop_seconds"]), mfe, np.nan
    )
    return output


def _load_score_sources(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for relative in DEFAULT_SCORE_SOURCES:
        path = root / relative
        item: dict[str, Any] = {
            "path": relative.as_posix(),
            "exists": path.is_file(),
            "status": "missing",
            "row_count": 0,
        }
        if not path.is_file():
            inventory.append(item)
            continue
        try:
            loaded = _read_score_file(path)
        except (OSError, ValueError, json.JSONDecodeError, ImportError) as exc:
            item.update(status="unreadable", error=f"{type(exc).__name__}:{exc}")
            inventory.append(item)
            continue
        rows.extend(loaded)
        item.update(status="ok", row_count=len(loaded))
        inventory.append(item)
    return rows, inventory


def _read_score_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        return [dict(row) for row in pd.read_parquet(path).to_dict(orient="records")]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return _extract_rows(payload)
    raise ValueError(f"unsupported_score_source:{path.suffix}")


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    for key in (
        "rows",
        "records",
        "dataset",
        "decisions",
        "decision_sample",
        "target_records",
        "calibration_rows",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, Mapping))
    for value in payload.values():
        if isinstance(value, Mapping):
            rows.extend(_extract_rows(value))
    return rows


def _attach_scores(
    research: pd.DataFrame,
    score_rows: Sequence[Mapping[str, Any]],
    microbatch: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if research.empty:
        return research, {"qlib_score_rows": 0, "ai_shadow_score_rows": 0}
    output = research.copy()
    score_map: dict[str, dict[str, list[float]]] = {}
    all_rows = [*score_rows, *microbatch.to_dict(orient="records")]
    for row in all_rows:
        key = _row_trade_key(row)
        if not key:
            continue
        qlib = _first_finite(
            row.get("qlib_score"),
            row.get("qlib_probability"),
            row.get("prediction_score"),
            row.get("model_score"),
            row.get("qlib_rank_probability"),
        )
        shadow = _first_finite(
            row.get("ai_shadow_probability"),
            row.get("probability_quality"),
            row.get("ai_shadow_score"),
            row.get("shadow_score"),
        )
        target = score_map.setdefault(key, {"qlib": [], "shadow": []})
        if qlib is not None:
            target["qlib"].append(qlib)
        if shadow is not None:
            target["shadow"].append(min(1.0, max(0.0, shadow)))
    output["qlib_score"] = output["_trade_key"].map(
        lambda key: _mean_or_none(score_map.get(str(key), {}).get("qlib", []))
    )
    output["ai_shadow_probability"] = output["_trade_key"].map(
        lambda key: _mean_or_none(score_map.get(str(key), {}).get("shadow", []))
    )
    return output, {
        "qlib_score_rows": int(output["qlib_score"].notna().sum()),
        "ai_shadow_score_rows": int(output["ai_shadow_probability"].notna().sum()),
        "research_row_count": int(len(output)),
    }


def _resolve_master_rows(
    root: Path,
    supplied: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if supplied is not None:
        return [dict(row) for row in supplied], {"status": "ok", "reason": "in_memory"}
    master = read_trader_master_readonly(
        project_root=root,
        trader_master_path=root / DEFAULT_TRADER_MASTER,
    )
    return (
        [dict(row) for row in master.source_rows]
        if master.report.get("status") == "ok"
        else [],
        dict(master.report),
    )


def _build_trader_master_reference(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        pnl = _first_finite(row.get("net_pnl"), row.get("pnl_fechado"))
        if pnl is None:
            continue
        key = _row_trade_key(row) or f"master-{index}"
        trade_id = _numeric_trade_id(key)
        if trade_id in KNOWN_FINANCIAL_SAMPLE_INVALID_IDS:
            continue
        open_value = (
            row.get("open_time_utc")
            or row.get("open_time")
            or row.get("horario_abertura")
        )
        close_value = (
            row.get("close_time_utc")
            or row.get("close_time")
            or row.get("horario_fechamento")
        )
        open_time = pd.to_datetime(open_value, utc=True, errors="coerce")
        close_time = pd.to_datetime(close_value, utc=True, errors="coerce")
        normalized.append(
            {
                "trade_id": key,
                "symbol": str(row.get("symbol") or row.get("moeda") or "unknown").upper(),
                "side": str(row.get("side") or row.get("fechar_side") or "unknown").lower(),
                "open_time_utc": open_time,
                "close_time_utc": close_time,
                "hour_utc": open_time.hour if pd.notna(open_time) else None,
                "net_pnl": pnl,
            }
        )
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return {
            "trade_count": 0,
            "metrics": {},
            "top_profitable_segments": [],
            "top_harmful_segments": [],
        }
    metrics = financial_metrics(frame)
    segments: list[dict[str, Any]] = []
    for dimension in ("symbol", "side", "hour_utc"):
        for value, subset in frame.groupby(dimension, dropna=False, sort=True):
            segment_metrics = financial_metrics(subset)
            segments.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "trade_count": int(len(subset)),
                    "net_pnl": segment_metrics.get("net_pnl"),
                    "expectancy": segment_metrics.get("expectancy"),
                    "profit_factor": segment_metrics.get("profit_factor"),
                }
            )
    profitable = sorted(
        segments,
        key=lambda row: (-float(row["net_pnl"]), row["dimension"], row["value"]),
    )[:10]
    harmful = sorted(
        segments,
        key=lambda row: (float(row["net_pnl"]), row["dimension"], row["value"]),
    )[:10]
    return {
        "trade_count": int(len(frame)),
        "metrics": metrics,
        "top_profitable_segments": profitable,
        "top_harmful_segments": harmful,
    }


def _winner_capture_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "winner_count": 0,
            "winner_capture_ratio": None,
            "profit_left_on_table": None,
        }
    net = pd.to_numeric(frame.get("net_pnl"), errors="coerce")
    winners = frame.loc[net.gt(0)].copy()
    capture = pd.to_numeric(
        winners.get("winner_capture_ratio"), errors="coerce"
    ).dropna()
    left = pd.to_numeric(
        winners.get("profit_left_on_table"), errors="coerce"
    ).dropna()
    return {
        "winner_count": int(len(winners)),
        "winner_with_mfe_count": int(len(capture)),
        "winner_capture_ratio": float(capture.mean()) if not capture.empty else None,
        "winner_capture_ratio_basis": (
            "realized_net_pnl_over_maximum_favorable_excursion_net_of_known_costs"
        ),
        "median_winner_capture_ratio": float(capture.median()) if not capture.empty else None,
        "profit_left_on_table": float(left.sum()) if not left.empty else None,
        "low_capture_winner_count": int((capture < 0.50).sum()) if not capture.empty else 0,
    }


def _loser_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"loser_count": 0, "classification_counts": {}}
    net = pd.to_numeric(frame.get("net_pnl"), errors="coerce")
    losers = frame.loc[net.lt(0)].copy()
    counts = (
        losers.get("loss_path_classification", pd.Series(dtype="string"))
        .astype("string")
        .value_counts()
        .sort_index()
        .to_dict()
    )
    return {
        "loser_count": int(len(losers)),
        "classification_counts": {str(key): int(value) for key, value in counts.items()},
        "average_mae_pct": _mean_numeric(losers, "mae_pct"),
        "average_mfe_pct_before_loss": _mean_numeric(losers, "mfe_pct"),
        "average_time_to_stop_seconds": _mean_numeric(losers, "time_to_stop_seconds"),
        "average_time_to_mfe_seconds": _mean_numeric(losers, "time_to_mfe_seconds"),
        "average_time_to_mae_seconds": _mean_numeric(losers, "time_to_mae_seconds"),
    }


def _data_limitations(
    research_report: Mapping[str, Any],
    score_coverage: Mapping[str, Any],
) -> list[str]:
    limitations = [
        str(item) for item in research_report.get("data_limitations", [])
    ]
    if int(score_coverage.get("qlib_score_rows", 0)) == 0:
        limitations.append("qlib_trade_level_score_unavailable")
    if int(score_coverage.get("ai_shadow_score_rows", 0)) == 0:
        limitations.append("ai_shadow_trade_level_score_unavailable")
    limitations.append(
        "counterfactual_exit_uses_observed_fees_funding_and_execution_prices;"
        "separate_slippage_only_if_source_provides_it"
    )
    return sorted(set(limitations))
