"""Research-only economic comparison of Qlib V3 AI Shadow versus Paper control."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    AIShadowDecision,
    DecisionRecordV42,
    FinalDecision,
)
from smartcrypto.learning.qlib_v3_prospective import store
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    Identity,
    check_identity,
    utc,
)
from smartcrypto.research.aibot_parity.economic_benchmark import (
    EXPECTED_BASELINE,
    duration_bucket,
)

SCHEMA_VERSION = "ai_shadow_economic_challenger_sync_v1"
MIN_DIAGNOSTIC_RESOLVED_TRADES = 30


class AIShadowEconomicChallengerError(RuntimeError):
    """Fail-closed validation error for the research-only challenger."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AIShadowEconomicChallengerError(f"{name}_not_numeric")
    number = float(value)
    if not math.isfinite(number):
        raise AIShadowEconomicChallengerError(f"{name}_not_finite")
    return number


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    value = float(numerator) / float(denominator)
    if not math.isfinite(value):
        return None
    return value


def _pnl_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss_abs": 0.0,
            "profit_factor": None,
            "expectancy": None,
            "win_rate": None,
            "max_drawdown": None,
        }

    ordered = sorted(
        rows,
        key=lambda row: (
            utc(row["close_time_utc"]),
            int(row["trade_id"]),
        ),
    )
    pnl = [float(row["net_pnl"]) for row in ordered]
    gross_profit = float(sum(value for value in pnl if value > 0.0))
    gross_loss_abs = float(abs(sum(value for value in pnl if value < 0.0)))
    profit_factor = (
        gross_profit / gross_loss_abs if gross_loss_abs > 0.0 else None
    )

    cumulative = 0.0
    peak: float | None = None
    worst_drawdown = 0.0
    for value in pnl:
        cumulative += value
        if peak is None or cumulative > peak:
            peak = cumulative
        drawdown = cumulative - peak
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown

    return {
        "trade_count": len(pnl),
        "net_pnl": float(sum(pnl)),
        "gross_profit": gross_profit,
        "gross_loss_abs": gross_loss_abs,
        "profit_factor": profit_factor,
        "expectancy": float(sum(pnl) / len(pnl)),
        "win_rate": float(sum(value > 0.0 for value in pnl) / len(pnl)),
        "max_drawdown": abs(float(worst_drawdown)),
    }


def _comparison(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    control_rows = list(rows)
    treatment_rows = [row for row in rows if row["shadow_selected"] is True]
    rejected_rows = [row for row in rows if row["shadow_selected"] is False]

    control = _pnl_metrics(control_rows)
    treatment = _pnl_metrics(treatment_rows)
    rejected = _pnl_metrics(rejected_rows)

    control_losers = sum(float(row["net_pnl"]) < 0.0 for row in control_rows)
    rejected_losers = sum(float(row["net_pnl"]) < 0.0 for row in rejected_rows)
    control_winners = sum(float(row["net_pnl"]) > 0.0 for row in control_rows)
    rejected_winners = sum(float(row["net_pnl"]) > 0.0 for row in rejected_rows)
    selected_winners = sum(float(row["net_pnl"]) > 0.0 for row in treatment_rows)

    delta_expectancy = None
    if control["expectancy"] is not None and treatment["expectancy"] is not None:
        delta_expectancy = float(treatment["expectancy"] - control["expectancy"])

    delta_profit_factor = None
    if control["profit_factor"] is not None and treatment["profit_factor"] is not None:
        delta_profit_factor = float(
            treatment["profit_factor"] - control["profit_factor"]
        )

    delta_net_pnl = float(treatment["net_pnl"] - control["net_pnl"])
    direction = "neutral"
    if delta_net_pnl > 0.0:
        direction = "positive"
    elif delta_net_pnl < 0.0:
        direction = "negative"

    return {
        "control": control,
        "treatment": treatment,
        "rejected": rejected,
        "selected_trade_count": len(treatment_rows),
        "rejected_trade_count": len(rejected_rows),
        "coverage": _safe_ratio(len(treatment_rows), len(control_rows)),
        "delta_net_pnl": delta_net_pnl,
        "delta_expectancy": delta_expectancy,
        "delta_profit_factor": delta_profit_factor,
        "bad_trade_avoidance_rate": _safe_ratio(rejected_losers, control_losers),
        "false_abstention_rate": _safe_ratio(rejected_winners, control_winners),
        "profitable_trade_retention_rate": _safe_ratio(
            selected_winners,
            control_winners,
        ),
        "rejected_net_pnl": float(rejected["net_pnl"]),
        "selection_uplift_net_pnl": float(-rejected["net_pnl"]),
        "economic_direction": direction,
    }


def _validate_signal_rows(
    signals: Sequence[Mapping[str, Any]],
    *,
    expected: Identity,
) -> dict[str, DecisionRecordV42]:
    by_signal: dict[str, DecisionRecordV42] = {}
    seen_events: set[str] = set()

    for row in signals:
        if not isinstance(row, Mapping):
            raise AIShadowEconomicChallengerError("signal_row_not_object")
        signal_id = str(row.get("signal_id") or "")
        decision_event_id = str(row.get("decision_event_id") or "")
        if not signal_id or not decision_event_id:
            raise AIShadowEconomicChallengerError("signal_identity_missing")
        if signal_id in by_signal:
            raise AIShadowEconomicChallengerError("duplicate_signal_id")
        if decision_event_id in seen_events:
            raise AIShadowEconomicChallengerError("duplicate_decision_event_id")

        try:
            decision = DecisionRecordV42.model_validate(row.get("decision"))
        except Exception as exc:
            raise AIShadowEconomicChallengerError(
                "invalid_sealed_shadow_decision"
            ) from exc

        if decision.signal_id != signal_id or decision.event_id != decision_event_id:
            raise AIShadowEconomicChallengerError("signal_decision_identity_mismatch")
        if decision.model_hash != expected.model_artifact_sha256:
            raise AIShadowEconomicChallengerError("shadow_model_identity_mismatch")
        if decision.final_decision is not FinalDecision.ALLOW:
            raise AIShadowEconomicChallengerError("resolved_signal_not_paper_allow")
        if decision.ai_shadow_decision not in {
            AIShadowDecision.ALLOW,
            AIShadowDecision.ABSTAIN,
        }:
            raise AIShadowEconomicChallengerError(
                "shadow_decision_not_economically_rankable"
            )

        by_signal[signal_id] = decision
        seen_events.add(decision_event_id)

    return by_signal


def evaluate_evidence_state(
    state: Mapping[str, Any],
    *,
    expected: Identity = CANONICAL,
) -> dict[str, Any]:
    """Evaluate exact resolved V3 evidence without training or operational writes."""

    if state.get("schema_version") != store.SCHEMA:
        raise AIShadowEconomicChallengerError("evidence_store_schema_mismatch")
    try:
        check_identity(state.get("identity"), expected)
    except Exception as exc:
        raise AIShadowEconomicChallengerError("evidence_identity_mismatch") from exc

    signals = state.get("signals")
    outcomes = state.get("outcomes")
    if not isinstance(signals, list) or not isinstance(outcomes, list):
        raise AIShadowEconomicChallengerError("evidence_records_invalid")

    by_signal = _validate_signal_rows(signals, expected=expected)

    resolved_rows: list[dict[str, Any]] = []
    seen_trade_ids: set[int] = set()
    seen_resolved_signals: set[str] = set()

    for row in outcomes:
        if not isinstance(row, Mapping):
            raise AIShadowEconomicChallengerError("outcome_row_not_object")
        signal_id = str(row.get("signal_id") or "")
        decision = by_signal.get(signal_id)
        if decision is None:
            raise AIShadowEconomicChallengerError("outcome_without_shadow_signal")
        if signal_id in seen_resolved_signals:
            raise AIShadowEconomicChallengerError("multiple_outcomes_for_signal")

        trade_id_raw = row.get("trade_id")
        if isinstance(trade_id_raw, bool) or not isinstance(trade_id_raw, int):
            raise AIShadowEconomicChallengerError("trade_id_not_integer")
        trade_id = int(trade_id_raw)
        if trade_id <= 0 or trade_id in seen_trade_ids:
            raise AIShadowEconomicChallengerError("duplicate_or_invalid_trade_id")
        if str(row.get("decision_event_id") or "") != decision.event_id:
            raise AIShadowEconomicChallengerError("outcome_decision_identity_mismatch")
        if row.get("is_closed") is not True:
            raise AIShadowEconomicChallengerError("outcome_not_closed")

        open_time = utc(row.get("open_time_utc"))
        close_time = utc(row.get("close_time_utc"))
        if close_time <= open_time:
            raise AIShadowEconomicChallengerError("outcome_time_order_invalid")

        pnl = _finite_number(row.get("net_pnl"), "net_pnl")
        duration_seconds = float((close_time - open_time).total_seconds())
        bucket = duration_bucket(duration_seconds)
        if bucket is None:
            raise AIShadowEconomicChallengerError("duration_bucket_unavailable")

        selected = decision.ai_shadow_decision is AIShadowDecision.ALLOW
        resolved_rows.append(
            {
                "signal_id": signal_id,
                "decision_event_id": decision.event_id,
                "trade_id": trade_id,
                "symbol": decision.symbol,
                "side": decision.side.value,
                "qlib_score": float(decision.qlib_score),
                "shadow_selected": selected,
                "open_time_utc": open_time.isoformat(),
                "close_time_utc": close_time.isoformat(),
                "duration_seconds": duration_seconds,
                "duration_bucket": bucket,
                "net_pnl": pnl,
            }
        )
        seen_trade_ids.add(trade_id)
        seen_resolved_signals.add(signal_id)

    global_comparison = _comparison(resolved_rows)
    priority_bucket = str(EXPECTED_BASELINE["top_bucket"])
    priority_rows = [
        row for row in resolved_rows if row["duration_bucket"] == priority_bucket
    ]
    priority_comparison = _comparison(priority_rows)

    signal_count = len(by_signal)
    resolved_count = len(resolved_rows)
    unresolved_count = signal_count - resolved_count
    sample_sufficient = resolved_count >= MIN_DIAGNOSTIC_RESOLVED_TRADES

    if signal_count == 0:
        status = "waiting"
        reason = "no_natural_v3_shadow_signals"
    elif resolved_count == 0:
        status = "waiting"
        reason = "no_resolved_v3_shadow_outcomes"
    else:
        status = "ok"
        reason = "shadow_control_treatment_economic_comparison_ready"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": "OBSERVE_ONLY_RESEARCH_EVIDENCE",
        "identity": expected.mapping(),
        "signal_count": signal_count,
        "resolved_trade_count": resolved_count,
        "unresolved_signal_count": unresolved_count,
        "minimum_diagnostic_resolved_trades": MIN_DIAGNOSTIC_RESOLVED_TRADES,
        "sample_sufficient_for_diagnostic_comparison": sample_sufficient,
        "global_comparison": global_comparison,
        "branch08_priority": {
            "dimension": EXPECTED_BASELINE["top_dimension"],
            "bucket": priority_bucket,
        },
        "priority_segment_comparison": priority_comparison,
        "resolved_rows": resolved_rows,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "training_requested": False,
        "training_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_strategy": False,
        "changes_leverage": False,
        "changes_stake": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_data": False,
        "historical_backfill_allowed": False,
        "nearest_timestamp_matching_allowed": False,
        "approximate_matching": False,
        "exact_signal_trade_lineage": True,
    }


def build_ai_shadow_economic_challenger_sync_v1(
    *,
    project_root: str | Path,
    evidence_path: str | Path | None = None,
    expected: Identity = CANONICAL,
) -> dict[str, Any]:
    """Load the canonical V3 evidence store read-only and evaluate economics."""

    root = Path(project_root).resolve()
    if evidence_path is None:
        path = store.location(root, expected)
    else:
        explicit = Path(evidence_path)
        path = (explicit if explicit.is_absolute() else root / explicit).resolve()

    source_exists = False
    source_sha256: str | None = None
    source_size_bytes: int | None = None

    try:
        source_exists = path.is_file()
        if source_exists:
            source_sha256 = _sha256(path)
            source_size_bytes = path.stat().st_size
        state = store.load(path, expected)
        report = evaluate_evidence_state(state, expected=expected)
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": (
                str(exc)
                if isinstance(exc, AIShadowEconomicChallengerError)
                else f"evidence_load_or_validation_failed:{type(exc).__name__}"
            ),
            "decision": "OBSERVE_ONLY_RESEARCH_EVIDENCE",
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "training_requested": False,
            "training_performed": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_strategy": False,
            "changes_leverage": False,
            "changes_stake": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "writes_data": False,
            "historical_backfill_allowed": False,
            "nearest_timestamp_matching_allowed": False,
            "approximate_matching": False,
            "exact_signal_trade_lineage": True,
        }

    report["source"] = {
        "path": str(path),
        "exists": source_exists,
        "sha256": source_sha256,
        "size_bytes": source_size_bytes,
    }
    return report
