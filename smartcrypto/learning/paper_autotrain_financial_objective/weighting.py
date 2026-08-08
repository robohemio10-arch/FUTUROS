"""Magnitude-aware sample weighting for daily Qlib / AI Shadow challengers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import KNOWN_FINANCIAL_SAMPLE_INVALID_IDS
from .utils import _numeric_trade_id, _trade_key_series


def _weight_microbatch(
    microbatch: pd.DataFrame,
    research: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    if microbatch.empty:
        return microbatch.copy(), 0
    weighted = microbatch.copy()
    weighted["_trade_key"] = _trade_key_series(weighted)
    research_map = (
        research.drop_duplicates("_trade_key", keep="last").set_index("_trade_key")
        if not research.empty
        else pd.DataFrame()
    )
    mapping = {
        "net_pnl": "financial_net_pnl",
        "winner_capture_ratio": "financial_winner_capture_ratio",
        "profit_left_on_table": "financial_profit_left_on_table",
        "loss_path_classification": "financial_loss_path_classification",
        "qlib_score": "financial_qlib_score",
        "ai_shadow_probability": "financial_ai_shadow_probability",
    }
    for source, target in mapping.items():
        if not research_map.empty and source in research_map.columns:
            weighted[target] = weighted["_trade_key"].map(research_map[source])
        else:
            weighted[target] = (
                pd.NA if source == "loss_path_classification" else np.nan
            )
    fallback_source = weighted.get(
        "target_return",
        weighted.get(
            "net_pnl",
            weighted.get(
                "pnl_fechado",
                pd.Series(np.nan, index=weighted.index, dtype=float),
            ),
        ),
    )
    fallback_pnl = pd.to_numeric(fallback_source, errors="coerce")
    weighted["financial_net_pnl"] = pd.to_numeric(
        weighted["financial_net_pnl"], errors="coerce"
    ).combine_first(fallback_pnl)
    invalid = weighted["_trade_key"].map(_numeric_trade_id).isin(
        KNOWN_FINANCIAL_SAMPLE_INVALID_IDS
    )
    invalid_count = int(invalid.sum())
    weighted = weighted.loc[~invalid].copy()
    pnl = pd.to_numeric(weighted["financial_net_pnl"], errors="coerce")
    nonzero = pnl.abs().loc[pnl.abs() > 0]
    scale = float(nonzero.median()) if not nonzero.empty else 1.0
    magnitude = (
        (pnl.abs() / max(scale, 1e-12))
        .clip(lower=0.0, upper=4.0)
        .fillna(0.0)
    )
    weights = pd.Series(1.0 + 0.50 * magnitude, index=weighted.index, dtype=float)
    capture = pd.to_numeric(
        weighted["financial_winner_capture_ratio"], errors="coerce"
    )
    winners = pnl.gt(0)
    uncaptured = (1.0 - capture).clip(lower=0.0, upper=1.0).fillna(0.0)
    weights += pd.Series(
        np.where(winners, 0.75 * uncaptured, 0.0), index=weighted.index
    )
    loss_class = weighted["financial_loss_path_classification"].astype("string")
    entry_filter = loss_class.eq("entry_filter_candidate").fillna(False)
    profit_protection = loss_class.eq("profit_protection_exit_candidate").fillna(False)
    weights += pd.Series(np.where(entry_filter, 0.75, 0.0), index=weighted.index)
    weights -= pd.Series(np.where(profit_protection, 0.25, 0.0), index=weighted.index)
    weighted["financial_sample_weight"] = weights.clip(lower=0.25, upper=5.0)
    weighted["financial_objective_classification"] = np.select(
        [
            winners & capture.notna(),
            winners,
            pnl.lt(0) & profit_protection,
            pnl.lt(0),
        ],
        [
            "winner_capture_learning",
            "winner_profit_learning",
            "profit_protection_learning",
            "entry_filter_learning",
        ],
        default="neutral",
    )
    return weighted.drop(columns=["_trade_key"]).reset_index(drop=True), invalid_count
