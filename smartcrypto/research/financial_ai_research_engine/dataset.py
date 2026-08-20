"""Point-in-time dataset assembly for financial research."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import read_table
from smartcrypto.learning.feature_contracts.dataset_manifest import frame_hash
from smartcrypto.learning.feature_contracts.feature_contract import (
    METADATA_COLUMNS as INSTITUTIONAL_METADATA_COLUMNS,
    POST_TRADE_LEAKAGE_COLUMNS,
    build_feature_contract as build_institutional_feature_contract,
    classify_column,
)
from smartcrypto.research.paper_edge_foundation.foundation import (
    SourceIntegrityError,
    file_sha256,
    prepare_closed_trades,
    read_authoritative_paper_source,
)

from .contracts import (
    EXTENDED_POST_TRADE_OUTCOMES,
    FINANCIAL_EV_SEMANTICS,
    normalize_side,
    normalize_symbol,
    stable_hash,
)


FEATURE_TIMESTAMP_COLUMNS = ("feature_timestamp_utc", "timestamp", "ts")
FEATURE_AVAILABLE_COLUMNS = ("feature_available_at_utc", "available_at_utc")
REGIME_COLUMNS = ("entry_market_regime", "market_regime", "regime")
TIMEFRAME_SECONDS = {"15s": 15, "1m": 60, "5m": 300}

IDENTITY_DOMAINS = ("trade_id", "candidate_id", "order_id")
LOCAL_METADATA_COLUMNS = frozenset(
    {
        "id",
        "symbol",
        "pair",
        "side",
        "tf",
        "timeframe",
        "timestamp",
        "ts",
        "ts_ms",
        "feature_timestamp_utc",
        "feature_available_at_utc",
        "available_at_utc",
        "source_file",
        "source_hash",
        "source_row_identity",
        "record_hash",
        "model_version",
        "score_generated_at_utc",
        "score_available_at_utc",
        "regime_generated_at_utc",
        "regime_available_at_utc",
        *REGIME_COLUMNS,
    }
)

POST_TRADE_OUTCOME_NAMES = frozenset(
    {
        *POST_TRADE_LEAKAGE_COLUMNS,
        *EXTENDED_POST_TRADE_OUTCOMES,
    }
)


@dataclass(frozen=True)
class SourceFrame:
    name: str
    status: str
    path: str | None
    sha256: str | None
    frame: pd.DataFrame | None
    reason: str

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "path": self.path,
            "sha256": self.sha256,
            "row_count": int(len(self.frame)) if self.frame is not None else 0,
        }


def build_financial_training_dataset(
    *,
    project_root: str | Path,
    paper_db: str | Path | None,
    feature_source: str | Path | None = None,
    qlib_source: str | Path | None = None,
    regime_source: str | Path | None = None,
    trader_master_source: str | Path | None = None,
    execution_cost_source: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(project_root).resolve()
    sources: dict[str, Any] = {}
    if paper_db is None:
        return pd.DataFrame(), _missing_paper_report(sources, "SOURCE_MISSING")

    paper_path = _resolve_input(root, paper_db)
    try:
        paper = read_authoritative_paper_source(paper_path)
        closed, cohort = _prepare_financial_closed_trades(paper["trades"])
    except SourceIntegrityError as exc:
        sources["paper_db"] = {
            "status": "SOURCE_UNVERIFIED",
            "reason": exc.reason,
            "path": str(paper_path),
            "row_count": 0,
        }
        return pd.DataFrame(), _missing_paper_report(sources, "SOURCE_UNVERIFIED")

    sources["paper_db"] = {
        "status": "ok",
        "reason": "authoritative_paper_db_read_only",
        "path": str(paper_path),
        "sha256_before": paper["sha256_before"],
        "sha256_after": paper["sha256_after"],
        "source_hash_invariant": paper["sha256_before"] == paper["sha256_after"],
        "sqlite_integrity_check": paper["sqlite_integrity_check"],
        "row_count": int(len(paper["trades"])),
        **cohort,
    }

    frame = _paper_rows(closed, str(paper["sha256_before"]))

    feature = _read_optional_source(root, "feature_source", feature_source)
    qlib = _read_optional_source(root, "qlib_source", qlib_source)
    regime = _read_optional_source(root, "regime_source", regime_source)
    trader_master = _read_optional_source(
        root, "trader_master_source", trader_master_source
    )
    execution_cost = _read_optional_source(
        root, "execution_cost_source", execution_cost_source
    )
    optional_sources = (feature, qlib, regime, trader_master, execution_cost)
    sources.update({source.name: source.public() for source in optional_sources})

    frame, feature_columns, feature_summary = _attach_features(frame, feature)
    frame, qlib_summary = _attach_qlib(frame, qlib)
    frame, regime_summary = _attach_regime(frame, regime)
    tm_summary = _deterministic_linkage(frame, trader_master, "TM")
    frame, cost_summary = _attach_execution_cost(frame, execution_cost)
    frame = _finalize_lineage(frame, feature_columns)
    feature_contract = _feature_contract(frame, feature_columns, feature_summary)

    label_valid = _financial_label_valid_mask(frame)
    dataset_hash = _dataset_hash(frame, feature_columns)

    dataset_summary = {
        "total_rows": int(len(frame)),
        "lineage_valid_rows": int(frame["lineage_status"].eq("VALID").sum()),
        "trainable_rows": int(frame["trainable"].sum()),
        "excluded_rows": int((~frame["trainable"]).sum()),
        "positive_count": int(frame["positive_net_outcome"].eq(1).sum()),
        "negative_count": int(frame["positive_net_outcome"].eq(0).sum()),
        "valid_financial_label_count": int(label_valid.sum()),
        "invalid_financial_label_count": int((~label_valid).sum()),
        "financial_label_valid": bool(len(frame) and label_valid.all()),
        "candidate_linked_row_count": int(
            frame["candidate_linkage_status"].eq("LINKED").sum()
        ),
        "candidate_unlinked_row_count": int(
            frame["candidate_linkage_status"].ne("LINKED").sum()
        ),
        "symbol_counts": _counts(frame["symbol"]),
        "side_counts": _counts(frame["side"]),
        "regime_counts": _counts(frame["regime"]),
        "pnl_authority": "FREQTRADE_CLOSE_PROFIT_ABS",
        "financial_ev_semantics": FINANCIAL_EV_SEMANTICS,
        "dataset_hash": dataset_hash,
    }

    return frame, {
        "status": "ok" if not frame.empty else "BLOCKED",
        "reason": (
            "financial_training_dataset_built" if not frame.empty else "dataset_empty"
        ),
        "sources": sources,
        "dataset": dataset_summary,
        "lineage": {
            "feature": feature_summary,
            "qlib": qlib_summary,
            "regime": regime_summary,
            "lookahead_blocked_rows": int(
                frame["lineage_errors"].str.contains("LOOKAHEAD_BLOCKED").sum()
            ),
            "lineage_unverified_rows": int(
                frame["lineage_errors"].str.contains("LINEAGE_UNVERIFIED").sum()
            ),
            "invalid_label_rows": int(
                frame["lineage_errors"].str.contains("INVALID_FINANCIAL_LABEL").sum()
            ),
            "invalid_trade_identity_rows": int(
                frame["lineage_errors"].str.contains("INVALID_TRADE_IDENTITY").sum()
            ),
        },
        "feature_contract": feature_contract,
        "targets": {
            "primary_target": "realized_net_pnl_usdt",
            "classification_target": "positive_net_outcome",
            "pnl_authority": "FREQTRADE_CLOSE_PROFIT_ABS",
            "financial_label_valid": bool(len(frame) and label_valid.all()),
            "valid_financial_label_count": int(label_valid.sum()),
            "invalid_financial_label_count": int((~label_valid).sum()),
            "fees_or_funding_subtracted_again": False,
            "funding_sign_convention": "SOURCE_POSITIVE_REVENUE_NEGATIVE_COST",
            "label_available_after_decision_count": int(
                (
                    frame["label_available_at_utc"]
                    > frame["decision_timestamp_utc"]
                ).sum()
            ),
        },
        "cost_model": cost_summary,
        "trader_master_comparison": tm_summary,
    }


def _prepare_financial_closed_trades(
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Preserve canonical structural validation while failing labels closed per row.

    ``prepare_closed_trades`` is the authoritative structural validator used by
    Paper Edge Foundation.  Its contract intentionally rejects the complete
    source when any closed trade has a non-finite ``close_profit_abs``.  The
    Financial AI dataset has a stricter row-level learning contract: the source
    may remain readable, but the affected row must be retained as non-trainable
    with ``INVALID_FINANCIAL_LABEL``.

    To avoid duplicating the canonical closed-trade validation, this adapter
    relaxes only that one PnL check for the structural pass.  All other
    validations (is_open, timestamps, side and duration) still execute inside
    ``prepare_closed_trades`` unchanged.  The original PnL values are restored
    immediately afterwards; ``_paper_rows`` canonicalizes non-finite values to
    NaN, and ``_finalize_lineage`` excludes those rows from training.
    """

    try:
        closed, cohort = prepare_closed_trades(trades)
        cohort = dict(cohort)
        cohort.setdefault("closed_trade_invalid_authoritative_pnl_count", 0)
        cohort.setdefault("closed_trade_invalid_authoritative_pnl_ids", [])
        return closed, cohort
    except SourceIntegrityError as exc:
        if exc.reason != "closed_trade_missing_authoritative_pnl":
            raise

    if "id" not in trades.columns or "close_profit_abs" not in trades.columns:
        raise SourceIntegrityError("sqlite_required_columns_missing")

    raw = trades.copy()
    is_open = pd.to_numeric(raw["is_open"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    closed_mask = is_open.eq(0)
    pnl = pd.to_numeric(raw["close_profit_abs"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    invalid_closed_mask = closed_mask & pnl.isna()

    invalid_ids: list[int] = []
    for value in raw.loc[invalid_closed_mask, "id"].tolist():
        try:
            invalid_ids.append(int(value))
        except (TypeError, ValueError):
            # The canonical structural pass below remains responsible for
            # rejecting malformed identities.
            continue

    structural = raw.copy()
    structural.loc[invalid_closed_mask, "close_profit_abs"] = 0.0
    closed, cohort = prepare_closed_trades(structural)

    original_pnl_by_id = raw.set_index("id")["close_profit_abs"]
    closed = closed.copy()
    closed["close_profit_abs"] = closed["id"].map(original_pnl_by_id)

    enriched = dict(cohort)
    enriched["closed_trade_invalid_authoritative_pnl_count"] = len(invalid_ids)
    enriched["closed_trade_invalid_authoritative_pnl_ids"] = sorted(invalid_ids)
    enriched["financial_label_validation_mode"] = "ROW_LEVEL_FAIL_CLOSED"
    return closed, enriched


def _paper_rows(closed: pd.DataFrame, source_hash: str) -> pd.DataFrame:
    output = pd.DataFrame(index=closed.index)
    output["trade_id"] = pd.to_numeric(closed["id"], errors="coerce").astype("Int64")
    output["candidate_id"] = pd.Series(pd.NA, index=output.index, dtype="string")
    output["candidate_linkage_status"] = "CANDIDATE_UNLINKED"
    output["estimate_subject_id"] = output["trade_id"].map(
        lambda value: (
            f"trade:{int(value)}" if pd.notna(value) else "trade:INVALID"
        )
    )
    output["row_id"] = output["estimate_subject_id"].map(
        lambda value: stable_hash(
            {
                "schema": "financial_training_row_v1",
                "estimate_subject_id": value,
            }
        )
    )
    output["decision_timestamp_utc"] = pd.to_datetime(
        closed["open_date"], utc=True, errors="coerce"
    )
    output["label_available_at_utc"] = pd.to_datetime(
        closed["close_date"], utc=True, errors="coerce"
    )
    output["open_time_utc"] = output["decision_timestamp_utc"]
    output["close_time_utc"] = output["label_available_at_utc"]
    output["symbol"] = closed["pair"].map(normalize_symbol)
    output["pair"] = closed["pair"].astype(str)
    output["side"] = closed["side"].map(normalize_side)
    output["regime"] = None

    output["reported_realized_pnl_usdt"] = _numeric(closed, "close_profit_abs")
    output["realized_net_pnl_usdt"] = output["reported_realized_pnl_usdt"]
    pnl = output["realized_net_pnl_usdt"]
    positive = pd.Series(pd.NA, index=output.index, dtype="Int64")
    finite_mask = pnl.map(_is_finite_number)
    positive.loc[finite_mask] = pnl.loc[finite_mask].gt(0).astype(int)
    output["positive_net_outcome"] = positive

    stake = _numeric(closed, "stake_amount")
    output["return_on_stake"] = output["realized_net_pnl_usdt"] / stake.where(
        stake.ne(0)
    )
    output["normalized_net_return"] = _numeric(closed, "close_profit")
    output["holding_minutes"] = (
        output["label_available_at_utc"] - output["decision_timestamp_utc"]
    ).dt.total_seconds() / 60.0

    output["fee_open_cost"] = _numeric(closed, "fee_open_cost")
    output["fee_close_cost"] = _numeric(closed, "fee_close_cost")
    output["fee_total_cost"] = output[["fee_open_cost", "fee_close_cost"]].sum(
        axis=1, min_count=2
    )
    funding = _numeric(closed, "funding_fees")
    output["funding_revenue"] = pd.Series(
        np.where(funding.isna(), np.nan, np.maximum(funding, 0.0)),
        index=output.index,
        dtype=float,
    )
    output["funding_cost"] = pd.Series(
        np.where(funding.isna(), np.nan, np.maximum(-funding, 0.0)),
        index=output.index,
        dtype=float,
    )
    output["funding_net"] = funding
    output["estimated_slippage_cost"] = np.nan
    output["estimated_spread_cost"] = np.nan
    output["switching_cost_estimate"] = np.nan
    output["total_observed_cost"] = output["fee_total_cost"] + output["funding_cost"]
    output["total_estimated_cost"] = np.nan

    observed = output[
        ["fee_open_cost", "fee_close_cost", "funding_net"]
    ].notna().all(axis=1)
    output["cost_model_status"] = np.where(
        observed, "OBSERVED_NET_TARGET", "SOURCE_MISSING"
    )

    output["paper_source_hash"] = source_hash.lower()
    output["feature_source_hash"] = None
    output["qlib_source_hash"] = None
    output["regime_source_hash"] = None
    empty_timestamp = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["feature_timestamp_utc"] = empty_timestamp.copy()
    output["feature_available_at_utc"] = empty_timestamp.copy()
    output["score_generated_at_utc"] = empty_timestamp.copy()
    output["score_available_at_utc"] = empty_timestamp.copy()
    output["regime_generated_at_utc"] = empty_timestamp.copy()
    output["regime_available_at_utc"] = empty_timestamp.copy()
    output["qlib_score"] = np.nan
    output["prob_up"] = np.nan
    output["signal_confidence"] = np.nan
    output["model_version"] = None
    output["feature_source_row_identity"] = None
    return output


def _attach_features(
    base: pd.DataFrame,
    source: SourceFrame,
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    frame = base.copy()
    if source.frame is None or source.sha256 is None:
        return frame, [], {
            "status": source.status,
            "reason": source.reason,
            "linked_row_count": 0,
            "point_in_time_valid_count": 0,
            "feature_column_count": 0,
            "source_feature_columns": [],
            "excluded_post_trade_columns": [],
        }

    raw = source.frame.copy().reset_index(drop=True)
    safe_originals, excluded = _safe_numeric_feature_columns(raw)
    feature_columns = [f"feature__{column}" for column in safe_originals]
    for output_column in feature_columns:
        frame[output_column] = np.nan

    linked = 0
    domain = _shared_identity_domain(frame, raw)
    if domain is not None:
        rows = _indexed_rows(raw, domain)
        for index, row in frame.iterrows():
            key = _identity_value(row.get(domain), domain)
            if key is None:
                continue
            source_row = rows.get(key)
            if source_row is None:
                continue
            _copy_feature_row(
                frame,
                index,
                source_row,
                safe_originals,
                source.sha256,
            )
            linked += 1
    elif {"symbol"}.issubset(raw.columns) and _first_column(
        raw, FEATURE_TIMESTAMP_COLUMNS
    ):
        linked = _align_market_features(
            frame, raw, safe_originals, source.sha256
        )
    else:
        return frame, [], {
            "status": "SOURCE_UNVERIFIED",
            "reason": (
                "feature_source_missing_deterministic_identity_or_market_time_key"
            ),
            "linked_row_count": 0,
            "point_in_time_valid_count": 0,
            "feature_column_count": 0,
            "source_feature_columns": [],
            "excluded_post_trade_columns": sorted(excluded),
        }

    valid = _ordered_timestamp_mask(
        frame["feature_timestamp_utc"],
        frame["feature_available_at_utc"],
        frame["decision_timestamp_utc"],
    )
    return frame, feature_columns, {
        "status": "ok" if linked else "SOURCE_UNVERIFIED",
        "reason": (
            "point_in_time_features_linked"
            if linked
            else "feature_rows_unlinked"
        ),
        "identity_domain": domain,
        "linked_row_count": linked,
        "point_in_time_valid_count": int(valid.sum()),
        "feature_column_count": len(feature_columns),
        "source_feature_columns": safe_originals,
        "excluded_post_trade_columns": sorted(excluded),
        "institutional_feature_classifier_reused": True,
    }


def _align_market_features(
    output: pd.DataFrame,
    raw: pd.DataFrame,
    numeric_columns: list[str],
    source_hash: str,
) -> int:
    timestamp_column = _first_column(raw, FEATURE_TIMESTAMP_COLUMNS)
    if timestamp_column is None:
        return 0

    market = raw.copy()
    market["__symbol"] = market["symbol"].map(normalize_symbol)
    market["__timestamp"] = pd.to_datetime(
        market[timestamp_column], utc=True, errors="coerce"
    )
    explicit_available = _first_column(market, FEATURE_AVAILABLE_COLUMNS)
    if explicit_available:
        market["__available"] = pd.to_datetime(
            market[explicit_available], utc=True, errors="coerce"
        )
    elif "tf" in market.columns:
        duration = market["tf"].astype(str).map(TIMEFRAME_SECONDS)
        market["__available"] = market["__timestamp"] + pd.to_timedelta(
            duration, unit="s"
        )
    elif "timeframe" in market.columns:
        duration = market["timeframe"].astype(str).map(TIMEFRAME_SECONDS)
        market["__available"] = market["__timestamp"] + pd.to_timedelta(
            duration, unit="s"
        )
    else:
        return 0

    market = market.dropna(
        subset=["__timestamp", "__available", "__symbol"]
    )
    market = market.sort_values(
        ["__symbol", "__available", "__timestamp"], kind="mergesort"
    )
    grouped = {
        symbol: group.reset_index(drop=False)
        for symbol, group in market.groupby("__symbol", sort=False)
    }

    linked = 0
    for index, row in output.iterrows():
        candidates = grouped.get(str(row["symbol"]))
        if candidates is None or candidates.empty:
            continue
        position = (
            int(
                candidates["__available"].searchsorted(
                    row["decision_timestamp_utc"], side="right"
                )
            )
            - 1
        )
        if position < 0:
            continue
        selected = candidates.iloc[position]
        _copy_feature_row(
            output,
            index,
            selected,
            numeric_columns,
            source_hash,
            timestamp=selected["__timestamp"],
            available=selected["__available"],
        )
        if output.at[index, "regime"] is None:
            for column in REGIME_COLUMNS:
                if column in selected and pd.notna(selected[column]):
                    output.at[index, "regime"] = str(selected[column])
                    output.at[index, "regime_generated_at_utc"] = selected[
                        "__timestamp"
                    ]
                    output.at[index, "regime_available_at_utc"] = selected[
                        "__available"
                    ]
                    output.at[index, "regime_source_hash"] = source_hash.lower()
                    break
        linked += 1
    return linked


def _copy_feature_row(
    output: pd.DataFrame,
    index: int,
    source_row: pd.Series,
    numeric_columns: Iterable[str],
    source_hash: str,
    *,
    timestamp: Any | None = None,
    available: Any | None = None,
) -> None:
    timestamp_column = _first_column(source_row, FEATURE_TIMESTAMP_COLUMNS)
    available_column = _first_column(source_row, FEATURE_AVAILABLE_COLUMNS)
    timestamp_value = (
        timestamp
        if timestamp is not None
        else source_row.get(timestamp_column)
    )
    available_value = (
        available
        if available is not None
        else source_row.get(available_column)
    )
    output.at[index, "feature_timestamp_utc"] = pd.to_datetime(
        timestamp_value, utc=True, errors="coerce"
    )
    output.at[index, "feature_available_at_utc"] = pd.to_datetime(
        available_value, utc=True, errors="coerce"
    )
    output.at[index, "feature_source_hash"] = source_hash.lower()
    row_identity = (
        source_row.get("source_row_identity")
        or source_row.get("record_hash")
    )
    output.at[index, "feature_source_row_identity"] = str(
        row_identity if row_identity not in (None, "") else source_row.name
    )
    for column in numeric_columns:
        output.at[index, f"feature__{column}"] = pd.to_numeric(
            pd.Series([source_row.get(column)]), errors="coerce"
        ).iloc[0]


def _attach_qlib(
    base: pd.DataFrame,
    source: SourceFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = base.copy()
    if source.frame is None or source.sha256 is None:
        return output, {
            "status": source.status,
            "reason": source.reason,
            "linked_trade_count": 0,
            "point_in_time_valid_count": 0,
        }

    required = {
        "score_generated_at_utc",
        "score_available_at_utc",
        "model_version",
    }
    domain = _shared_identity_domain(output, source.frame)
    if domain is None or not required.issubset(source.frame.columns):
        return output, {
            "status": "SOURCE_UNVERIFIED",
            "reason": "qlib_identity_or_point_in_time_contract_missing",
            "identity_domain": None,
            "linked_trade_count": 0,
            "point_in_time_valid_count": 0,
        }

    rows = _indexed_rows(source.frame, domain)
    linked = 0
    valid = 0
    for index, base_row in output.iterrows():
        key = _identity_value(base_row.get(domain), domain)
        if key is None:
            continue
        source_row = rows.get(key)
        if source_row is None:
            continue

        linked += 1
        output.at[index, "score_generated_at_utc"] = pd.to_datetime(
            source_row.get("score_generated_at_utc"),
            utc=True,
            errors="coerce",
        )
        output.at[index, "score_available_at_utc"] = pd.to_datetime(
            source_row.get("score_available_at_utc"),
            utc=True,
            errors="coerce",
        )
        output.at[index, "model_version"] = source_row.get("model_version")
        output.at[index, "qlib_source_hash"] = source.sha256.lower()
        for column in ("qlib_score", "prob_up", "signal_confidence"):
            if column in source_row:
                output.at[index, column] = pd.to_numeric(
                    pd.Series([source_row.get(column)]), errors="coerce"
                ).iloc[0]

        if _ordered_timestamp_values(
            output.at[index, "score_generated_at_utc"],
            output.at[index, "score_available_at_utc"],
            output.at[index, "decision_timestamp_utc"],
        ):
            valid += 1
        else:
            output.at[index, "qlib_score"] = np.nan
            output.at[index, "prob_up"] = np.nan
            output.at[index, "signal_confidence"] = np.nan
            output.at[index, "model_version"] = None

    return output, {
        "status": (
            "ok"
            if valid == linked and linked
            else "LOOKAHEAD_BLOCKED"
            if linked
            else "QLIB_LINEAGE_UNVERIFIED"
        ),
        "reason": "qlib_point_in_time_linkage_evaluated",
        "identity_domain": domain,
        "linked_trade_count": linked,
        "point_in_time_valid_count": valid,
        "linkage_coverage_rate": linked / len(output) if len(output) else 0.0,
    }


def _attach_regime(
    base: pd.DataFrame,
    source: SourceFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = base.copy()
    inherited = int(output["regime"].notna().sum())

    if source.frame is None or source.sha256 is None:
        return output, {
            "status": "ok" if inherited else source.status,
            "reason": (
                "regime_inherited_from_feature_source"
                if inherited
                else source.reason
            ),
            "linked_trade_count": inherited,
            "point_in_time_valid_count": inherited,
        }

    domain = _shared_identity_domain(output, source.frame)
    regime_column = _first_column(source.frame, REGIME_COLUMNS)
    required = {"regime_generated_at_utc", "regime_available_at_utc"}
    if (
        domain is None
        or regime_column is None
        or not required.issubset(source.frame.columns)
    ):
        return output, {
            "status": "SOURCE_UNVERIFIED",
            "reason": "regime_identity_or_point_in_time_contract_missing",
            "identity_domain": None,
            "linked_trade_count": inherited,
            "point_in_time_valid_count": inherited,
        }

    rows = _indexed_rows(source.frame, domain)
    linked = inherited
    valid = inherited
    for index, base_row in output.iterrows():
        key = _identity_value(base_row.get(domain), domain)
        if key is None:
            continue
        source_row = rows.get(key)
        if source_row is None:
            continue

        generated = pd.to_datetime(
            source_row.get("regime_generated_at_utc"),
            utc=True,
            errors="coerce",
        )
        available = pd.to_datetime(
            source_row.get("regime_available_at_utc"),
            utc=True,
            errors="coerce",
        )
        linked += 1
        if _ordered_timestamp_values(
            generated,
            available,
            output.at[index, "decision_timestamp_utc"],
        ):
            output.at[index, "regime"] = source_row.get(regime_column)
            output.at[index, "regime_generated_at_utc"] = generated
            output.at[index, "regime_available_at_utc"] = available
            output.at[index, "regime_source_hash"] = source.sha256.lower()
            valid += 1
        else:
            output.at[index, "regime"] = None

    return output, {
        "status": (
            "ok"
            if valid == linked and linked
            else "LOOKAHEAD_BLOCKED"
            if linked
            else "SOURCE_UNVERIFIED"
        ),
        "reason": "regime_point_in_time_linkage_evaluated",
        "identity_domain": domain,
        "linked_trade_count": linked,
        "point_in_time_valid_count": valid,
        "linkage_coverage_rate": linked / len(output) if len(output) else 0.0,
    }


def _deterministic_linkage(
    base: pd.DataFrame,
    source: SourceFrame,
    prefix: str,
) -> dict[str, Any]:
    if source.frame is None:
        return {
            "trader_master_source_status": source.status,
            "trader_master_row_count": 0,
            "identity_domain": None,
            "linked_trade_count": 0,
            "linkage_coverage_rate": 0.0,
            "reason": source.reason,
        }

    domain = _shared_identity_domain(base, source.frame)
    if domain is None:
        return {
            "trader_master_source_status": f"{prefix}_UNLINKED",
            "trader_master_row_count": int(len(source.frame)),
            "identity_domain": None,
            "linked_trade_count": 0,
            "linkage_coverage_rate": 0.0,
            "reason": (
                "generic_id_rejected_and_deterministic_shared_identity_missing"
            ),
        }

    source_ids = set(_indexed_rows(source.frame, domain))
    base_ids = [
        _identity_value(value, domain)
        for value in base[domain].tolist()
        if _identity_value(value, domain) is not None
    ]
    linked = sum(value in source_ids for value in base_ids)
    return {
        "trader_master_source_status": (
            "ok" if linked else f"{prefix}_UNLINKED"
        ),
        "trader_master_row_count": int(len(source.frame)),
        "identity_domain": domain,
        "linked_trade_count": int(linked),
        "linkage_coverage_rate": linked / len(base) if len(base) else 0.0,
        "reason": "deterministic_same_domain_identity_only",
    }


def _attach_execution_cost(
    base: pd.DataFrame,
    source: SourceFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = base.copy()
    linked = 0
    domain = None
    if source.frame is not None:
        domain = _shared_identity_domain(output, source.frame)
        if domain is not None:
            rows = _indexed_rows(source.frame, domain)
            for index, base_row in output.iterrows():
                key = _identity_value(base_row.get(domain), domain)
                if key is None:
                    continue
                row = rows.get(key)
                if row is None:
                    continue
                linked += 1
                for column in (
                    "estimated_slippage_cost",
                    "estimated_spread_cost",
                    "switching_cost_estimate",
                ):
                    if column in row:
                        output.at[index, column] = pd.to_numeric(
                            pd.Series([row.get(column)]), errors="coerce"
                        ).iloc[0]

    output["total_estimated_cost"] = output[
        ["estimated_slippage_cost", "estimated_spread_cost"]
    ].sum(axis=1, min_count=1)
    observed = output[
        ["fee_open_cost", "fee_close_cost", "funding_net"]
    ].notna().all(axis=1)
    estimated = output[
        ["estimated_slippage_cost", "estimated_spread_cost"]
    ].notna().all(axis=1)

    return output, {
        "pnl_target_is_already_net": True,
        "fees_or_funding_subtracted_from_target_again": False,
        "funding_sign_convention": "SOURCE_POSITIVE_REVENUE_NEGATIVE_COST",
        "cost_coverage_rate": (
            float((observed | estimated).mean()) if len(output) else 0.0
        ),
        "observed_cost_coverage_rate": (
            float(observed.mean()) if len(output) else 0.0
        ),
        "estimated_cost_coverage_rate": (
            float(estimated.mean()) if len(output) else 0.0
        ),
        "cost_model_complete": bool(observed.all()) if len(output) else False,
        "execution_cost_source_status": source.status,
        "execution_cost_identity_domain": domain,
        "execution_cost_linked_trade_count": linked,
        "missing_spread_or_slippage_is_reported_not_zero": True,
    }


def _finalize_lineage(
    frame: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    output = frame.copy()
    errors: list[str] = []
    trainable: list[bool] = []

    for _, row in output.iterrows():
        row_errors: list[str] = []

        trade_id = _identity_value(row.get("trade_id"), "trade_id")
        if trade_id is None:
            row_errors.append("INVALID_TRADE_IDENTITY")

        decision = pd.to_datetime(
            row.get("decision_timestamp_utc"), utc=True, errors="coerce"
        )
        label_available = pd.to_datetime(
            row.get("label_available_at_utc"), utc=True, errors="coerce"
        )
        if pd.isna(decision):
            row_errors.append("LINEAGE_UNVERIFIED:decision_timestamp")

        if not (
            pd.notna(label_available)
            and pd.notna(decision)
            and label_available > decision
        ):
            row_errors.append("LINEAGE_UNVERIFIED:label_timestamp")

        if not _is_finite_number(row.get("realized_net_pnl_usdt")):
            row_errors.append("INVALID_FINANCIAL_LABEL")

        feature_timestamps_present = (
            pd.notna(row["feature_timestamp_utc"])
            and pd.notna(row["feature_available_at_utc"])
        )
        if (
            not feature_timestamps_present
            or not str(row.get("feature_source_hash") or "").strip()
        ):
            row_errors.append("LINEAGE_UNVERIFIED:feature")
        elif not _ordered_timestamp_values(
            row["feature_timestamp_utc"],
            row["feature_available_at_utc"],
            row["decision_timestamp_utc"],
        ):
            row_errors.append("LOOKAHEAD_BLOCKED:feature")

        has_feature = bool(feature_columns) and any(
            _is_finite_number(row.get(column)) for column in feature_columns
        )
        if not has_feature:
            row_errors.append("LINEAGE_UNVERIFIED:feature_values")

        errors.append(";".join(row_errors))
        trainable.append(not row_errors)

    output["lineage_errors"] = errors
    output["lineage_status"] = np.where(
        np.asarray(trainable), "VALID", "LINEAGE_UNVERIFIED"
    )
    output["trainable"] = trainable
    return output.sort_values(
        ["decision_timestamp_utc", "trade_id"], kind="mergesort"
    ).reset_index(drop=True)


def _feature_contract(
    frame: pd.DataFrame,
    feature_columns: list[str],
    feature_summary: Mapping[str, Any],
) -> dict[str, Any]:
    contract_frame = pd.DataFrame(index=frame.index)
    for column in sorted(feature_columns):
        if column in frame.columns:
            contract_frame[column] = frame[column]
    contract_frame["label_realized_net_pnl_usdt"] = frame.get(
        "realized_net_pnl_usdt"
    )
    contract_frame["label_positive_net_outcome"] = frame.get(
        "positive_net_outcome"
    )

    contract = build_institutional_feature_contract(
        contract_frame,
        source_datasets=["financial_ai_research_engine_v1"],
    )
    institutional_features = sorted(
        str(column) for column in contract.get("feature_columns", [])
    )
    expected_features = sorted(
        column for column in feature_columns if column in frame.columns
    )
    validation_errors = list(contract.get("validation_errors", []))
    if institutional_features != expected_features:
        validation_errors.append("institutional_feature_set_mismatch")

    result = dict(contract)
    result.update(
        {
            "feature_contract_hash": contract.get("contract_hash"),
            "valid": not validation_errors,
            "validation_errors": sorted(set(validation_errors)),
            "source_feature_columns": list(
                feature_summary.get("source_feature_columns", [])
            ),
            "excluded_post_trade_columns": list(
                feature_summary.get("excluded_post_trade_columns", [])
            ),
            "institutional_feature_contract_reused": True,
            "extended_post_trade_policy": sorted(POST_TRADE_OUTCOME_NAMES),
            "dataset_label_columns": [
                "realized_net_pnl_usdt",
                "positive_net_outcome",
            ],
        }
    )
    return result


def _read_optional_source(
    root: Path,
    name: str,
    value: str | Path | None,
) -> SourceFrame:
    if value is None:
        return SourceFrame(
            name,
            "SOURCE_MISSING",
            None,
            None,
            None,
            "source_not_requested",
        )

    path = _resolve_input(root, value)
    if not path.exists() or not path.is_file():
        return SourceFrame(
            name,
            "SOURCE_MISSING",
            str(path),
            None,
            None,
            "source_missing",
        )
    if path.is_symlink():
        return SourceFrame(
            name,
            "SOURCE_UNVERIFIED",
            str(path),
            None,
            None,
            "symlink_not_allowed",
        )
    try:
        frame = read_table(path)
        digest = file_sha256(path).lower()
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        return SourceFrame(
            name,
            "SOURCE_UNVERIFIED",
            str(path),
            None,
            None,
            type(exc).__name__,
        )
    return SourceFrame(name, "ok", str(path), digest, frame, "source_read")


def _resolve_input(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _safe_numeric_feature_columns(
    frame: pd.DataFrame,
) -> tuple[list[str], set[str]]:
    safe: list[str] = []
    excluded_post_trade: set[str] = set()

    for column in sorted(map(str, frame.columns)):
        lower = column.lower()
        role = classify_column(column)
        if lower in POST_TRADE_OUTCOME_NAMES:
            excluded_post_trade.add(column)
            continue
        if role in {"forbidden", "label", "outcome", "identifier"}:
            if role in {"forbidden", "label", "outcome"}:
                excluded_post_trade.add(column)
            continue
        if (
            lower in {item.lower() for item in LOCAL_METADATA_COLUMNS}
            or lower in {item.lower() for item in INSTITUTIONAL_METADATA_COLUMNS}
            or lower.endswith("_id")
        ):
            continue

        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().any():
            safe.append(column)

    return safe, excluded_post_trade


def _shared_identity_domain(
    base: pd.DataFrame,
    source: pd.DataFrame,
) -> str | None:
    for domain in IDENTITY_DOMAINS:
        if domain not in base.columns or domain not in source.columns:
            continue
        base_values = {
            value
            for item in base[domain].tolist()
            if (value := _identity_value(item, domain)) is not None
        }
        source_values = {
            value
            for item in source[domain].tolist()
            if (value := _identity_value(item, domain)) is not None
        }
        if base_values and source_values:
            return domain
    return None


def _indexed_rows(
    frame: pd.DataFrame,
    domain: str,
) -> dict[int | str, pd.Series]:
    if domain not in IDENTITY_DOMAINS or domain not in frame.columns:
        return {}
    output: dict[int | str, pd.Series] = {}
    duplicates: set[int | str] = set()

    for _, row in frame.iterrows():
        value = _identity_value(row.get(domain), domain)
        if value is None:
            continue
        if value in output:
            duplicates.add(value)
        output[value] = row

    for value in duplicates:
        output.pop(value, None)
    return output


def _identity_value(
    value: Any,
    domain: str,
) -> int | str | None:
    if value is None or value is pd.NA or isinstance(value, bool):
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None

    if domain == "trade_id":
        text = str(value).strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric) or not numeric.is_integer() or numeric < 0:
            return None
        return int(numeric)

    if domain in {"candidate_id", "order_id"}:
        text = str(value).strip()
        return text or None

    raise ValueError("unsupported_identity_domain")


def _first_column(
    frame_or_row: Any,
    columns: Iterable[str],
) -> str | None:
    available = set(
        frame_or_row.index
        if isinstance(frame_or_row, pd.Series)
        else frame_or_row.columns
    )
    return next((column for column in columns if column in available), None)


def _ordered_timestamp_mask(
    generated: pd.Series,
    available: pd.Series,
    decision: pd.Series,
) -> pd.Series:
    return (
        generated.notna()
        & available.notna()
        & decision.notna()
        & generated.le(available)
        & available.le(decision)
    )


def _ordered_timestamp_values(
    generated: Any,
    available: Any,
    decision: Any,
) -> bool:
    values = [
        pd.to_datetime(value, utc=True, errors="coerce")
        for value in (generated, available, decision)
    ]
    return bool(
        all(pd.notna(value) for value in values)
        and values[0] <= values[1] <= values[2]
    )


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _counts(series: pd.Series) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in series.fillna("SOURCE_MISSING")
        .value_counts()
        .sort_index()
        .items()
    }


def _financial_label_valid_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    pnl_valid = frame["realized_net_pnl_usdt"].map(_is_finite_number)
    trade_valid = frame["trade_id"].map(
        lambda value: _identity_value(value, "trade_id") is not None
    )
    decision_valid = pd.to_datetime(
        frame["decision_timestamp_utc"], utc=True, errors="coerce"
    ).notna()
    label_time = pd.to_datetime(
        frame["label_available_at_utc"], utc=True, errors="coerce"
    )
    decision_time = pd.to_datetime(
        frame["decision_timestamp_utc"], utc=True, errors="coerce"
    )
    temporal_valid = label_time.notna() & decision_time.notna() & label_time.gt(
        decision_time
    )
    return pnl_valid & trade_valid & decision_valid & temporal_valid


def _dataset_hash(
    frame: pd.DataFrame,
    feature_columns: list[str],
) -> str | None:
    if frame.empty:
        return None
    columns = [
        column
        for column in (
            "row_id",
            "trade_id",
            "estimate_subject_id",
            "decision_timestamp_utc",
            "label_available_at_utc",
            "symbol",
            "side",
            "regime",
            "realized_net_pnl_usdt",
            "positive_net_outcome",
            *sorted(feature_columns),
        )
        if column in frame.columns
    ]
    return str(frame_hash(frame[columns].copy()))


def _is_finite_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed)


def _missing_paper_report(
    sources: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    empty_contract = _feature_contract(
        pd.DataFrame(
            {
                "realized_net_pnl_usdt": pd.Series(dtype=float),
                "positive_net_outcome": pd.Series(dtype="Int64"),
            }
        ),
        [],
        {
            "source_feature_columns": [],
            "excluded_post_trade_columns": [],
        },
    )
    return {
        "status": "BLOCKED",
        "reason": reason,
        "sources": sources,
        "dataset": {
            "total_rows": 0,
            "lineage_valid_rows": 0,
            "trainable_rows": 0,
            "excluded_rows": 0,
            "positive_count": 0,
            "negative_count": 0,
            "valid_financial_label_count": 0,
            "invalid_financial_label_count": 0,
            "financial_label_valid": False,
            "dataset_hash": None,
        },
        "lineage": {},
        "feature_contract": empty_contract,
        "targets": {
            "financial_label_valid": False,
            "valid_financial_label_count": 0,
            "invalid_financial_label_count": 0,
        },
        "cost_model": {"cost_model_complete": False},
        "trader_master_comparison": {},
    }
