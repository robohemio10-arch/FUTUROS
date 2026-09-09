from __future__ import annotations

import ast
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from scripts.observe_qlib_v2_prospective_signals_v1 import (
    _merge_ledger,
    _validate_ledger_path,
)
from smartcrypto.execution.decision_ledger_v4_2 import seal_decision_record
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    build_qlib_v2_prospective_paper_confirmation_v1,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_signal_observer import (
    SCHEMA_VERSION,
    build_qlib_v2_prospective_signal_observer_v1,
)


MODULE_PATH = Path(
    "smartcrypto/learning/paper_autolearning/qlib_v2_prospective_signal_observer.py"
)


def _market_and_rows(count: int = 450) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle_count = 500 + count * 10 + 30
    candles: list[dict[str, object]] = []
    price = 50000.0
    closes: list[float] = []
    for index in range(candle_count):
        timestamp = base + timedelta(minutes=5 * index)
        wave = 20.0 * math.sin(index / 13.0) + 8.0 * math.sin(index / 5.0)
        close = max(100.0, price + wave)
        candles.append(
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "tf": "5m",
                "ts": timestamp,
                "open": price,
                "high": max(price, close) + 10.0,
                "low": min(price, close) - 10.0,
                "close": close,
                "volume": 1000.0 + 100.0 * math.sin(index / 7.0) + 10.0 * (index % 9),
            }
        )
        closes.append(close)
        price = close

    rows: list[dict[str, object]] = []
    for index in range(count):
        candle_index = 500 + index * 10
        open_time = base + timedelta(minutes=5 * (candle_index + 1))
        close_time = open_time + timedelta(minutes=20)
        side = "long" if index % 2 == 0 else "short"
        ret_5 = closes[candle_index] / closes[candle_index - 5] - 1.0
        net_pnl = (3.0 if ret_5 > 0 else -1.0) if side == "long" else -2.0
        rows.append(
            {
                "event_id": f"event-{index:04d}",
                "trade_id": f"trade-{index:04d}",
                "is_closed": True,
                "validation_status": "ok",
                "symbol_norm": "BTCUSDT",
                "side": side,
                "open_time_utc": open_time.isoformat(),
                "close_time_utc": close_time.isoformat(),
                "entry_price": 50000.0,
                "quantity": 0.002,
                "notional": 100.0,
                "leverage": 2.0,
                "net_pnl": net_pnl,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return pd.DataFrame(candles), rows


def _predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id

    def score(frame: pd.DataFrame) -> np.ndarray:
        signal = frame["feature_ret_5_side"].to_numpy(dtype=float)
        is_long = frame["feature_side_long"].to_numpy(dtype=float) >= 0.5
        return np.where(is_long, signal, 1_000_000.0)

    return score(calibration_x), score(test_x)


def _boundary(rows: list[dict[str, object]], index: int = 330) -> datetime:
    return datetime.fromisoformat(str(rows[index]["open_time_utc"]))


def _freeze(
    market: pd.DataFrame,
    rows: list[dict[str, object]],
    boundary: datetime,
) -> dict[str, Any]:
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        certified_implementation_commit="a" * 40,
        certified_ci_run_id=123456789,
        certified_ci_completed_at_utc=boundary - timedelta(seconds=1),
        freeze_materialized_at_utc=boundary,
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["freeze_provenance_complete"] is True
    freeze = report["freeze_spec"]
    assert isinstance(freeze, dict)
    return freeze


def _authoritative_signal(
    *,
    boundary: datetime,
    signal_id: str = "signal:phase13-signal-producer:observer001",
    decision_event_id: str = "decision-event:observer001",
    side: str = "long",
    include_attestation: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    decision_time = boundary + timedelta(minutes=10)
    generated_at = decision_time - timedelta(seconds=2)
    valid_until = decision_time + timedelta(minutes=30)
    candidate_id = "candidate:phase13-signal-producer:observer001"
    correlation_id = "correlation:observer001"
    score = 0.42

    record = seal_decision_record(
        {
            "event_id": decision_event_id,
            "signal_id": signal_id,
            "candidate_id": candidate_id,
            "correlation_id": correlation_id,
            "idempotency_key": "idempotency:observer001",
            "runtime_mode": "paper",
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": side,
            "feature_timestamp": generated_at,
            "decision_timestamp": decision_time,
            "feature_contract_version": "paper-signal-observation-lineage-v1",
            "feature_hash": "1" * 64,
            "model_id": "qlib_lgbm_v1",
            "model_version": "qlib_lgbm_v1",
            "model_hash": "2" * 64,
            "qlib_score": score,
            "calibrated_probability": 0.71,
            "expected_net_pnl": None,
            "fast_stop_probability": None,
            "regime": "unknown",
            "alignment": "unknown",
            "ai_shadow_decision": "NOT_EVALUATED",
            "ai_shadow_reasons": (),
            "risk_decision": "APPROVED",
            "risk_reasons": (),
            "approved_stake_usdt": 50.0,
            "approved_leverage": 2.0,
            "final_decision": "ALLOW",
            "final_reasons": ("risk_manager_approved",),
            "operational_authority": False,
            "runtime_integration": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }
    ).model_dump(mode="json")

    signal: dict[str, Any] = {
        "candidate_id": candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": side,
        "score": score,
        "model_version": "qlib_lgbm_v1",
        "risk_approved": True,
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        DECISION_LEDGER_KEY: {
            "schema_version": "decision_ledger_active_signal_envelope_v1",
            "decision_event_id": decision_event_id,
            "decision_payload_sha256": record["payload_sha256"],
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "decision_timestamp": decision_time.isoformat(),
        },
    }
    if include_attestation:
        signal[ATTESTATION_KEY] = {
            "schema_version": ATTESTATION_SCHEMA,
            "materialization_sha256": "3" * 64,
            "source_signal_sha256": "4" * 64,
            "research_candidate_sha256": "5" * 64,
            "registry_candidate_sha256": "6" * 64,
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "research_signal_candidate_id": "signal_candidate_observer001",
            "signal_instance_id": "signal-instance:observer001",
            "producer_id": "phase13-signal-producer",
            "prospective_only": True,
            "authoritative_identity": True,
            "synthetic_identity": False,
            "trade_id_used_as_candidate_id": False,
        }
    return signal, record, decision_time + timedelta(seconds=1)

def _observe(
    *,
    market: pd.DataFrame,
    rows: list[dict[str, object]],
    freeze: dict[str, Any],
    signal: dict[str, Any],
    decision_records: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[str, Any]:
    return build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_records=decision_records,
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
        score_completed_at_utc=observed_at + timedelta(milliseconds=100),
    )

def test_observer_scores_authoritative_signal_with_ex_ante_identity() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert report["status"] == "observing"
    assert report["freeze_spec_verified"] is True
    assert report["model_reconstruction_verified"] is True
    assert report["observation_count"] == 1
    observation = report["observations"][0]
    assert observation["schema_version"] == SCHEMA_VERSION
    assert observation["signal_id"] == signal["signal_id"]
    assert observation["decision_event_id"] == signal[DECISION_LEDGER_KEY]["decision_event_id"]
    assert observation["decision_payload_sha256"] == decision_record["payload_sha256"]
    assert observation["decision_ledger_identity_verified"] is True
    assert observation["lineage_attestation_present"] is False
    assert observation["observed_at_utc"] == observed_at.isoformat()
    assert observation["score_completed_at_utc"] == (
        observed_at + timedelta(milliseconds=100)
    ).isoformat()
    assert observation["scoring_latency_seconds"] == 0.1
    assert observation["signal_active_at_observation"] is True
    assert observation["signal_active_at_score_completion"] is True
    assert observation["post_outcome_fields_present"] is False
    assert len(observation["signal_snapshot_sha256"]) == 64
    assert len(observation["feature_vector_sha256"]) == 64
    assert len(observation["observation_sha256"]) == 64
    assert "trade_id" not in observation
    assert "paper_trade_id" not in observation
    assert "realized_net_pnl" not in observation
    assert report["writes_runtime"] is False
    assert report["sends_orders"] is False


def test_post_boundary_outcome_mutation_cannot_change_signal_observation() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)

    first = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    mutated = [dict(row) for row in rows]
    for row in mutated[331:]:
        row["net_pnl"] = float(row["net_pnl"]) * -1000.0

    second = _observe(
        market=market,
        rows=mutated,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert first["status"] == second["status"] == "observing"
    assert first["observations"] == second["observations"]
    assert first["calibration_score_fingerprint_sha256"] == second[
        "calibration_score_fingerprint_sha256"
    ]


def test_pre_boundary_outcome_mutation_blocks_frozen_reconstruction() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    mutated = [dict(row) for row in rows]
    mutated[100]["net_pnl"] = float(mutated[100]["net_pnl"]) + 0.5

    report = _observe(
        market=market,
        rows=mutated,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "frozen_dataset_reconstruction_mismatch"


def test_identity_mismatch_blocks_entire_evidence_batch() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _, observed_at = _authoritative_signal(boundary=boundary)
    _, mismatched_record, _ = _authoritative_signal(
        boundary=boundary,
        signal_id="signal:phase13-signal-producer:other",
    )

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[mismatched_record],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "sealed_decision_identity_mismatch:signal_id" in report["reason"]
    assert report["observation_count"] == 0


def test_optional_valid_lineage_attestation_is_preserved_but_not_required() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(
        boundary=boundary,
        include_attestation=True,
    )

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert report["status"] == "observing"
    observation = report["observations"][0]
    assert observation["lineage_attestation_present"] is True
    assert len(observation["lineage_attestation_sha256"]) == 64


def test_missing_sealed_decision_record_blocks_post_boundary_signal() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _, observed_at = _authoritative_signal(boundary=boundary)

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "decision_ledger_record_missing" in report["reason"]


def test_tampered_sealed_decision_payload_hash_blocks() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    tampered = dict(decision_record)
    tampered["payload_sha256"] = "f" * 64

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[tampered],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "decision_ledger_record_invalid" in report["reason"]


def test_pre_boundary_signal_is_ignored_without_decision_record_lookup() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _, _ = _authoritative_signal(boundary=boundary - timedelta(minutes=20))
    observed_at = boundary + timedelta(seconds=1)

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[],
        observed_at=observed_at,
    )

    assert report["status"] == "waiting_for_signals"
    assert report["reason"] == "no_authoritative_post_freeze_active_signals"
    assert report["blockers"] == []


def test_observer_reads_sealed_decision_jsonl_readonly(tmp_path: Path) -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    ledger_path = tmp_path / "decision_ledger_v4_2.jsonl"
    ledger_path.write_text(json.dumps(decision_record) + "\n", encoding="utf-8")

    report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_path=ledger_path,
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
    )

    assert report["status"] == "observing"
    assert report["decision_ledger_identity_verified"] is True
    assert report["decision_ledger_source_path"] == str(ledger_path.resolve())


def test_malformed_decision_ledger_jsonl_blocks(tmp_path: Path) -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _, observed_at = _authoritative_signal(boundary=boundary)
    ledger_path = tmp_path / "decision_ledger_v4_2.jsonl"
    ledger_path.write_text("{not-json\n", encoding="utf-8")

    report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_path=ledger_path,
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
    )

    assert report["status"] == "blocked"
    assert "decision_ledger_json_invalid:1" in report["reason"]
    assert report["freeze_spec_verified"] is True


def test_post_outcome_field_in_active_signal_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    signal["trade_id"] = 123

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "post_outcome_fields_forbidden:trade_id" in report["reason"]


def test_observer_cannot_backdate_before_decision() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, _ = _authoritative_signal(boundary=boundary)
    decision_time = datetime.fromisoformat(signal[DECISION_LEDGER_KEY]["decision_timestamp"])

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=decision_time - timedelta(milliseconds=1),
    )

    assert report["status"] == "blocked"
    assert "observer_timestamp_before_decision" in report["reason"]


def test_expired_signal_is_not_accepted_as_ex_ante_observation() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, _ = _authoritative_signal(boundary=boundary)
    valid_until = datetime.fromisoformat(signal["valid_until"])

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=valid_until + timedelta(microseconds=1),
    )

    assert report["status"] == "blocked"
    assert "signal_expired_before_observation" in report["reason"]


def test_non_utc_authoritative_timestamp_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    decision_time = datetime.fromisoformat(signal[DECISION_LEDGER_KEY]["decision_timestamp"])
    local_time = decision_time.astimezone(timezone(timedelta(hours=-3)))
    signal[DECISION_LEDGER_KEY] = dict(signal[DECISION_LEDGER_KEY])
    signal[DECISION_LEDGER_KEY]["decision_timestamp"] = local_time.isoformat()

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "timestamp_not_utc:decision_timestamp" in report["reason"]


def test_ledger_merge_is_idempotent_and_identity_mutation_fails_closed() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )

    recorded_at = observed_at + timedelta(milliseconds=200)
    first = _merge_ledger(
        existing=None,
        report=report,
        recorded_at_utc=recorded_at,
    )
    second = _merge_ledger(
        existing=first,
        report=report,
        recorded_at_utc=recorded_at + timedelta(milliseconds=50),
    )
    assert first["observation_count"] == 1
    assert first["observations"][0]["ledger_recorded_at_utc"] == recorded_at.isoformat()
    assert second["observation_count"] == 1
    assert second["new_observation_count"] == 0
    assert second["idempotent_observation_count"] == 1

    mutated_report = dict(report)
    changed = dict(report["observations"][0])
    changed["observation_sha256"] = hashlib.sha256(b"mutation").hexdigest()
    mutated_report["observations"] = [changed]
    with pytest.raises(RuntimeError, match="observer_signal_identity_mutation"):
        _merge_ledger(existing=first, report=mutated_report)


def test_score_completion_before_observer_start_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)

    report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_records=[decision_record],
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
        score_completed_at_utc=observed_at - timedelta(microseconds=1),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "score_completed_before_observer_started"


def test_score_completion_after_signal_expiry_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    valid_until = datetime.fromisoformat(str(signal["valid_until"]))

    report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_records=[decision_record],
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
        score_completed_at_utc=valid_until + timedelta(microseconds=1),
    )

    assert report["status"] == "blocked"
    assert report["reason"].startswith("signal_expired_before_score_completion:")


def test_ledger_recording_timestamp_must_follow_score_and_precede_expiry() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary=boundary)
    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        decision_records=[decision_record],
        observed_at=observed_at,
    )
    score_completed = datetime.fromisoformat(
        report["observations"][0]["score_completed_at_utc"]
    )
    valid_until = datetime.fromisoformat(str(signal["valid_until"]))

    with pytest.raises(
        RuntimeError,
        match="observer_ledger_recorded_before_score_completion",
    ):
        _merge_ledger(
            existing=None,
            report=report,
            recorded_at_utc=score_completed - timedelta(microseconds=1),
        )

    with pytest.raises(
        RuntimeError,
        match="observer_ledger_recorded_after_signal_expiry",
    ):
        _merge_ledger(
            existing=None,
            report=report,
            recorded_at_utc=valid_until + timedelta(microseconds=1),
        )


def test_ledger_write_path_is_confined_to_research_namespace(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    valid = root / "data" / "research" / "qlib_v2" / "ledger.json"
    _validate_ledger_path(root, valid)

    with pytest.raises(RuntimeError, match="outside_research_root"):
        _validate_ledger_path(root, root / "data" / "runtime" / "ledger.json")
    with pytest.raises(RuntimeError, match="must_be_json"):
        _validate_ledger_path(root, root / "data" / "research" / "qlib_v2" / "ledger.txt")


def test_observer_module_has_no_execution_writer_or_private_exchange_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden_prefixes = (
        "freqtrade.persistence",
        "freqtrade.exchange",
        "smartcrypto.execution.order_manager",
        "smartcrypto.risk.risk_manager",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
        "smartcrypto.execution.decision_ledger_v4_2.writer",
    )
    assert not any(
        any(name.startswith(prefix) for prefix in forbidden_prefixes)
        for name in imports
    )
