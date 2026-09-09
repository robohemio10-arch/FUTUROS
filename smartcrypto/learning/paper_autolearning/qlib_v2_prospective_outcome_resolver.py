"""Strict identity-safe resolution of Qlib V2 prospective Paper outcomes.

The resolver consumes the immutable Qlib V2 freeze, the ex-ante prospective signal
ledger, a read-only Freqtrade Paper snapshot, and canonical Paper outcome events. It
resolves only this chain:

    signal_id
        -> decision_event_id captured ex ante
        -> exact decision_event_id token in Freqtrade enter_tag
        -> paper_trade_id
        -> exact outcome_events.trade_id
        -> stressed economic result

No timestamp-nearest matching, symbol/side identity inference, list-position matching,
trade-id-to-candidate aliasing, runtime writes, RiskManager mutation, order submission,
or private exchange access is allowed. Symbol, side, and timestamps are consistency
checks only after identity has already been resolved by explicit IDs.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from smartcrypto.execution.freqtrade_contract import internal_symbol
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    CandidateLineageError,
    extract_explicit_decision_event_id,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_v2_prospective_paper_confirmation as prospective,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_v2_prospective_signal_observer as observer,
)

SCHEMA_VERSION = "paper_autolearning_qlib_v2_prospective_outcome_resolver_v2"
DEFAULT_FREEZE_SPEC_PATH = observer.DEFAULT_FREEZE_SPEC_PATH
DEFAULT_OBSERVER_LEDGER_PATH = observer.DEFAULT_LEDGER_PATH
DEFAULT_PAPER_SNAPSHOT_DB_PATH = Path(
    "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)
DEFAULT_OUTCOME_PATH = base.DEFAULT_OUTCOME_PATH
DEFAULT_REPORT_PATH = Path(
    "data/research/qlib_v2/qlib_v2_prospective_outcome_resolution_v2.json"
)

MIN_RESOLVED_DECISIONS = 200
MIN_OBSERVATION_DAYS = 45.0
MIN_SELECTED_TRADES = 50
MIN_SCORER_COVERAGE = 0.99
MIN_TREATMENT_PROFIT_FACTOR = 1.10
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_SEED = 20260909

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only_execution_inputs": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "changes_risk": False,
    "changes_strategy": False,
    "changes_stake": False,
    "changes_leverage": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "historical_backfill_allowed": False,
    "fuzzy_identity_matching_allowed": False,
    "timestamp_only_identity_matching_allowed": False,
    "symbol_side_identity_inference_allowed": False,
    "trade_id_as_candidate_id_allowed": False,
}


def build_qlib_v2_prospective_outcome_resolution_v1(
    *,
    project_root: str | Path,
    freeze_spec: Mapping[str, Any] | None = None,
    observer_ledger: Mapping[str, Any] | None = None,
    paper_trade_rows: Sequence[Mapping[str, Any]] | None = None,
    outcome_rows: Sequence[Mapping[str, Any]] | None = None,
    freeze_spec_path: str | Path | None = None,
    observer_ledger_path: str | Path | None = None,
    paper_snapshot_db_path: str | Path | None = None,
    outcome_path: str | Path | None = None,
    paper_trade_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Resolve ex-ante Qlib V2 signal observations to exact realized Paper outcomes."""

    root = Path(project_root).resolve()
    resolved_freeze_path = base._resolve(root, freeze_spec_path or DEFAULT_FREEZE_SPEC_PATH)
    resolved_ledger_path = base._resolve(
        root,
        observer_ledger_path or DEFAULT_OBSERVER_LEDGER_PATH,
    )
    resolved_snapshot_path = base._resolve(
        root,
        paper_snapshot_db_path or DEFAULT_PAPER_SNAPSHOT_DB_PATH,
    )
    resolved_outcome_path = base._resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)

    freeze = (
        dict(freeze_spec)
        if freeze_spec is not None
        else _read_json_object(resolved_freeze_path)
    )
    freeze_state, freeze_blockers = observer._validate_freeze(freeze)
    if freeze_state is None:
        return _blocked_report(
            reason=freeze_blockers[0],
            blockers=freeze_blockers,
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
        )

    policy_sha = str(freeze_state["policy_sha256"])
    boundary = freeze_state["boundary"]
    assert isinstance(boundary, datetime)
    stress_bps = float(freeze_state["stress_bps"])

    ledger = (
        dict(observer_ledger)
        if observer_ledger is not None
        else _read_json_object(resolved_ledger_path)
    )
    observations, ledger_blockers = _validate_observer_ledger(
        ledger=ledger,
        freeze=freeze,
        boundary=boundary,
        policy_sha=policy_sha,
    )
    if ledger_blockers:
        return _blocked_report(
            reason=ledger_blockers[0],
            blockers=ledger_blockers,
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    if not observations:
        return {
            **_common_report(
                freeze_path=resolved_freeze_path,
                ledger_path=resolved_ledger_path,
                snapshot_path=resolved_snapshot_path,
                outcome_path=resolved_outcome_path,
                policy_sha256=policy_sha,
                prospective_start_utc=boundary,
            ),
            "status": "waiting_for_observations",
            "reason": "prospective_signal_ledger_empty",
            "decision": "COLETAR_EVIDENCIA_PROSPECTIVA",
            "observation_count": 0,
            "linked_paper_trade_count": 0,
            "resolved_decision_count": 0,
            "selected_resolved_trade_count": 0,
            "economic_evidence": None,
            "promotion_gate": _promotion_gate_empty(),
            "resolved_observations": [],
            "unresolved_observations": [],
            "blockers": [],
        }

    if paper_trade_rows is None:
        try:
            trade_rows = _read_closed_paper_trades_readonly(resolved_snapshot_path)
            source_sha = _sha256_file(resolved_snapshot_path)
            trade_source_mode = "read_only_sqlite_snapshot"
        except (OSError, ValueError, sqlite3.Error) as exc:
            return _blocked_report(
                reason="paper_snapshot_read_failed",
                blockers=["paper_snapshot_read_failed"],
                freeze_path=resolved_freeze_path,
                ledger_path=resolved_ledger_path,
                snapshot_path=resolved_snapshot_path,
                outcome_path=resolved_outcome_path,
                policy_sha256=policy_sha,
                prospective_start_utc=boundary,
                diagnostics={"error_type": type(exc).__name__, "error": str(exc)},
            )
    else:
        trade_rows = [dict(row) for row in paper_trade_rows]
        source_sha = (
            _validate_sha256_text(paper_trade_source_sha256)
            if paper_trade_source_sha256 is not None
            else prospective._sha256_json(trade_rows)
        )
        trade_source_mode = "injected_rows"

    if outcome_rows is None:
        outcome_input = base._read_rows(resolved_outcome_path)
    else:
        outcome_input = [dict(row) for row in outcome_rows]

    trade_index, trade_blockers = _index_paper_trades(trade_rows)
    if trade_blockers:
        return _blocked_report(
            reason=trade_blockers[0],
            blockers=trade_blockers,
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    outcome_index, outcome_blockers = _index_outcomes(outcome_input)
    if outcome_blockers:
        return _blocked_report(
            reason=outcome_blockers[0],
            blockers=outcome_blockers,
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    resolved: list[dict[str, Any]] = []
    resolved_outcome_rows: list[dict[str, Any]] = []
    selected_outcome_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    integrity_blockers: list[str] = []
    linked_trade_count = 0
    late_observation_count = 0
    late_score_completion_count = 0
    late_ledger_recording_count = 0

    for item in observations:
        decision_event_id = str(item["decision_event_id"])
        trade = trade_index.get(decision_event_id)
        if trade is None:
            unresolved.append(_unresolved(item, "paper_trade_not_yet_linked"))
            continue

        linked_trade_count += 1
        consistency = _validate_observation_trade_consistency(item, trade)
        if consistency:
            integrity_blockers.extend(consistency)
            continue

        observed_at = _parse_utc(item["observed_at_utc"], "observed_at_utc")
        score_completed_at = _parse_utc(
            item["score_completed_at_utc"],
            "score_completed_at_utc",
        )
        ledger_recorded_at = _parse_utc(
            item["ledger_recorded_at_utc"],
            "ledger_recorded_at_utc",
        )
        trade_open = trade["open_date"]
        trade_close = trade["close_date"]
        assert isinstance(trade_open, datetime)
        assert isinstance(trade_close, datetime)

        if observed_at > trade_close:
            late_observation_count += 1
            unresolved.append(_unresolved(item, "observation_after_trade_close", trade=trade))
            continue
        if observed_at > trade_open:
            late_observation_count += 1
            unresolved.append(_unresolved(item, "observation_after_trade_open", trade=trade))
            continue
        if score_completed_at > trade_open:
            late_score_completion_count += 1
            unresolved.append(
                _unresolved(item, "score_completed_after_trade_open", trade=trade)
            )
            continue
        if ledger_recorded_at > trade_open:
            late_ledger_recording_count += 1
            unresolved.append(
                _unresolved(item, "ledger_recorded_after_trade_open", trade=trade)
            )
            continue

        paper_trade_id = int(trade["id"])
        outcome = outcome_index.get(paper_trade_id)
        if outcome is None:
            unresolved.append(_unresolved(item, "outcome_event_not_yet_available", trade=trade))
            continue

        economic_blockers = _validate_trade_outcome_consistency(
            observation=item,
            trade=trade,
            outcome=outcome,
        )
        if economic_blockers:
            integrity_blockers.extend(economic_blockers)
            continue

        notional = base._notional(outcome)
        net_pnl = base._numeric(outcome.get("net_pnl"))
        if notional is None or notional <= 0:
            unresolved.append(_unresolved(item, "economic_notional_missing", trade=trade))
            continue
        if net_pnl is None or not math.isfinite(net_pnl):
            unresolved.append(_unresolved(item, "economic_net_pnl_missing", trade=trade))
            continue

        stressed_pnl = base._stressed_pnl(outcome, stress_bps)
        selected = item.get("selected") is True
        normalized_outcome = dict(outcome)
        resolved_outcome_rows.append(normalized_outcome)
        if selected:
            selected_outcome_rows.append(normalized_outcome)

        resolved.append(
            {
                "observation_id": item["observation_id"],
                "candidate_id": item["candidate_id"],
                "signal_id": item["signal_id"],
                "correlation_id": item["correlation_id"],
                "decision_event_id": decision_event_id,
                "paper_trade_id": paper_trade_id,
                "pair": trade["pair"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "decision_timestamp_utc": item["decision_timestamp_utc"],
                "observed_at_utc": item["observed_at_utc"],
                "score_completed_at_utc": item["score_completed_at_utc"],
                "ledger_recorded_at_utc": item["ledger_recorded_at_utc"],
                "trade_open_time_utc": _time_iso(trade_open),
                "trade_close_time_utc": _time_iso(trade_close),
                "selected": selected,
                "score": item["score"],
                "threshold": item["threshold"],
                "net_pnl": round(float(net_pnl), 10),
                "stressed_net_pnl": round(float(stressed_pnl), 10),
                "notional": round(float(notional), 10),
                "observation_precedes_trade_open": True,
                "score_completion_precedes_trade_open": True,
                "ledger_recording_precedes_trade_open": True,
                "identity_resolution": (
                    "signal_id->decision_event_id->enter_tag->paper_trade_id->outcome_trade_id"
                ),
            }
        )

    if integrity_blockers:
        unique = list(dict.fromkeys(integrity_blockers))
        return _blocked_report(
            reason=unique[0],
            blockers=unique,
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={
                "linked_paper_trade_count": linked_trade_count,
                "partial_resolved_count": len(resolved),
            },
        )

    baseline = base._economic_metrics(resolved_outcome_rows, stress_bps)
    treatment = base._economic_metrics(selected_outcome_rows, stress_bps)
    delta_net = float(treatment["net_pnl"]) - float(baseline["net_pnl"])
    bootstrap = _paired_bootstrap_delta(
        resolved=resolved,
        samples=BOOTSTRAP_SAMPLES,
        seed=BOOTSTRAP_SEED,
    )

    latest_recorded_at = max(
        (
            _parse_utc(item["ledger_recorded_at_utc"], "ledger_recorded_at_utc")
            for item in observations
        ),
        default=boundary,
    )
    observation_days = max(
        0.0,
        (latest_recorded_at - boundary).total_seconds() / 86400.0,
    )
    scorer_coverage = (
        len(resolved) / linked_trade_count if linked_trade_count else 0.0
    )
    execution_link_rate = (
        linked_trade_count / len(observations) if observations else 0.0
    )

    gate = _promotion_gate(
        resolved_count=len(resolved),
        observation_days=observation_days,
        selected_count=len(selected_outcome_rows),
        scorer_coverage=scorer_coverage,
        treatment=treatment,
        delta_net=delta_net,
        bootstrap=bootstrap,
    )
    gate_passed = bool(gate["all_economic_evidence_gates_passed"])

    return {
        **_common_report(
            freeze_path=resolved_freeze_path,
            ledger_path=resolved_ledger_path,
            snapshot_path=resolved_snapshot_path,
            outcome_path=resolved_outcome_path,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        ),
        "status": "evidence_gate_passed_research_only" if gate_passed else "collecting",
        "reason": (
            "prospective_economic_evidence_gates_passed_research_only"
            if gate_passed
            else "prospective_evidence_below_required_gate"
        ),
        "decision": (
            "PROSPECTIVE_EVIDENCE_GATE_PASSED_RESEARCH_ONLY"
            if gate_passed
            else "COLETAR_EVIDENCIA_PROSPECTIVA"
        ),
        "paper_trade_source_mode": trade_source_mode,
        "paper_trade_source_sha256": source_sha,
        "observation_count": len(observations),
        "linked_paper_trade_count": linked_trade_count,
        "resolved_decision_count": len(resolved),
        "selected_resolved_trade_count": len(selected_outcome_rows),
        "late_observation_count": late_observation_count,
        "late_score_completion_count": late_score_completion_count,
        "late_ledger_recording_count": late_ledger_recording_count,
        "unresolved_observation_count": len(unresolved),
        "observation_days": round(observation_days, 10),
        "execution_link_rate": round(execution_link_rate, 10),
        "scorer_coverage": round(scorer_coverage, 10),
        "economic_evidence": {
            "additional_execution_stress_bps": stress_bps,
            "baseline": baseline,
            "treatment": treatment,
            "delta_stressed_net_pnl": round(delta_net, 10),
            "paired_bootstrap_delta": bootstrap,
        },
        "promotion_gate": gate,
        "prospective_profit_certified": False,
        "promotion_allowed": False,
        "resolved_observations": resolved,
        "unresolved_observations": unresolved,
        "blockers": [],
    }


def _validate_observer_ledger(
    *,
    ledger: Mapping[str, Any],
    freeze: Mapping[str, Any],
    boundary: datetime,
    policy_sha: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    if not ledger:
        return [], ["prospective_signal_ledger_missing"]
    if ledger.get("schema_version") != observer.LEDGER_SCHEMA_VERSION:
        blockers.append("prospective_signal_ledger_schema_mismatch")
    if ledger.get("policy_sha256") != policy_sha:
        blockers.append("prospective_signal_ledger_policy_mismatch")
    if ledger.get("identity_authority") != "sealed_decision_ledger_v4_2":
        blockers.append("prospective_signal_ledger_identity_authority_mismatch")
    if ledger.get("prospective_start_utc") != freeze.get("prospective_start_utc"):
        blockers.append("prospective_signal_ledger_boundary_mismatch")

    raw_observations = ledger.get("observations")
    if not isinstance(raw_observations, list):
        return [], [*blockers, "prospective_signal_ledger_observations_invalid"]

    hash_payload = {
        "schema_version": observer.LEDGER_SCHEMA_VERSION,
        "policy_sha256": policy_sha,
        "prospective_start_utc": ledger.get("prospective_start_utc"),
        "identity_authority": "sealed_decision_ledger_v4_2",
        "observations": raw_observations,
    }
    expected_ledger_sha = prospective._sha256_json(hash_payload)
    if ledger.get("ledger_sha256") != expected_ledger_sha:
        blockers.append("prospective_signal_ledger_sha256_mismatch")

    observations: list[dict[str, Any]] = []
    seen_signal: set[str] = set()
    seen_decision: set[str] = set()
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, Mapping):
            blockers.append(f"prospective_observation_not_mapping:{index}")
            continue
        item = dict(raw)
        required_text = (
            "observation_id",
            "observation_sha256",
            "candidate_id",
            "signal_id",
            "correlation_id",
            "decision_event_id",
            "decision_payload_sha256",
            "decision_ledger_schema_version",
            "decision_ledger_record_type",
            "decision_ledger_runtime_mode",
            "decision_feature_timestamp_utc",
            "decision_feature_hash",
            "decision_model_id",
            "decision_model_version",
            "decision_model_hash",
            "policy_sha256",
            "pre_boundary_dataset_sha256",
            "decision_timestamp_utc",
            "observed_at_utc",
            "score_completed_at_utc",
            "ledger_recorded_at_utc",
            "signal_valid_until_utc",
            "signal_snapshot_sha256",
            "feature_vector_sha256",
        )
        missing = [field for field in required_text if not _nonempty_text(item.get(field))]
        if missing:
            blockers.append(
                f"prospective_observation_required_field_missing:{index}:{missing[0]}"
            )
            continue
        if item.get("decision_ledger_identity_verified") is not True:
            blockers.append(
                f"prospective_observation_decision_ledger_unverified:{index}"
            )
            continue
        if item.get("decision_ledger_schema_version") != "decision_ledger_payload_v4_2":
            blockers.append(
                f"prospective_observation_decision_ledger_schema_mismatch:{index}"
            )
            continue
        if item.get("decision_ledger_record_type") != "decision":
            blockers.append(
                f"prospective_observation_decision_record_type_invalid:{index}"
            )
            continue
        if item.get("decision_ledger_runtime_mode") != "paper":
            blockers.append(
                f"prospective_observation_decision_runtime_mode_invalid:{index}"
            )
            continue
        if item["policy_sha256"] != policy_sha:
            blockers.append(f"prospective_observation_policy_mismatch:{index}")
        if item["pre_boundary_dataset_sha256"] != freeze.get(
            "pre_boundary_dataset_sha256"
        ):
            blockers.append(f"prospective_observation_dataset_mismatch:{index}")
        if item.get("post_outcome_fields_present") is not False:
            blockers.append(f"prospective_observation_post_outcome_fields_present:{index}")
        if item.get("signal_active_at_observation") is not True:
            blockers.append(f"prospective_observation_signal_not_active:{index}")
        if not isinstance(item.get("selected"), bool):
            blockers.append(f"prospective_observation_selected_invalid:{index}")

        signal_id = str(item["signal_id"])
        decision_id = str(item["decision_event_id"])
        if signal_id in seen_signal:
            blockers.append(f"prospective_observation_duplicate_signal_id:{signal_id}")
        if decision_id in seen_decision:
            blockers.append(f"prospective_observation_duplicate_decision_event_id:{decision_id}")
        seen_signal.add(signal_id)
        seen_decision.add(decision_id)

        try:
            decision_time = _parse_utc(
                item["decision_timestamp_utc"],
                "decision_timestamp_utc",
            )
            observed_time = _parse_utc(item["observed_at_utc"], "observed_at_utc")
            score_completed_time = _parse_utc(
                item["score_completed_at_utc"],
                "score_completed_at_utc",
            )
            ledger_recorded_time = _parse_utc(
                item["ledger_recorded_at_utc"],
                "ledger_recorded_at_utc",
            )
            valid_until = _parse_utc(
                item["signal_valid_until_utc"],
                "signal_valid_until_utc",
            )
        except ValueError as exc:
            blockers.append(f"prospective_observation_timestamp_invalid:{index}:{exc}")
            continue
        if decision_time <= boundary:
            blockers.append(f"prospective_observation_not_post_freeze:{index}")
        if observed_time < decision_time:
            blockers.append(f"prospective_observation_before_decision:{index}")
        if score_completed_time < observed_time:
            blockers.append(f"prospective_score_completed_before_observation:{index}")
        if ledger_recorded_time < score_completed_time:
            blockers.append(f"prospective_ledger_recorded_before_score_completion:{index}")
        if observed_time > valid_until:
            blockers.append(f"prospective_observation_after_signal_expiry:{index}")
        if score_completed_time > valid_until:
            blockers.append(f"prospective_score_completed_after_signal_expiry:{index}")
        if ledger_recorded_time > valid_until:
            blockers.append(f"prospective_ledger_recorded_after_signal_expiry:{index}")

        for hash_field in (
            "observation_sha256",
            "decision_payload_sha256",
            "decision_feature_hash",
            "decision_model_hash",
            "signal_snapshot_sha256",
            "feature_vector_sha256",
        ):
            try:
                _validate_sha256_text(str(item[hash_field]))
            except ValueError:
                blockers.append(f"prospective_observation_sha256_invalid:{index}:{hash_field}")

        if observer._observation_sha256(item) != item["observation_sha256"]:
            blockers.append(f"prospective_observation_sha256_mismatch:{index}")
        observations.append(item)

    if blockers:
        return [], list(dict.fromkeys(blockers))
    observations.sort(
        key=lambda item: (
            str(item["decision_timestamp_utc"]),
            str(item["signal_id"]),
        )
    )
    return observations, []


def _read_closed_paper_trades_readonly(path: Path) -> list[dict[str, Any]]:
    candidate = path.expanduser().resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("paper_snapshot_not_regular_file")
    uri = candidate.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(
            "SELECT id, pair, is_short, is_open, open_date, close_date, enter_tag "
            "FROM trades WHERE is_open = 0 ORDER BY id"
        )
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _index_paper_trades(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    index: dict[str, dict[str, Any]] = {}
    seen_trade_ids: set[int] = set()
    blockers: list[str] = []

    for row_number, raw in enumerate(rows):
        row = dict(raw)
        tag = row.get("enter_tag")
        if not isinstance(tag, str) or "decision_event_id=" not in tag:
            continue
        try:
            decision_event_id = extract_explicit_decision_event_id(tag)
            trade_id = _positive_int(row.get("id"), "paper_trade_id")
            is_open = _strict_bool(row.get("is_open"), "paper_trade_is_open")
            if is_open:
                raise ValueError("paper_trade_must_be_closed")
            pair = _required_text(row, "pair")
            symbol = internal_symbol(pair)
            if not symbol:
                raise ValueError("paper_trade_symbol_invalid")
            is_short = _strict_bool(row.get("is_short"), "paper_trade_is_short")
            side = "short" if is_short else "long"
            open_date = _parse_utc(row.get("open_date"), "paper_trade_open_date")
            close_date = _parse_utc(row.get("close_date"), "paper_trade_close_date")
            if close_date < open_date:
                raise ValueError("paper_trade_close_before_open")
        except CandidateLineageError as exc:
            blockers.append(
                f"paper_trade_decision_event_id_invalid:{row_number}:{exc.reason}"
            )
            continue
        except (TypeError, ValueError) as exc:
            blockers.append(f"paper_trade_invalid:{row_number}:{str(exc)}")
            continue

        if decision_event_id in index:
            blockers.append(f"duplicate_paper_trade_decision_event_id:{decision_event_id}")
            continue
        if trade_id in seen_trade_ids:
            blockers.append(f"duplicate_paper_trade_id:{trade_id}")
            continue
        seen_trade_ids.add(trade_id)
        index[decision_event_id] = {
            "id": trade_id,
            "pair": pair,
            "symbol": symbol,
            "side": side,
            "is_open": False,
            "open_date": open_date,
            "close_date": close_date,
            "enter_tag": tag,
        }
    return index, list(dict.fromkeys(blockers))


def _index_outcomes(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[str]]:
    normalized, invalid_time_count = base._normalize_outcomes([dict(row) for row in rows])
    blockers: list[str] = []
    if invalid_time_count:
        blockers.append("outcome_event_invalid_time_detected")

    index: dict[int, dict[str, Any]] = {}
    for raw in normalized:
        value = raw.get("trade_id")
        try:
            trade_id = _positive_int(value, "outcome_trade_id")
        except (TypeError, ValueError):
            continue
        if trade_id in index:
            blockers.append(f"duplicate_outcome_trade_id:{trade_id}")
            continue
        index[trade_id] = dict(raw)
    return index, list(dict.fromkeys(blockers))


def _validate_observation_trade_consistency(
    observation: Mapping[str, Any],
    trade: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    decision_time = _parse_utc(
        observation.get("decision_timestamp_utc"),
        "decision_timestamp_utc",
    )
    generated_time = _parse_utc(
        observation.get("signal_generated_at_utc"),
        "signal_generated_at_utc",
    )
    trade_open = trade["open_date"]
    assert isinstance(trade_open, datetime)
    if decision_time > trade_open:
        blockers.append(
            f"decision_timestamp_after_trade_open:{observation.get('decision_event_id')}"
        )
    if generated_time > trade_open:
        blockers.append(
            f"signal_generated_after_trade_open:{observation.get('signal_id')}"
        )

    expected_symbol = internal_symbol(str(observation.get("symbol") or ""))
    if expected_symbol != trade.get("symbol"):
        blockers.append(f"paper_trade_symbol_mismatch:{observation.get('signal_id')}")
    if str(observation.get("side") or "").lower() != trade.get("side"):
        blockers.append(f"paper_trade_side_mismatch:{observation.get('signal_id')}")
    return blockers


def _validate_trade_outcome_consistency(
    *,
    observation: Mapping[str, Any],
    trade: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    trade_id = int(trade["id"])
    try:
        outcome_trade_id = _positive_int(outcome.get("trade_id"), "outcome_trade_id")
    except (TypeError, ValueError):
        return [f"outcome_trade_id_invalid:{trade_id}"]
    if outcome_trade_id != trade_id:
        blockers.append(f"outcome_trade_id_mismatch:{trade_id}")

    outcome_symbol = internal_symbol(
        str(outcome.get("symbol_norm") or outcome.get("symbol") or "")
    )
    if outcome_symbol != trade.get("symbol"):
        blockers.append(f"outcome_symbol_mismatch:{trade_id}")
    if str(outcome.get("side") or "").strip().lower() != trade.get("side"):
        blockers.append(f"outcome_side_mismatch:{trade_id}")

    outcome_open = outcome.get("__open_time")
    outcome_close = outcome.get("__close_time")
    trade_open = trade.get("open_date")
    trade_close = trade.get("close_date")
    if not isinstance(outcome_open, datetime) or not isinstance(outcome_close, datetime):
        blockers.append(f"outcome_time_missing:{trade_id}")
        return blockers
    if not isinstance(trade_open, datetime) or not isinstance(trade_close, datetime):
        blockers.append(f"paper_trade_time_missing:{trade_id}")
        return blockers
    if abs((outcome_open - trade_open).total_seconds()) > 0.001:
        blockers.append(f"outcome_open_time_mismatch:{trade_id}")
    if abs((outcome_close - trade_close).total_seconds()) > 0.001:
        blockers.append(f"outcome_close_time_mismatch:{trade_id}")

    observed_at = _parse_utc(observation.get("observed_at_utc"), "observed_at_utc")
    score_completed_at = _parse_utc(
        observation.get("score_completed_at_utc"),
        "score_completed_at_utc",
    )
    ledger_recorded_at = _parse_utc(
        observation.get("ledger_recorded_at_utc"),
        "ledger_recorded_at_utc",
    )
    if observed_at > outcome_close:
        blockers.append(f"observation_after_outcome_close:{trade_id}")
    if score_completed_at > outcome_close:
        blockers.append(f"score_completed_after_outcome_close:{trade_id}")
    if ledger_recorded_at > outcome_close:
        blockers.append(f"ledger_recorded_after_outcome_close:{trade_id}")
    return blockers


def _paired_bootstrap_delta(
    *,
    resolved: Sequence[Mapping[str, Any]],
    samples: int,
    seed: int,
) -> dict[str, Any]:
    if not resolved:
        return {
            "sample_count": samples,
            "seed": seed,
            "resolved_trade_count": 0,
            "delta_net_pnl_ci95_lower": None,
            "delta_net_pnl_ci95_upper": None,
            "delta_expectancy_ci95_lower": None,
            "delta_expectancy_ci95_upper": None,
        }

    deltas = np.asarray(
        [
            0.0
            if item.get("selected") is True
            else -float(item["stressed_net_pnl"])
            for item in resolved
        ],
        dtype=float,
    )
    if not np.isfinite(deltas).all():
        raise ValueError("bootstrap_delta_non_finite")

    rng = np.random.default_rng(seed)
    n = len(deltas)
    totals = np.empty(samples, dtype=float)
    means = np.empty(samples, dtype=float)
    for index in range(samples):
        sampled = deltas[rng.integers(0, n, size=n)]
        totals[index] = float(sampled.sum())
        means[index] = float(sampled.mean())

    return {
        "sample_count": samples,
        "seed": seed,
        "resolved_trade_count": n,
        "delta_net_pnl_ci95_lower": round(float(np.quantile(totals, 0.025)), 10),
        "delta_net_pnl_ci95_upper": round(float(np.quantile(totals, 0.975)), 10),
        "delta_expectancy_ci95_lower": round(float(np.quantile(means, 0.025)), 10),
        "delta_expectancy_ci95_upper": round(float(np.quantile(means, 0.975)), 10),
    }


def _promotion_gate(
    *,
    resolved_count: int,
    observation_days: float,
    selected_count: int,
    scorer_coverage: float,
    treatment: Mapping[str, Any],
    delta_net: float,
    bootstrap: Mapping[str, Any],
) -> dict[str, Any]:
    treatment_pf = treatment.get("profit_factor")
    ci_lower = bootstrap.get("delta_net_pnl_ci95_lower")
    gates = {
        "resolved_decisions_min_200": resolved_count >= MIN_RESOLVED_DECISIONS,
        "observation_days_min_45": observation_days >= MIN_OBSERVATION_DAYS,
        "selected_trades_min_50": selected_count >= MIN_SELECTED_TRADES,
        "scorer_coverage_min_0_99": scorer_coverage >= MIN_SCORER_COVERAGE,
        "treatment_stressed_net_pnl_positive": float(treatment["net_pnl"]) > 0.0,
        "treatment_expectancy_positive": float(treatment["expectancy"]) > 0.0,
        "treatment_profit_factor_min_1_10": (
            treatment_pf is not None and float(treatment_pf) >= MIN_TREATMENT_PROFIT_FACTOR
        ),
        "delta_stressed_net_pnl_positive": delta_net > 0.0,
        "bootstrap_delta_net_pnl_ci95_lower_positive": (
            ci_lower is not None and float(ci_lower) > 0.0
        ),
    }
    return {
        "required_resolved_decisions": MIN_RESOLVED_DECISIONS,
        "required_observation_days": MIN_OBSERVATION_DAYS,
        "required_selected_trades": MIN_SELECTED_TRADES,
        "required_scorer_coverage": MIN_SCORER_COVERAGE,
        "required_treatment_profit_factor": MIN_TREATMENT_PROFIT_FACTOR,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "gates": gates,
        "all_economic_evidence_gates_passed": all(gates.values()),
        "promotion_allowed": False,
        "operational_authority": False,
    }


def _promotion_gate_empty() -> dict[str, Any]:
    return _promotion_gate(
        resolved_count=0,
        observation_days=0.0,
        selected_count=0,
        scorer_coverage=0.0,
        treatment={"net_pnl": 0.0, "expectancy": 0.0, "profit_factor": None},
        delta_net=0.0,
        bootstrap={"delta_net_pnl_ci95_lower": None},
    )


def _unresolved(
    observation: Mapping[str, Any],
    reason: str,
    *,
    trade: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trade_open = None if trade is None else trade.get("open_date")
    return {
        "observation_id": observation.get("observation_id"),
        "signal_id": observation.get("signal_id"),
        "decision_event_id": observation.get("decision_event_id"),
        "paper_trade_id": None if trade is None else trade.get("id"),
        "observed_at_utc": observation.get("observed_at_utc"),
        "score_completed_at_utc": observation.get("score_completed_at_utc"),
        "ledger_recorded_at_utc": observation.get("ledger_recorded_at_utc"),
        "paper_trade_open_time_utc": _time_iso(trade_open),
        "reason": reason,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_sha256_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError("sha256_invalid")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}_invalid")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field}_invalid")
    if parsed <= 0:
        raise ValueError(f"{field}_invalid")
    return parsed


def _strict_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{field}_invalid")


def _required_text(source: Mapping[str, Any], field: str) -> str:
    value = _nonempty_text(source.get(field))
    if value is None:
        raise ValueError(f"required_text_missing:{field}")
    return value


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _parse_utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"timestamp_missing:{field}")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp_not_timezone_aware:{field}")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"timestamp_not_utc:{field}")
    return parsed.astimezone(UTC)


def _time_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return None


def _common_report(
    *,
    freeze_path: Path,
    ledger_path: Path,
    snapshot_path: Path,
    outcome_path: Path,
    policy_sha256: str | None,
    prospective_start_utc: datetime | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "freeze_spec_path": str(freeze_path),
        "observer_ledger_path": str(ledger_path),
        "paper_snapshot_db_path": str(snapshot_path),
        "outcome_source_path": str(outcome_path),
        "policy_sha256": policy_sha256,
        "prospective_start_utc": _time_iso(prospective_start_utc),
        "identity_resolution_mode": (
            "signal_id->decision_event_id->enter_tag->paper_trade_id->outcome_trade_id"
        ),
        "decision_event_id_required": True,
        "observer_ledger_required": True,
        "paper_snapshot_read_only": True,
        "outcome_event_trade_id_exact_match_required": True,
        **SAFETY_FLAGS,
        "write_performed": False,
    }


def _blocked_report(
    *,
    reason: str,
    blockers: Sequence[str],
    freeze_path: Path,
    ledger_path: Path,
    snapshot_path: Path,
    outcome_path: Path,
    policy_sha256: str | None = None,
    prospective_start_utc: datetime | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **_common_report(
            freeze_path=freeze_path,
            ledger_path=ledger_path,
            snapshot_path=snapshot_path,
            outcome_path=outcome_path,
            policy_sha256=policy_sha256,
            prospective_start_utc=prospective_start_utc,
        ),
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "observation_count": 0,
        "linked_paper_trade_count": 0,
        "resolved_decision_count": 0,
        "selected_resolved_trade_count": 0,
        "economic_evidence": None,
        "promotion_gate": _promotion_gate_empty(),
        "prospective_profit_certified": False,
        "promotion_allowed": False,
        "resolved_observations": [],
        "unresolved_observations": [],
        "diagnostics": dict(diagnostics or {}),
        "blockers": list(dict.fromkeys(str(item) for item in blockers)),
    }
