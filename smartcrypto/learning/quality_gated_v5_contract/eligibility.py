"""Deterministic multi-reason row eligibility classification."""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from .contracts import BLOCK_REASON_PRECEDENCE


def normalize_trade_id(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def flatten_reasons(*groups: Iterable[str] | None) -> list[str]:
    reasons: set[str] = set()
    for group in groups:
        if group is None:
            continue
        for reason in group:
            if reason:
                reasons.add(str(reason))
    precedence = {reason: index for index, reason in enumerate(BLOCK_REASON_PRECEDENCE)}
    return sorted(reasons, key=lambda reason: (precedence.get(reason, len(precedence)), reason))


def choose_primary_reason(block_reasons: list[str]) -> str:
    return block_reasons[0] if block_reasons else "ELIGIBLE"


def build_eligibility(
    trades: pd.DataFrame,
    provenance: pd.DataFrame,
    freshness: pd.DataFrame,
    feature_quality: pd.DataFrame,
    temporal_leakage: pd.DataFrame,
    *,
    feature_name_audit: dict[str, Any],
) -> pd.DataFrame:
    trade = trades.reset_index(drop=True)
    ids = (
        trade["trade_id"].map(normalize_trade_id)
        if "trade_id" in trade.columns
        else pd.Series([""] * len(trade))
    )
    duplicate_mask = ids.ne("") & ids.duplicated(keep=False)

    records: list[dict[str, Any]] = []
    global_feature_reasons = list(feature_name_audit.get("block_reasons", []))
    for index in range(len(trade)):
        identity_reasons: list[str] = []
        if not ids.iloc[index]:
            identity_reasons.append("BLOCKED_EMPTY_TRADE_ID")
        if duplicate_mask.iloc[index]:
            identity_reasons.append("BLOCKED_DUPLICATE_TRADE_ID")

        open_time = pd.to_datetime(
            trade.iloc[index].get("open_ts", trade.iloc[index].get("open_time_utc")),
            errors="coerce",
            utc=True,
        )
        if pd.isna(open_time):
            identity_reasons.append("BLOCKED_INVALID_OPEN_TIME")

        freshness_reasons: list[str] = []
        for timeframe in ("1m", "5m"):
            freshness_reasons.extend(
                freshness.iloc[index].get(
                    f"snapshot_{timeframe}_block_reasons", []
                )
            )

        block_reasons = flatten_reasons(
            identity_reasons,
            provenance.iloc[index].get("provenance_block_reasons", []),
            freshness_reasons,
            feature_quality.iloc[index].get("feature_block_reasons", []),
            temporal_leakage.iloc[index].get(
                "temporal_leakage_block_reasons", []
            ),
            global_feature_reasons,
        )
        warning_reasons: list[str] = []
        status = "BLOCKED" if block_reasons else "ELIGIBLE"
        records.append(
            {
                "trade_id": ids.iloc[index],
                "eligibility_status": status,
                "primary_reason": choose_primary_reason(block_reasons),
                "block_reasons": block_reasons,
                "warning_reasons": warning_reasons,
                "eligible_for_research_training": not block_reasons,
                "eligible_for_model_training": not block_reasons
                and not warning_reasons,
                "duplicate_trade_id": bool(duplicate_mask.iloc[index]),
            }
        )

    return pd.DataFrame(records)


def eligibility_summary(frame: pd.DataFrame) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    for reasons in frame["block_reasons"]:
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "eligible_rows": int(frame["eligible_for_model_training"].sum()),
        "blocked_rows": int((~frame["eligible_for_model_training"]).sum()),
        "duplicate_trade_ids": int(frame["duplicate_trade_id"].sum()),
        "primary_reason_counts": frame["primary_reason"]
        .value_counts(dropna=False)
        .sort_index()
        .to_dict(),
        "block_reason_counts": dict(sorted(reason_counts.items())),
    }
