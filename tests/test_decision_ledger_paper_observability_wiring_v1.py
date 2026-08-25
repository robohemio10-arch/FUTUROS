from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.audit_decision_ledger_paper_observability_wiring_v1 import (
    build_audit_report,
)
from scripts.validate_decision_ledger_paper_observability_wiring_v1 import (
    build_report as build_validator_report,
)
from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    CANONICAL_INDEX_PATH,
    CriticalIdempotencyConflict,
    IdempotentDecisionLedgerRuntimeSink,
    PaperObservabilityWiringConfigV1,
    PersistentIndexError,
    finalize_after_risk_manager,
    load_observability_config,
    prepare_before_risk_manager,
    sync_phase14_trade_links_readonly,
)
from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    CANONICAL_ALLOWED_ROOT,
    PaperRuntimeWriterProfileV1,
    RuntimeIdentityEvidenceV1,
    create_paper_runtime_writer,
    run_writer_preflight,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeDecisionProjectionV1,
)
from smartcrypto.execution.signal_risk_gate import RiskGateResult

ROOT = Path(__file__).resolve().parents[1]
MODEL_HASH = "a" * 64
RISK_HASH = "b" * 64


def _identity() -> RuntimeIdentityEvidenceV1:
    return RuntimeIdentityEvidenceV1(
        source="test",
        verified=True,
        elevated=False,
        effective_uid=1000,
        reason="non_root_identity_verified",
    )


def _writer_profile() -> PaperRuntimeWriterProfileV1:
    return PaperRuntimeWriterProfileV1(
        activation_state="preflight_only",
        enabled=True,
        runtime_write_authorized=True,
    )


def _enabled_config(*, trade_link_enabled: bool = False) -> PaperObservabilityWiringConfigV1:
    return PaperObservabilityWiringConfigV1(
        enabled=True,
        writer_enabled=True,
        trade_link_enabled=trade_link_enabled,
        model_hash=MODEL_HASH,
        writer_profile=_writer_profile(),
    )


def _candidate(symbol: str, side: str, score: float) -> dict[str, object]:
    base = symbol.removesuffix("USDT")
    return {
        "pair": f"{base}/USDT:USDT",
        "symbol": symbol,
        "side": side,
        "score": score,
        "generated_at": "2026-07-21T12:00:00Z",
        "valid_until": "2026-07-21T12:30:00Z",
        "max_position_usdt": 50.0,
        "leverage": 2.0,
        "model_version": "qlib-test-v1",
    }


def _risk_gate(prepared_signals: tuple[dict[str, object], ...]) -> RiskGateResult:
    approved = dict(prepared_signals[0])
    approved.update(
        {
            "risk_approved": True,
            "risk_reasons": [],
            "risk_checked_at_utc": "2026-07-21T12:00:01Z",
            "risk_policy_id": "risk-limits:test",
            "risk_config_hash": RISK_HASH,
            "risk_manager_source": "test-risk-manager",
        }
    )
    rejected = dict(prepared_signals[1])
    rejected.update(
        {
            "risk_approved": False,
            "risk_reasons": ["test_rejection"],
            "risk_checked_at_utc": "2026-07-21T12:00:01Z",
            "risk_policy_id": "risk-limits:test",
            "risk_config_hash": RISK_HASH,
            "risk_manager_source": "test-risk-manager",
        }
    )
    return RiskGateResult(
        status="ok",
        reason=None,
        risk_manager_available=True,
        risk_manager_source="test-risk-manager",
        risk_limits_path="tmp/risk.yml",
        risk_config_hash=RISK_HASH,
        signals_submitted=2,
        signals_approved=1,
        signals_rejected=1,
        approved_signals=[approved],
        rejected_signals=[rejected],
    )


def _prepare_root(tmp_path: Path) -> Path:
    allowed_root = tmp_path / Path(*CANONICAL_ALLOWED_ROOT.split("/"))
    allowed_root.mkdir(parents=True)
    return allowed_root


def _prepared_enabled(tmp_path: Path, *, trade_link_enabled: bool = False):
    _prepare_root(tmp_path)
    config = _enabled_config(trade_link_enabled=trade_link_enabled)
    prepared = prepare_before_risk_manager(
        [
            _candidate("BTCUSDT", "long", 0.9),
            _candidate("ETHUSDT", "short", -0.8),
        ],
        producer_id="test-producer",
        config_source=config,
    )
    return config, prepared, _risk_gate(prepared.signals)


def test_default_config_is_disabled_and_immutable() -> None:
    config = PaperObservabilityWiringConfigV1()

    assert config.enabled is False
    assert config.writer_enabled is False
    assert config.trade_link_enabled is False
    assert config.writer_profile.enabled is False
    assert config.safety_flags.sends_orders is False
    assert config.safety_flags.exchange_private_access is False
    assert config.safety_flags.changes_risk is False
    with pytest.raises(ValidationError):
        config.enabled = True  # type: ignore[misc]


def test_enabled_config_requires_writer_and_authoritative_model_hash() -> None:
    with pytest.raises(ValidationError, match="writer_enabled"):
        PaperObservabilityWiringConfigV1(enabled=True)
    with pytest.raises(ValidationError, match="authoritative_model_hash"):
        PaperObservabilityWiringConfigV1(
            enabled=True,
            writer_enabled=True,
            writer_profile=_writer_profile(),
        )


def test_disabled_preparation_preserves_candidate_payload() -> None:
    candidates = [_candidate("BTCUSDT", "long", 0.9)]
    prepared = prepare_before_risk_manager(
        candidates,
        producer_id="test-producer",
        config_source=PaperObservabilityWiringConfigV1(),
    )

    assert prepared.enabled is False
    assert prepared.lineage_built_before_risk_manager is False
    assert prepared.signals == tuple(candidates)


def test_enabled_preparation_builds_deterministic_lineage_before_risk() -> None:
    config = _enabled_config()
    candidates = [_candidate("BTCUSDT", "long", 0.9)]

    first = prepare_before_risk_manager(
        candidates,
        producer_id="test-producer",
        config_source=config,
    )
    second = prepare_before_risk_manager(
        candidates,
        producer_id="test-producer",
        config_source=config,
    )

    assert first == second
    assert first.lineage_built_before_risk_manager is True
    signal = first.signals[0]
    assert signal["signal_id"]
    assert signal["candidate_id"]
    assert signal["correlation_id"]
    assert signal["feature_hash"]
    assert signal["model_hash"] == MODEL_HASH
    assert "risk_approved" not in signal


def test_disabled_finalize_never_invokes_writer_or_changes_paper_behavior() -> None:
    prepared = prepare_before_risk_manager(
        [_candidate("BTCUSDT", "long", 0.9), _candidate("ETHUSDT", "short", -0.8)],
        producer_id="test-producer",
        config_source=PaperObservabilityWiringConfigV1(),
    )
    risk_gate = _risk_gate(prepared.signals)

    outcome = finalize_after_risk_manager(prepared, risk_gate=risk_gate)

    assert outcome.active_signals == risk_gate.approved_signals
    assert outcome.report.status == "disabled"
    assert outcome.report.writer_invoked is False
    assert outcome.report.writes_runtime is False
    assert outcome.report.paper_behavior_changed is False
    assert outcome.report.publication_blocked is False


def test_enabled_flow_persists_before_returning_enveloped_signal(tmp_path: Path) -> None:
    _config, prepared, risk_gate = _prepared_enabled(tmp_path)

    outcome = finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc),
        project_root=tmp_path,
        identity=_identity(),
    )

    assert outcome.report.status == "ok"
    assert outcome.report.persisted_decision_count == 2
    assert outcome.report.active_envelope_count == 1
    assert outcome.report.writer_invoked is True
    assert outcome.report.writes_runtime is True
    assert outcome.report.paper_behavior_changed is False
    envelope = outcome.active_signals[0]["decision_ledger"]
    assert isinstance(envelope, dict)
    assert envelope["decision_event_id"]
    ledger = tmp_path / "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    index = tmp_path / Path(*CANONICAL_INDEX_PATH.split("/"))
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2
    assert index.is_file()


def test_same_key_and_hash_is_duplicate_without_append(tmp_path: Path) -> None:
    _config, prepared, risk_gate = _prepared_enabled(tmp_path)
    timestamp = datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc)

    first = finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=timestamp,
        project_root=tmp_path,
        identity=_identity(),
    )
    second = finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=timestamp,
        project_root=tmp_path,
        identity=_identity(),
    )

    ledger = tmp_path / "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    assert first.report.persisted_decision_count == 2
    assert second.report.persisted_decision_count == 0
    assert second.report.duplicate_decision_count == 2
    assert second.report.writes_runtime is False
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_same_key_with_different_hash_is_critical_conflict(tmp_path: Path) -> None:
    _config, prepared, risk_gate = _prepared_enabled(tmp_path)
    timestamp = datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc)
    finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=timestamp,
        project_root=tmp_path,
        identity=_identity(),
    )
    writer_profile = _writer_profile()
    preflight = run_writer_preflight(
        project_root=tmp_path,
        profile=writer_profile,
        identity=_identity(),
    )
    factory = create_paper_runtime_writer(profile=writer_profile, preflight=preflight)
    assert factory.writer is not None
    index_path = tmp_path / Path(*CANONICAL_INDEX_PATH.split("/"))
    sink = IdempotentDecisionLedgerRuntimeSink(
        writer=factory.writer,
        index_path=index_path,
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next(iter(index["entries"].values()))
    projection = RuntimeDecisionProjectionV1.model_validate(entry["projection"])
    entry["payload_sha256"] = "f" * 64
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(CriticalIdempotencyConflict):
        sink.append(projection)


def test_corrupted_persistent_index_blocks_without_memory_fallback(tmp_path: Path) -> None:
    _prepare_root(tmp_path)
    profile = _writer_profile()
    preflight = run_writer_preflight(
        project_root=tmp_path,
        profile=profile,
        identity=_identity(),
    )
    factory = create_paper_runtime_writer(profile=profile, preflight=preflight)
    assert factory.writer is not None
    index_path = tmp_path / Path(*CANONICAL_INDEX_PATH.split("/"))
    index_path.write_text("{invalid", encoding="utf-8")
    sink = IdempotentDecisionLedgerRuntimeSink(
        writer=factory.writer,
        index_path=index_path,
    )

    with pytest.raises(PersistentIndexError):
        sink.read_index()


def test_projection_failure_blocks_approved_publication(tmp_path: Path) -> None:
    _prepare_root(tmp_path)
    config = _enabled_config()
    prepared = prepare_before_risk_manager(
        [_candidate("BTCUSDT", "long", 0.9), _candidate("ETHUSDT", "short", -0.8)],
        producer_id="test-producer",
        config_source=config,
    )
    gate = _risk_gate(prepared.signals)
    gate.approved_signals[0].pop("feature_timestamp")

    outcome = finalize_after_risk_manager(
        prepared,
        risk_gate=gate,
        decision_timestamp=datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc),
        project_root=tmp_path,
        identity=_identity(),
    )

    assert outcome.report.status == "blocked"
    assert outcome.report.publication_blocked is True
    assert outcome.active_signals == []
    assert outcome.report.writer_invoked is False


def test_trade_link_adapter_disabled_does_not_read_missing_database(tmp_path: Path) -> None:
    report = sync_phase14_trade_links_readonly(
        snapshot_db=tmp_path / "missing.sqlite",
        project_root=tmp_path,
        config_source=PaperObservabilityWiringConfigV1(),
    )

    assert report.status == "disabled"
    assert report.writer_invoked is False
    assert report.writes_runtime is False
    assert report.writes_sqlite is False
    assert report.timestamp_only_matching_allowed is False
    assert report.automatic_replay_allowed is False


def test_phase14_trade_link_requires_explicit_decision_event_id(tmp_path: Path) -> None:
    config, prepared, risk_gate = _prepared_enabled(tmp_path, trade_link_enabled=True)
    decision_time = datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc)
    finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=decision_time,
        project_root=tmp_path,
        identity=_identity(),
    )
    database = _create_trade_db(tmp_path / "snapshot.sqlite", enter_tag="smartcrypto_long")

    report = sync_phase14_trade_links_readonly(
        snapshot_db=database,
        project_root=tmp_path,
        config_source=config,
        identity=_identity(),
    )

    assert report.status == "ok"
    assert report.source_trade_count == 1
    assert report.correlated_trade_count == 0
    assert report.projected_trade_link_count == 0
    assert report.writer_invoked is False


def test_phase14_trade_link_uses_explicit_id_and_is_idempotent(tmp_path: Path) -> None:
    config, prepared, risk_gate = _prepared_enabled(tmp_path, trade_link_enabled=True)
    decision_time = datetime(2026, 7, 21, 12, 0, 2, tzinfo=timezone.utc)
    outcome = finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        decision_timestamp=decision_time,
        project_root=tmp_path,
        identity=_identity(),
    )
    envelope = outcome.active_signals[0]["decision_ledger"]
    assert isinstance(envelope, dict)
    event_id = envelope["decision_event_id"]
    database = _create_trade_db(
        tmp_path / "snapshot.sqlite",
        enter_tag=f"smartcrypto_long|decision_event_id={event_id}",
    )

    first = sync_phase14_trade_links_readonly(
        snapshot_db=database,
        project_root=tmp_path,
        config_source=config,
        identity=_identity(),
    )
    second = sync_phase14_trade_links_readonly(
        snapshot_db=database,
        project_root=tmp_path,
        config_source=config,
        identity=_identity(),
    )

    assert first.status == "ok"
    assert first.persisted_trade_link_count == 1
    assert second.persisted_trade_link_count == 0
    assert second.duplicate_trade_link_count == 1
    assert database.is_file()


def test_three_producers_use_shared_coordinator_around_risk_manager() -> None:
    for relative in (
        "smartcrypto/execution/signal_producer.py",
        "smartcrypto/qlib_engine/signal_exporter.py",
        "smartcrypto/execution/signal_contract_guard.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        prepare = source.index("prepare_before_risk_manager(")
        risk = source.index("apply_risk_manager_gate(", prepare)
        finalize = source.index("finalize_after_risk_manager(", risk)
        assert prepare < risk < finalize
        assert "decision_ledger_paper_observability_wiring_v1" in source


def test_strategy_preserves_correlation_without_changing_trading_policy() -> None:
    source = (
        ROOT / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
    ).read_text(encoding="utf-8")

    assert "decision_event_id" in source
    assert "signal_id" in source
    assert "correlation_id" in source
    assert "def _write_decision" in source
    assert 'minimal_roi = {"0": 0.02}' in source
    assert "stoploss = -0.015" in source
    assert "return min(2.0, max_leverage)" in source


def test_validator_and_auditor_report_enabled_preflight_only(tmp_path: Path) -> None:
    config_path = tmp_path / "decision_ledger.yml"
    config_path.write_text(
        (ROOT / "config/decision_ledger_paper_observability.yml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    validator = build_validator_report(
        project_root=tmp_path,
        config_path=config_path,
    )
    auditor = build_audit_report(
        ROOT,
        ROOT / "config/decision_ledger_paper_observability.yml",
    )

    assert validator["status"] == "ok"
    assert validator["enabled"] is True
    assert validator["writer_invoked"] is False
    assert validator["writes_runtime"] is False
    assert validator["paper_behavior_changed"] is False
    assert auditor["status"] == "ok"
    assert all(item["wiring_order_valid"] for item in auditor["producer_checks"])


def test_versioned_config_enables_fail_closed_preflight_writer() -> None:
    config = load_observability_config(
        ROOT / "config/decision_ledger_paper_observability.yml"
    )

    assert config.enabled is True
    assert config.writer_enabled is True
    assert config.trade_link_enabled is False
    assert config.writer_profile.enabled is True
    assert config.writer_profile.activation_state == "preflight_only"


def _create_trade_db(path: Path, *, enter_tag: str) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY, pair TEXT, is_open INTEGER, is_short INTEGER, "
            "open_date TEXT, enter_tag TEXT)"
        )
        connection.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?)",
            (
                101,
                "BTC/USDT:USDT",
                0,
                0,
                "2026-07-21T12:00:03Z",
                enter_tag,
            ),
        )
    return path
