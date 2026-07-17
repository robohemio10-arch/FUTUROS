"""Set-based comparison against the current official quality-gated dataset."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .eligibility import normalize_trade_id


def identity_set(frame: pd.DataFrame) -> set[str]:
    if "trade_id" not in frame.columns:
        return set()
    return {
        value
        for value in frame["trade_id"].map(normalize_trade_id).tolist()
        if value
    }


def compare_official_projection(
    official: pd.DataFrame,
    row_projection: pd.DataFrame,
) -> dict[str, Any]:
    official_ids = identity_set(official)
    universe_ids = identity_set(row_projection)
    projected_ids = {
        str(row.trade_id)
        for row in row_projection.itertuples(index=False)
        if bool(row.eligible_for_model_training) and str(row.trade_id).strip()
    }

    retained = official_ids & projected_ids
    blocked_official = official_ids - projected_ids
    new_eligible = projected_ids - official_ids
    official_missing_from_universe = official_ids - universe_ids

    reasons_by_id: dict[str, list[str]] = {}
    for row in row_projection.itertuples(index=False):
        trade_id = normalize_trade_id(getattr(row, "trade_id", ""))
        if trade_id:
            reasons_by_id[trade_id] = list(getattr(row, "block_reasons", []))

    unexplained_removed = sorted(
        trade_id
        for trade_id in blocked_official
        if trade_id not in official_missing_from_universe
        and not reasons_by_id.get(trade_id)
    )
    explained_removed = sorted(
        trade_id
        for trade_id in blocked_official
        if trade_id not in unexplained_removed
    )
    newly_blocked = sorted(
        trade_id
        for trade_id in universe_ids - official_ids
        if trade_id not in projected_ids
    )

    if unexplained_removed or official_missing_from_universe:
        status = "blocked"
        reason = "unexplained_official_identity_loss"
    elif blocked_official:
        status = "review_required"
        reason = "explained_quality_or_temporal_reduction"
    else:
        status = "ok"
        reason = "all_official_ids_retained"

    return {
        "status": status,
        "reason": reason,
        "official_rows": int(len(official)),
        "official_unique_trade_ids": len(official_ids),
        "projected_rows": len(projected_ids),
        "universe_unique_trade_ids": len(universe_ids),
        "official_ids_retained": len(retained),
        "official_ids_blocked": len(blocked_official),
        "new_ids_eligible": len(new_eligible),
        "newly_blocked_ids": len(newly_blocked),
        "official_ids_missing_from_universe": len(official_missing_from_universe),
        "unexplained_removed_official_ids": len(unexplained_removed),
        "retained_official_ids": sorted(retained),
        "blocked_official_ids": sorted(blocked_official),
        "explained_removed_official_ids": explained_removed,
        "newly_eligible_ids": sorted(new_eligible),
        "newly_blocked_trade_ids": newly_blocked,
        "official_missing_from_universe_ids": sorted(
            official_missing_from_universe
        ),
        "unexplained_removed_official_trade_ids": unexplained_removed,
    }
