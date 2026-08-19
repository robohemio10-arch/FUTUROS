"""Pure, deterministic Shadow Opportunity Engine V1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from smartcrypto.analysis.paper_financial_performance import read_table
from smartcrypto.research.paper_edge_foundation.foundation import (
    file_sha256,
    prepare_closed_trades,
    read_authoritative_paper_source,
)

from .contracts import (
    CandidateObservation,
    MarketEvidence,
    PositionSnapshot,
    epoch_seconds,
    finite_float,
    normalize_side,
    normalize_symbol,
    stable_id,
    utc_iso,
    valid_sha256,
)
from .exit_efficiency import analyze_exit_efficiency


SCHEMA_VERSION = "shadow_opportunity_engine_v1"
DEFAULT_REPORT = Path("data/reports/shadow_opportunity_engine_v1.json")
DEFAULT_LEDGER = Path("data/reports/shadow_opportunity_ledger_v1.jsonl")
FINANCIAL_EV_COLUMNS = ("expected_return_net", "candidate_ev", "financial_expected_value")
ORDINAL_SCORE_COLUMNS = ("ranking_score", "qlib_score", "prob_up", "signal_confidence")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only": True,
    "operational_authority": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "writes_active_signals": False,
    "writes_active_model": False,
    "writes_active_registry": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_max_open_trades": False,
    "sends_orders": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "replacement_authorized": False,
    "replacement_executed": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None
    normalized = str(value).strip()
    return normalized or None


class ShadowOpportunityEngine:
    """In-memory event processor with no operational adapters or writers."""

    def __init__(
        self,
        *,
        positions: Sequence[PositionSnapshot] = (),
        shadow_capacity_limit: int | None = None,
    ) -> None:
        self._positions = tuple(positions)
        self._capacity_limit = shadow_capacity_limit
        self._candidates: dict[str, CandidateObservation] = {}
        self._ledger: dict[str, dict[str, Any]] = {}
        self._score_history: dict[
            tuple[str, str, str | None, str | None], list[tuple[str, float]]
        ] = {}

    def process_market_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        candidate = build_candidate(event)
        duplicate_event = candidate.candidate_id in self._candidates
        if not duplicate_event:
            self._candidates[candidate.candidate_id] = candidate
        if not duplicate_event and candidate.ranking_score is not None:
            key = self._alpha_history_key(candidate)
            self._score_history.setdefault(key, []).append(
                (candidate.observed_at_utc, candidate.ranking_score)
            )
        decision = self._decision_for(candidate, event)
        decision = {**decision, "duplicate_event": duplicate_event}
        if not duplicate_event:
            self._ledger[decision["ledger_id"]] = decision
        return {
            "candidate": candidate.to_dict(),
            "decision": decision,
            "alpha_decay": self._alpha_decay(candidate),
            "duplicate_event": duplicate_event,
        }

    def process_events(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        processed = [self.process_market_event(event) for event in events]
        return {
            "processed_event_count": len(processed),
            "events": processed,
            "snapshot": self.snapshot(),
        }

    def snapshot(self) -> dict[str, Any]:
        candidates = [candidate.to_dict() for candidate in self._candidates.values()]
        ranking_mode = (
            "FINANCIAL_EV"
            if candidates and all(row["candidate_ev"] is not None for row in candidates)
            else "NON_FINANCIAL_ORDINAL"
        )
        ranking_field = "candidate_ev" if ranking_mode == "FINANCIAL_EV" else "ranking_score"
        ordered = sorted(
            candidates,
            key=lambda row: (
                row[ranking_field] is None,
                -(float(row[ranking_field]) if row[ranking_field] is not None else 0.0),
                row["candidate_id"],
            ),
        )
        ledger = sorted(self._ledger.values(), key=lambda row: row["ledger_id"])
        blocked = [
            row
            for row in ledger
            if row["missed_due_to_pair_occupancy"] or row["missed_due_to_global_capacity"]
        ]
        ev_count = sum(row["candidate_ev"] is not None for row in candidates)
        replacement_evaluable = sum(
            row["replacement_status"] == "EVALUABLE" for row in ledger
        )
        symbols = sorted(
            {position.symbol for position in self._positions}
            | {candidate.symbol for candidate in self._candidates.values()}
        )
        return {
            "current_positions": [position.to_dict() for position in self._positions],
            "opportunity_book": {
                "ranking_mode": ranking_mode,
                "ranking_score_semantics": (
                    None if ranking_mode == "FINANCIAL_EV" else "NON_FINANCIAL_ORDINAL"
                ),
                "current_positions": [position.to_dict() for position in self._positions],
                "new_candidates": ordered,
            },
            "opportunity_cost": {
                "candidate_count": len(candidates),
                "occupied_candidate_count": len(blocked),
                "capacity_blocked_candidate_count": len(blocked),
                "missed_opportunity_count": len(blocked),
                "capital_hours_total": float(sum(item.capital_hours for item in self._positions)),
                "candidate_ev_coverage_rate": float(ev_count / len(candidates)) if candidates else 0.0,
                "replacement_evaluable_count": replacement_evaluable,
                "replacement_not_evaluable_count": len(ledger) - replacement_evaluable,
                "opportunity_cost_pnl": None,
                "opportunity_cost_status": "INSUFFICIENT_EV_EVIDENCE",
                "ledger": ledger,
            },
            "alpha_decay": {
                "candidate_count": len(candidates),
                "rows": [
                    self._alpha_decay(candidate)
                    for candidate in self._candidates.values()
                ],
            },
            "replacement_research": {
                "replacement_evaluable_count": replacement_evaluable,
                "replacement_authorized": False,
                "replacement_executed": False,
            },
            "multiasset": {
                "symbols_observed": symbols,
                "symbol_count": len(symbols),
                "multiasset_shadow_ready": len(symbols) >= 3,
                "freqtrade_whitelist_changed": False,
            },
            "ledger_entries": ledger,
        }

    def _decision_for(
        self,
        candidate: CandidateObservation,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        pair_positions = [position for position in self._positions if position.symbol == candidate.symbol]
        pair_occupied = bool(pair_positions)
        global_capacity = bool(
            self._capacity_limit is not None
            and self._capacity_limit >= 0
            and len(self._positions) >= self._capacity_limit
        )
        blocker = pair_positions[0] if pair_positions else (self._positions[0] if global_capacity and self._positions else None)
        remaining_ev = _validated_financial_value(
            event,
            value_columns=("position_remaining_ev", "remaining_position_ev"),
            semantics_field="remaining_position_ev_semantics",
            generated_field="remaining_position_ev_generated_at_utc",
            source_hash_field="remaining_position_ev_source_hash",
            observed_at_utc=candidate.observed_at_utc,
        ) if blocker is not None else None
        switching_cost = finite_float(event.get("switching_cost_estimate"))
        switching_status = str(event.get("switching_cost_status") or "INCOMPLETE").upper()
        if (
            candidate.candidate_ev is not None
            and remaining_ev is not None
            and switching_cost is not None
            and switching_status == "COMPLETE"
        ):
            replacement_delta = candidate.candidate_ev - remaining_ev - switching_cost
            replacement_status = "EVALUABLE"
        else:
            replacement_delta = None
            replacement_status = "INSUFFICIENT_EV_EVIDENCE"
        blocked = pair_occupied or global_capacity
        actionable = candidate.candidate_actionable_shadow
        reason = (
            "CANDIDATE_LINEAGE_BLOCKED"
            if not actionable
            else "PAIR_OCCUPIED"
            if pair_occupied
            else "GLOBAL_CAPACITY_OCCUPIED"
            if global_capacity
            else "CAPACITY_AVAILABLE"
        )
        ledger_payload = {
            "observed_at_utc": candidate.observed_at_utc,
            "position_trade_id": blocker.trade_id if blocker else None,
            "candidate_id": candidate.candidate_id,
            "reason": reason,
        }
        return {
            "ledger_id": stable_id("ledger", ledger_payload),
            "observed_at_utc": candidate.observed_at_utc,
            "position_trade_id": blocker.trade_id if blocker else None,
            "position_symbol": blocker.symbol if blocker else None,
            "position_side": blocker.side if blocker else None,
            "candidate_id": candidate.candidate_id,
            "candidate_symbol": candidate.symbol,
            "candidate_side": candidate.side,
            "candidate_integrity_valid": candidate.candidate_integrity_valid,
            "candidate_actionable_shadow": actionable,
            "position_age_seconds": blocker.position_age_seconds if blocker else None,
            "capital_hours": blocker.capital_hours if blocker else 0.0,
            "candidate_ranking_score": candidate.ranking_score,
            "candidate_ev": candidate.candidate_ev,
            "position_remaining_ev": remaining_ev,
            "switching_cost_estimate": switching_cost,
            "switching_cost_status": switching_status,
            "replacement_delta": replacement_delta,
            "replacement_status": replacement_status,
            "would_replace": bool(replacement_delta is not None and replacement_delta > 0),
            "replacement_authorized": False,
            "replacement_executed": False,
            "pair_occupied": pair_occupied,
            "global_capacity_occupied": global_capacity,
            "missed_due_to_pair_occupancy": bool(pair_occupied and actionable),
            "missed_due_to_global_capacity": bool(global_capacity and actionable),
            "would_enter_if_capacity_available": actionable,
            "missed_ev_status": (
                "EVALUABLE" if candidate.candidate_ev is not None else "UNKNOWN"
            ),
            "reason": reason,
            "capacity_blocked": bool(blocked and actionable),
        }

    def _alpha_decay(self, candidate: CandidateObservation) -> dict[str, Any]:
        history = self._score_history.get(self._alpha_history_key(candidate), [])
        if len(history) < 2 or candidate.ranking_score is None:
            return {
                "candidate_id": candidate.candidate_id,
                "alpha_decay_status": "INSUFFICIENT_HISTORY",
                "alpha_age_seconds": None,
                "initial_ranking_score": None,
                "current_ranking_score": candidate.ranking_score,
                "score_decay_absolute": None,
                "score_decay_ratio": None,
                "alpha_decay_score": None,
                "alpha_decay_score_semantics": "NON_FINANCIAL_ORDINAL",
            }
        initial_time, initial_score = history[0]
        current_epoch = epoch_seconds(candidate.observed_at_utc)
        initial_epoch = epoch_seconds(initial_time)
        if current_epoch is None or initial_epoch is None:
            return {
                "candidate_id": candidate.candidate_id,
                "alpha_decay_status": "INVALID_TIMESTAMP_LINEAGE",
                "alpha_age_seconds": None,
                "initial_ranking_score": initial_score,
                "current_ranking_score": candidate.ranking_score,
                "score_decay_absolute": None,
                "score_decay_ratio": None,
                "alpha_decay_score": None,
                "alpha_decay_score_semantics": "NON_FINANCIAL_ORDINAL",
            }
        age = float(current_epoch - initial_epoch)
        absolute = initial_score - candidate.ranking_score
        ratio = candidate.ranking_score / initial_score if initial_score != 0 else None
        return {
            "candidate_id": candidate.candidate_id,
            "alpha_decay_status": "OK",
            "alpha_age_seconds": age,
            "initial_ranking_score": initial_score,
            "current_ranking_score": candidate.ranking_score,
            "score_decay_absolute": absolute,
            "score_decay_ratio": ratio,
            "alpha_decay_score": ratio,
            "alpha_decay_score_semantics": "CURRENT_TO_INITIAL_NON_FINANCIAL_ORDINAL_RATIO",
        }

    @staticmethod
    def _alpha_history_key(
        candidate: CandidateObservation,
    ) -> tuple[str, str, str | None, str | None]:
        return (
            candidate.symbol,
            candidate.side,
            candidate.model_version,
            candidate.ranking_score_source_field,
        )


def build_candidate(event: Mapping[str, Any]) -> CandidateObservation:
    observed = utc_iso(event.get("observed_at_utc"))
    symbol = normalize_symbol(event.get("symbol") or event.get("pair"))
    side = normalize_side(event.get("side"))
    source_hash = str(event.get("source_hash") or "")
    source_row_identity = str(event.get("source_row_identity") or "")
    integrity_errors: list[str] = []
    if observed is None:
        integrity_errors.append("candidate_observed_at_missing_or_invalid")
    if not symbol:
        integrity_errors.append("candidate_symbol_missing")
    if side is None:
        integrity_errors.append("candidate_side_invalid")
    if not valid_sha256(source_hash):
        integrity_errors.append("candidate_source_hash_missing_or_invalid")
    if not source_row_identity:
        integrity_errors.append("candidate_source_row_identity_missing")
    candidate_integrity_valid = not integrity_errors
    errors = list(integrity_errors)
    market_evidence = _market_evidence_from_event(event, observed)
    market_lineage = [item.lineage() for item in market_evidence]
    market_valid = bool(market_lineage) and all(item["valid"] for item in market_lineage)
    errors.extend(
        error
        for lineage in market_lineage
        for error in lineage["errors"]
    )
    if not market_evidence:
        errors.append("market_evidence_missing")

    ordinal_scores = {
        column: finite_float(event.get(column)) for column in ORDINAL_SCORE_COLUMNS
    }
    ranking_score_source_field = next(
        (column for column in ORDINAL_SCORE_COLUMNS if ordinal_scores[column] is not None),
        None,
    )
    ranking_score = (
        ordinal_scores[ranking_score_source_field]
        if ranking_score_source_field is not None
        else None
    )
    score_present = ranking_score_source_field is not None
    score_generated = utc_iso(event.get("score_generated_at_utc"))
    score_available = utc_iso(event.get("score_available_at_utc"))
    score_hash = str(event.get("score_source_hash") or source_hash)
    model_version = str(event.get("model_version") or "").strip() or None
    observed_epoch = epoch_seconds(observed)
    score_generated_epoch = epoch_seconds(score_generated)
    score_available_epoch = epoch_seconds(score_available)
    score_valid = bool(
        score_present
        and ranking_score is not None
        and observed_epoch is not None
        and score_generated_epoch is not None
        and score_available_epoch is not None
        and score_generated_epoch <= score_available_epoch <= observed_epoch
        and valid_sha256(score_hash)
        and source_row_identity
        and model_version
    )
    if not score_present:
        errors.append("score_lineage_missing")
    elif not score_valid:
        errors.append("score_lineage_invalid")

    explicit_regime = _optional_text(
        event.get("entry_market_regime") or event.get("market_regime")
    )
    market_regime_evidence = next(
        (
            item
            for timeframe in ("5m", "1m", "15s")
            for item in market_evidence
            if item.timeframe == timeframe and item.market_regime
        ),
        None,
    )
    regime = explicit_regime or (
        market_regime_evidence.market_regime if market_regime_evidence else None
    )
    if explicit_regime:
        regime_generated = utc_iso(event.get("regime_generated_at_utc"))
        regime_available = utc_iso(event.get("regime_available_at_utc"))
        regime_hash = str(event.get("regime_source_hash") or "") or None
        regime_timeframe = _optional_text(event.get("regime_source_timeframe"))
        regime_method = _optional_text(event.get("regime_method"))
        regime_lookback = _optional_text(event.get("regime_lookback"))
    elif market_regime_evidence:
        regime_generated = market_regime_evidence.generated_at_utc
        regime_available = market_regime_evidence.available_at_utc
        regime_hash = market_regime_evidence.source_hash
        regime_timeframe = market_regime_evidence.timeframe
        regime_method = market_regime_evidence.regime_method
        regime_lookback = market_regime_evidence.regime_lookback
    else:
        regime_generated = None
        regime_available = None
        regime_hash = None
        regime_timeframe = None
        regime_method = None
        regime_lookback = None
    regime_generated_epoch = epoch_seconds(regime_generated)
    regime_available_epoch = epoch_seconds(regime_available)
    regime_valid = bool(
        regime
        and observed_epoch is not None
        and regime_generated_epoch is not None
        and regime_available_epoch is not None
        and regime_generated_epoch <= regime_available_epoch <= observed_epoch
        and valid_sha256(regime_hash)
    )
    if not regime:
        errors.append("regime_lineage_missing")
    elif not regime_valid:
        errors.append("regime_lineage_invalid")

    candidate_actionable_shadow = bool(
        candidate_integrity_valid and market_valid and score_valid and regime_valid
    )

    candidate_ev = _validated_financial_value(
        event,
        value_columns=FINANCIAL_EV_COLUMNS,
        semantics_field="financial_ev_semantics",
        generated_field="financial_ev_generated_at_utc",
        source_hash_field="financial_ev_source_hash",
        observed_at_utc=observed,
    )
    candidate_payload = {
        "observed_at_utc": observed,
        "symbol": symbol,
        "side": side,
        "source_hash": source_hash,
        "source_row_identity": source_row_identity,
    }
    return CandidateObservation(
        candidate_id=stable_id("candidate", candidate_payload),
        observed_at_utc=observed or "INVALID",
        symbol=symbol,
        side=side or "INVALID",
        source_hash=source_hash,
        source_row_identity=source_row_identity,
        candidate_integrity_valid=candidate_integrity_valid,
        lineage_status="VALID" if candidate_actionable_shadow else "BLOCKED",
        candidate_actionable_shadow=candidate_actionable_shadow,
        market_lineage_valid=market_valid,
        score_lineage_valid=score_valid,
        regime_lineage_valid=regime_valid,
        ranking_score=ranking_score,
        ranking_score_source_field=ranking_score_source_field,
        prob_up=ordinal_scores["prob_up"],
        qlib_score=ordinal_scores["qlib_score"],
        signal_confidence=ordinal_scores["signal_confidence"],
        candidate_ev=candidate_ev,
        candidate_ev_status="AVAILABLE" if candidate_ev is not None else "SOURCE_MISSING",
        model_version=model_version,
        score_generated_at_utc=score_generated,
        score_available_at_utc=score_available,
        engine_observed_at_utc=observed or "INVALID",
        regime=regime,
        regime_method=regime_method,
        regime_lookback=regime_lookback,
        regime_generated_at_utc=regime_generated,
        regime_available_at_utc=regime_available,
        regime_source_hash=regime_hash,
        regime_source_timeframe=regime_timeframe,
        lineage_errors=tuple(sorted(set(errors))),
    )


def _validated_financial_value(
    event: Mapping[str, Any],
    *,
    value_columns: Sequence[str],
    semantics_field: str,
    generated_field: str,
    source_hash_field: str,
    observed_at_utc: str | None,
) -> float | None:
    value = next(
        (
            parsed
            for column in value_columns
            if (parsed := finite_float(event.get(column))) is not None
        ),
        None,
    )
    observed_epoch = epoch_seconds(observed_at_utc)
    generated_epoch = epoch_seconds(event.get(generated_field))
    if not (
        value is not None
        and str(event.get(semantics_field) or "").upper() == "EXPECTED_NET_PNL_USDT"
        and observed_epoch is not None
        and generated_epoch is not None
        and generated_epoch <= observed_epoch
        and valid_sha256(event.get(source_hash_field))
    ):
        return None
    return value


def _market_evidence_from_event(
    event: Mapping[str, Any],
    observed: str | None,
) -> list[MarketEvidence]:
    payload = event.get("market_evidence")
    rows: list[Mapping[str, Any]]
    if isinstance(payload, Mapping):
        rows = [
            {**dict(value), "timeframe": timeframe}
            for timeframe, value in payload.items()
            if isinstance(value, Mapping)
        ]
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        rows = [value for value in payload if isinstance(value, Mapping)]
    elif event.get("timeframe"):
        rows = [event]
    else:
        rows = []
    output: list[MarketEvidence] = []
    for row in rows:
        output.append(
            MarketEvidence(
                symbol=normalize_symbol(row.get("symbol") or event.get("symbol")),
                timeframe=str(row.get("timeframe") or ""),
                candle_timestamp_utc=utc_iso(row.get("candle_timestamp_utc") or row.get("timestamp")),
                available_at_utc=utc_iso(row.get("available_at_utc")),
                generated_at_utc=utc_iso(row.get("generated_at_utc")),
                observed_at_utc=observed,
                source_hash=str(row.get("source_hash") or event.get("source_hash") or "") or None,
                source_row_identity=str(row.get("source_row_identity") or event.get("source_row_identity") or "") or None,
                open=finite_float(row.get("open")),
                high=finite_float(row.get("high")),
                low=finite_float(row.get("low")),
                close=finite_float(row.get("close")),
                market_regime=_optional_text(row.get("market_regime")),
                regime_method=_optional_text(row.get("regime_method")),
                regime_lookback=_optional_text(row.get("regime_lookback")),
            )
        )
    return output


def load_positions_readonly(
    trades: pd.DataFrame,
    evaluated_at_utc: str,
) -> tuple[list[PositionSnapshot], list[str]]:
    evaluated_epoch = epoch_seconds(evaluated_at_utc)
    if evaluated_epoch is None:
        raise ValueError("evaluated_at_utc_invalid")
    positions: list[PositionSnapshot] = []
    warnings: list[str] = []
    for row in trades.loc[pd.to_numeric(trades["is_open"], errors="coerce").eq(1)].itertuples(index=False):
        opened = utc_iso(row.open_date)
        opened_epoch = epoch_seconds(opened)
        side = "SHORT" if finite_float(row.is_short) == 1.0 else "LONG" if finite_float(row.is_short) == 0.0 else None
        stake = finite_float(row.stake_amount)
        leverage = finite_float(row.leverage)
        open_rate = finite_float(row.open_rate)
        if (
            opened_epoch is None
            or opened_epoch > evaluated_epoch
            or side is None
            or stake is None
            or stake < 0
            or leverage is None
            or leverage <= 0
            or open_rate is None
            or open_rate <= 0
        ):
            warnings.append(f"open_position_invalid:{int(row.id)}")
            continue
        age = evaluated_epoch - opened_epoch
        positions.append(
            PositionSnapshot(
                trade_id=int(row.id),
                pair=str(row.pair),
                symbol=normalize_symbol(row.pair),
                side=side,
                open_date=opened or "INVALID",
                stake_amount=stake,
                leverage=leverage,
                open_rate=open_rate,
                max_rate=finite_float(row.max_rate),
                min_rate=finite_float(row.min_rate),
                position_age_seconds=age,
                capital_locked_usdt=stake,
                capital_hours=stake * age / 3600.0,
                estimated_notional_usdt=stake * leverage,
            )
        )
    return positions, warnings


def inspect_market_source(
    path: str | Path | None,
    timeframe: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if path is None:
        return _missing_market_source(timeframe), pd.DataFrame()
    source = Path(path).resolve()
    if not source.exists() or source.is_symlink():
        return _missing_market_source(timeframe, str(source)), pd.DataFrame()
    files = sorted(source.rglob("*.parquet")) if source.is_dir() else [source]
    files = [file for file in files if file.is_file() and not file.is_symlink()]
    if not files:
        return _missing_market_source(timeframe, str(source)), pd.DataFrame()
    digest = hashlib.sha256()
    frames: list[pd.DataFrame] = []
    for file in files:
        file_hash = file_sha256(file)
        digest.update(file.relative_to(source).as_posix().encode("utf-8") if source.is_dir() else file.name.encode("utf-8"))
        digest.update(file_hash.encode("ascii"))
        try:
            frame = pd.read_parquet(file)
        except (OSError, ValueError, ImportError):
            continue
        tf_column = "tf" if "tf" in frame.columns else "timeframe" if "timeframe" in frame.columns else None
        if tf_column:
            frame = frame.loc[frame[tf_column].astype(str).eq(timeframe)]
        if frame.empty:
            continue
        timestamp_column = "timestamp" if "timestamp" in frame.columns else "ts" if "ts" in frame.columns else None
        required_market_columns = {"symbol", "open", "high", "low", "close"}
        if timestamp_column is None or not required_market_columns.issubset(frame.columns):
            continue
        generated_column = "generated_at_utc" if "generated_at_utc" in frame.columns else None
        selected = pd.DataFrame(
            {
                "symbol": frame["symbol"].map(normalize_symbol),
                "timestamp": pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce"),
                "open": pd.to_numeric(frame.get("open"), errors="coerce"),
                "high": pd.to_numeric(frame.get("high"), errors="coerce"),
                "low": pd.to_numeric(frame.get("low"), errors="coerce"),
                "close": pd.to_numeric(frame.get("close"), errors="coerce"),
                "generated_at_utc": (
                    pd.to_datetime(frame[generated_column], utc=True, errors="coerce")
                    if generated_column
                    else pd.NaT
                ),
                "market_regime": frame.get("market_regime"),
                "regime_method": frame.get("regime_method"),
                "regime_lookback": frame.get("regime_lookback"),
            }
        )
        selected["timeframe"] = timeframe
        frames.append(selected)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    source_hash = digest.hexdigest().upper()
    if combined.empty:
        return {
            **_missing_market_source(timeframe, str(source)),
            "source_hash": source_hash,
            "file_count": len(files),
            "status": "unusable",
        }, combined
    combined = combined.dropna(
        subset=["symbol", "timestamp", "open", "high", "low", "close"]
    )
    combined = combined.sort_values(
        ["symbol", "timestamp"], kind="mergesort"
    ).reset_index(drop=True)
    symbols = sorted(set(combined["symbol"]))
    return {
        "status": "available",
        "path": str(source),
        "timeframe": timeframe,
        "source_hash": source_hash,
        "file_count": len(files),
        "row_count": int(len(combined)),
        "symbols": symbols,
        "min_timestamp_utc": combined["timestamp"].min().isoformat(),
        "max_timestamp_utc": combined["timestamp"].max().isoformat(),
        "generated_at_available": bool(combined["generated_at_utc"].notna().all()),
    }, combined


def _missing_market_source(timeframe: str, path: str | None = None) -> dict[str, Any]:
    return {
        "status": "missing",
        "path": path,
        "timeframe": timeframe,
        "source_hash": None,
        "file_count": 0,
        "row_count": 0,
        "symbols": [],
        "min_timestamp_utc": None,
        "max_timestamp_utc": None,
        "generated_at_available": False,
    }


def load_candidate_events(
    path: str | Path | None,
    *,
    symbols: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path is None:
        return {"status": "missing", "path": None, "row_count": 0, "source_hash": None}, []
    source = Path(path).resolve()
    if not source.exists() or not source.is_file() or source.is_symlink():
        return {"status": "missing", "path": str(source), "row_count": 0, "source_hash": None}, []
    source_hash = file_sha256(source)
    try:
        frame = read_table(source)
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        return {
            "status": "unusable",
            "path": str(source),
            "row_count": 0,
            "source_hash": source_hash,
            "reason": f"candidate_source_read_failed:{type(exc).__name__}",
        }, []
    events: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        payload = row.dropna().to_dict()
        symbol = normalize_symbol(payload.get("symbol") or payload.get("pair"))
        if symbols and symbol not in symbols:
            continue
        payload["symbol"] = symbol
        payload.setdefault("source_hash", source_hash)
        payload.setdefault("source_row_identity", stable_id("row", {"source_hash": source_hash, "row": int(index)}))
        events.append(payload)
    return {
        "status": "available",
        "path": str(source),
        "row_count": len(events),
        "source_hash": source_hash,
    }, events


def build_shadow_opportunity_engine_v1(
    *,
    project_root: str | Path,
    paper_db: str | Path,
    evaluated_at_utc: str,
    candidate_source: str | Path | None = None,
    market_data_15s: str | Path | None = None,
    market_data_1m: str | Path | None = None,
    market_data_5m: str | Path | None = None,
    symbols: Sequence[str] | None = None,
    shadow_capacity_limit: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    evaluated_at = utc_iso(evaluated_at_utc)
    if evaluated_at is None:
        raise ValueError("evaluated_at_utc_invalid")
    source = read_authoritative_paper_source(paper_db)
    positions, position_warnings = load_positions_readonly(source["trades"], evaluated_at)
    closed, _counts = prepare_closed_trades(source["trades"])
    selected_symbols = {normalize_symbol(value) for value in symbols or () if normalize_symbol(value)}
    market_sources: dict[str, Any] = {}
    candle_frames: dict[str, pd.DataFrame] = {}
    for timeframe, path in (
        ("15s", market_data_15s),
        ("1m", market_data_1m),
        ("5m", market_data_5m),
    ):
        descriptor, frame = inspect_market_source(path, timeframe)
        market_sources[timeframe] = descriptor
        if selected_symbols and not frame.empty:
            frame = frame.loc[frame["symbol"].isin(selected_symbols)]
        candle_frames[timeframe] = frame
    candidate_descriptor, events = load_candidate_events(
        candidate_source,
        symbols=selected_symbols or None,
    )
    events = [_attach_market_context(event, candle_frames, market_sources) for event in events]
    engine = ShadowOpportunityEngine(
        positions=positions,
        shadow_capacity_limit=shadow_capacity_limit,
    )
    processed = engine.process_events(events)
    snapshot = processed["snapshot"]
    exit_efficiency = analyze_exit_efficiency(closed, candle_frames)
    candidates = snapshot["opportunity_book"]["new_candidates"]
    valid_lineage = sum(row["candidate_actionable_shadow"] for row in candidates)
    gates = {
        "market_data_15s_available": market_sources["15s"]["status"] == "available",
        "market_data_1m_available": market_sources["1m"]["status"] == "available",
        "market_data_5m_available": market_sources["5m"]["status"] == "available",
        "score_lineage_valid": bool(candidates) and all(row["score_lineage_valid"] for row in candidates),
        "regime_lineage_valid": bool(candidates) and all(row["regime_lineage_valid"] for row in candidates),
        "position_source_valid": not position_warnings,
        "candidate_ev_available": bool(candidates) and all(row["candidate_ev"] is not None for row in candidates),
        "remaining_ev_available": bool(snapshot["opportunity_cost"]["ledger"]) and all(
            row["position_remaining_ev"] is not None
            for row in snapshot["opportunity_cost"]["ledger"]
        ),
        "replacement_evaluable": snapshot["opportunity_cost"]["replacement_evaluable_count"] > 0,
        "exit_efficiency_path_coverage_sufficient": exit_efficiency[
            "exit_efficiency_path_coverage_sufficient"
        ],
        "multiasset_shadow_ready": snapshot["multiasset"]["multiasset_shadow_ready"],
        "shadow_opportunity_engine_ready": "partial",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": evaluated_at,
        "status": "ok",
        "reason": "shadow_observation_completed_no_operational_authority",
        "sources": {
            "paper_db": {
                "path": str(source["path"]),
                "sha256": source["sha256_before"],
                "hash_invariant": source["sha256_before"] == source["sha256_after"],
                "sqlite_integrity_check": source["sqlite_integrity_check"],
            },
            "candidate_source": candidate_descriptor,
            "market_data": market_sources,
        },
        "lineage": {
            "candidate_count": len(candidates),
            "valid_candidate_lineage_count": int(valid_lineage),
            "invalid_candidate_lineage_count": len(candidates) - int(valid_lineage),
        },
        "market_context": {
            "timeframes": market_sources,
            "regime_inferred_from_future_pnl": False,
        },
        "current_positions": snapshot["current_positions"],
        "opportunity_book": snapshot["opportunity_book"],
        "opportunity_cost": snapshot["opportunity_cost"],
        "alpha_decay": snapshot["alpha_decay"],
        "replacement_research": snapshot["replacement_research"],
        "exit_efficiency": exit_efficiency,
        "event_engine": {
            "status": "ok",
            "processed_event_count": processed["processed_event_count"],
            "deterministic_replay_contract": True,
            "clock_injected": True,
            "order_adapter_present": False,
        },
        "multiasset": snapshot["multiasset"],
        "public_ws_adapter_status": "not_implemented_no_reusable_public_provider",
        "gates": gates,
        "warnings": position_warnings,
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "ledger_append_requested": False,
        "ledger_append_performed": False,
        "output_report": str((root / DEFAULT_REPORT).resolve()),
        "output_ledger": str((root / DEFAULT_LEDGER).resolve()),
    }


def _attach_market_context(
    event: Mapping[str, Any],
    frames: Mapping[str, pd.DataFrame],
    descriptors: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    payload = dict(event)
    observed_utc = utc_iso(payload.get("observed_at_utc"))
    if observed_utc is None:
        payload["market_evidence"] = {}
        return payload
    observed = pd.Timestamp(observed_utc)
    symbol = normalize_symbol(payload.get("symbol"))
    evidence: dict[str, Any] = {}
    for timeframe, frame in frames.items():
        if frame.empty:
            continue
        seconds = {"15s": 15, "1m": 60, "5m": 300}[timeframe]
        eligible = frame.loc[
            frame["symbol"].eq(symbol)
            & frame["timestamp"].add(pd.Timedelta(seconds=seconds)).le(observed)
        ]
        if eligible.empty:
            continue
        row = eligible.iloc[-1]
        generated = row.get("generated_at_utc")
        evidence[timeframe] = {
            "symbol": symbol,
            "timestamp": row["timestamp"].isoformat(),
            "available_at_utc": (row["timestamp"] + pd.Timedelta(seconds=seconds)).isoformat(),
            "generated_at_utc": generated.isoformat() if pd.notna(generated) else None,
            "source_hash": descriptors[timeframe]["source_hash"],
            "source_row_identity": stable_id(
                "market-row",
                {
                    "source_hash": descriptors[timeframe]["source_hash"],
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": row["timestamp"].isoformat(),
                },
            ),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "market_regime": row.get("market_regime"),
            "regime_method": row.get("regime_method"),
            "regime_lookback": row.get("regime_lookback"),
        }
    payload["market_evidence"] = evidence
    return payload
