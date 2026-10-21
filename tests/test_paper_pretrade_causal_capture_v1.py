"""All market packets, clocks and financial outcomes here are synthetic fixtures."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    seal_decision_record,
    seal_trade_link_record,
)
from smartcrypto.research.pretrade_causal_capture.archive import EvidenceArchive, coverage
from smartcrypto.research.pretrade_causal_capture.capture import CaptureConfig, CaptureSession, Tick
from smartcrypto.research.pretrade_causal_capture.contracts import (
    CaptureBody,
    CaptureRecord,
    ClockProof,
    LifecycleBody,
    LifecycleRecord,
    SourceReceipt,
    digest,
    parse_record,
    seal_capture,
    seal_lifecycle,
)

ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class FixtureClock:
    def __init__(self) -> None:
        self.at = BASE
        self.ns = 0

    def now(self) -> Tick:
        self.at += timedelta(milliseconds=100)
        self.ns += 100_000_000
        return Tick(self.at, self.ns)


def proof(**changes: Any) -> ClockProof:
    return ClockProof.model_validate(
        {
            "reference_sha256": "a" * 64,
            "measured_at_utc": BASE - timedelta(minutes=1),
            "valid_until_utc": BASE + timedelta(hours=1),
            "error_bound_seconds": 0.001,
            "synthetic": True,
            **changes,
        }
    )


def session(**changes: Any) -> CaptureSession:
    return CaptureSession(
        CaptureConfig(**{"enabled": True, "epoch_id": "fixture-epoch-1", **changes}),
        clock=FixtureClock(),
        proof=proof(),
    )


def packet(s: CaptureSession, **changes: Any) -> SourceReceipt:
    value = s.receive_source(
        source_id="fixture-market",
        payload=b"synthetic closed candle",
        event_time_utc=changes.pop("event_time_utc", BASE - timedelta(minutes=1)),
        is_candle=True,
        candle_closed_at_utc=changes.pop("candle_closed_at_utc", BASE),
        **changes,
    )
    assert value is not None
    return value


def capture(
    s: CaptureSession, source: SourceReceipt | None = None, **changes: Any
) -> CaptureRecord:
    receipt = source or packet(s)
    value = s.capture(
        **{
            "opportunity_id": "candidate_000001",
            "candidate_id": "candidate_000001",
            "signal_id": "signal_000001",
            "symbol": "BTCUSDT",
            "side": "long",
            "feature_names": ("ret_1", "volume"),
            "feature_values": (0.12345678901234568, -0.0),
            "feature_sources": ((receipt.source_id,), (receipt.source_id,)),
            "sources": (receipt,),
            "feature_contract_version": "unified_feature_contract_v1",
            "model_id": "qlib_lgbm_ranker",
            "model_sha256": "b" * 64,
            "configuration_sha256": "c" * 64,
            **changes,
        }
    )
    assert value is not None, s.report()
    return value


def decision(**changes: Any) -> DecisionRecordV42:
    body = json.loads(
        (ROOT / "tests/fixtures/decision_ledger_v4_2/valid_decision.json").read_text()
    )
    body.pop("payload_sha256")
    body.update(changes)
    return seal_decision_record(body)


def decide(s: CaptureSession, c: CaptureRecord, **changes: Any) -> LifecycleRecord:
    assert isinstance(s.clock, FixtureClock)
    s.clock.at = BASE + timedelta(seconds=6)
    value = s.transition(c, decision=decision(**changes))
    assert value is not None, s.report()
    return value


def test_complete_original_vector_is_captured_before_model_decision() -> None:
    s = session()
    c = capture(s)
    seen = tuple(c.feature_values)
    d = decide(s, c)
    assert seen == (0.12345678901234568, -0.0)
    assert math.copysign(1, seen[1]) == -1
    assert c.vector_sha256 == digest(
        {"names": c.feature_names, "float64_hex": [v.hex() for v in seen]}
    )
    assert c.captured_at_utc < d.decision_timestamp_utc
    assert d.eligibility == "CAUSALLY_COMPLETE"
    assert coverage(s.records(), include_synthetic=True)["totals"]["causally_complete"] == 1
    assert not coverage(s.records())["totals"].get("opportunities", 0)


def test_full_lifecycle_uses_sealed_exact_links_and_no_missing_outcome_zero() -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    assert d.decision is not None
    before = d.decision.model_dump_json()
    link = seal_trade_link_record(
        {
            "event_id": "fixture-link",
            "parent_event_id": d.decision.event_id,
            "signal_id": d.decision.signal_id,
            "candidate_id": d.decision.candidate_id,
            "trade_id": 676,
            "correlation_id": d.decision.correlation_id,
            "idempotency_key": "fixture-link-676",
            "pair": d.decision.pair,
            "symbol": d.decision.symbol,
            "side": d.decision.side,
            "decision_timestamp": d.decision.decision_timestamp,
            "execution_timestamp": BASE + timedelta(seconds=6),
            "decision_payload_sha256": d.decision.payload_sha256,
            "link_reason": "synthetic_fixture",
        }
    )
    t = s.transition(c, decision=d.decision, trade_link=link)
    assert t is not None and t.eligibility == "CAUSALLY_COMPLETE"
    pending = coverage(s.records(), include_synthetic=True)
    assert pending["totals"]["outcome_unknown"] == 1
    assert "net_pnl" not in json.dumps(pending)
    assert isinstance(s.clock, FixtureClock)
    s.clock.at = BASE + timedelta(minutes=10)
    o = s.transition(
        c,
        decision=d.decision,
        trade_link=link,
        close_time_utc=BASE + timedelta(minutes=5),
        reported_net_pnl=1.25,
        financial_source_sha256="d" * 64,
    )
    assert o is not None and o.eligibility == "CAUSALLY_COMPLETE"
    assert o.financial_certified is False
    assert coverage(s.records(), include_synthetic=True)["totals"]["outcome_known"] == 1
    assert d.decision.model_dump_json() == before


@pytest.mark.parametrize("kind", ["allow", "risk_rejected", "selector_rejected"])
def test_only_rejections_observed_at_boundary_are_counted(kind: str) -> None:
    s = session()
    c = capture(s)
    if kind == "selector_rejected":
        value = s.transition(c, selector_rejected=True)
    elif kind == "risk_rejected":
        value = decide(
            s,
            c,
            risk_decision="REJECTED",
            risk_reasons=["fixture_limit"],
            final_decision="BLOCK",
            final_reasons=["fixture_limit"],
            approved_stake_usdt=0.0,
            approved_leverage=0.0,
        )
    else:
        value = decide(s, c)
    assert value is not None and value.stage == kind
    report = coverage(s.records(), include_synthetic=True)
    assert report["totals"][kind] == 1
    assert report["pre_boundary_rejections_proven"] is False


@pytest.mark.parametrize(
    "reason,clock_changes,packet_changes",
    [
        ("SOURCE_FUTURE", {}, {"event_time_utc": BASE + timedelta(seconds=30)}),
        ("CANDLE_NOT_CLOSED", {}, {"candle_closed_at_utc": BASE + timedelta(minutes=5)}),
        ("CLOCK_SKEW", {"error_bound_seconds": 0.2}, {}),
        ("CLOCK_UNPROVEN", {"valid_until_utc": BASE}, {}),
        ("CLOCK_UNPROVEN", {"measured_at_utc": BASE + timedelta(seconds=1)}, {}),
    ],
)
def test_bad_temporal_evidence_is_quarantined(
    reason: str, clock_changes: dict[str, Any], packet_changes: dict[str, Any]
) -> None:
    s = session()
    s.proof = proof(**clock_changes)
    c = capture(s, packet(s, **packet_changes))
    assert c.eligibility == "QUARANTINED"
    assert reason in c.quarantine_reasons
    d = decide(s, c)
    assert d.eligibility == "QUARANTINED"
    assert coverage(s.records(), include_synthetic=True)["totals"]["causally_complete"] == 0


def test_late_capture_cannot_repair_earlier_decision() -> None:
    s = session()
    assert isinstance(s.clock, FixtureClock)
    s.clock.at = BASE + timedelta(seconds=10)
    c = capture(s)
    value = s.transition(c, decision=decision())
    assert value is not None and "LATE_CAPTURE" in value.quarantine_reasons


def test_future_decision_is_quarantined() -> None:
    s = session()
    c = capture(s)
    value = s.transition(c, decision=decision())
    assert value is not None and "DECISION_FUTURE" in value.quarantine_reasons


def test_clock_regression_latches_and_does_not_invalidate_trading() -> None:
    s = session()
    source = packet(s)
    assert isinstance(s.clock, FixtureClock)
    s.clock.at -= timedelta(seconds=1)
    c = capture(s, source)
    assert "CLOCK_REGRESSION" in c.quarantine_reasons


def test_receipt_from_another_session_is_untrusted() -> None:
    source = packet(session())
    c = capture(session(), source)
    assert "SOURCE_UNTRUSTED" in c.quarantine_reasons


def test_missing_clock_or_source_is_not_default_clear() -> None:
    s = session()
    s.proof = None
    c = capture(s, feature_sources=(("missing",), ("missing",)))
    assert set(c.quarantine_reasons) >= {"CLOCK_UNPROVEN", "SOURCE_MISSING"}


def test_incomplete_vector_is_explicit_quarantine() -> None:
    c = capture(session(), feature_values=(0.1,))
    assert c.eligibility == "QUARANTINED" and "VECTOR_INCOMPLETE" in c.quarantine_reasons


def test_stale_source_is_not_refreshed_by_later_capture() -> None:
    s = session()
    source = packet(s)
    assert isinstance(s.clock, FixtureClock)
    s.clock.at += timedelta(minutes=6)
    assert "SOURCE_STALE" in capture(s, source).quarantine_reasons


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "wrong"),
        ("signal_id", "wrong"),
        ("symbol", "ETHUSDT"),
        ("side", "short"),
        ("model_hash", "d" * 64),
    ],
)
def test_identity_mismatch_never_becomes_causal(field: str, value: str) -> None:
    s = session()
    c = capture(s)
    d = decide(s, c, **{field: value})
    assert "IDENTITY_MISMATCH" in d.quarantine_reasons


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf"), True, "0.1"])
def test_invalid_original_values_are_rejected_without_logging_payload(
    bad_value: Any, caplog: pytest.LogCaptureFixture
) -> None:
    s = session()
    source = packet(s)
    with pytest.raises(AssertionError):
        capture(s, source, feature_values=(bad_value, 0.2))
    assert s.report()["evidence_valid"] is False
    assert "invalid_capture_contract" in caplog.text


@pytest.mark.parametrize(
    "name",
    ["api_key", "secret_token", "net_pnl", "exit_reason", "feature_mfe", "realized_duration"],
)
def test_sensitive_and_post_outcome_fields_are_forbidden(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(AssertionError):
        capture(session(), feature_names=(name, "volume"))
    assert name not in caplog.text


def test_tampered_receipt_or_record_fails_hash_validation() -> None:
    c = capture(session())
    payload = c.model_dump(mode="json")
    payload["feature_values"][0] = 0.9
    with pytest.raises(ValidationError):
        parse_record(payload)
    receipt = c.sources[0].model_dump(mode="json")
    receipt["available_at_utc"] = "2026-07-20T11:00:00Z"
    with pytest.raises(ValidationError, match="source_receipt_seal_mismatch"):
        SourceReceipt.model_validate(receipt)


def test_default_disabled_has_no_clock_queue_network_or_filesystem_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("side effect")

    s = CaptureSession(clock=type("NoClock", (), {"now": forbidden})())
    monkeypatch.setattr(Path, "mkdir", forbidden)
    assert s.receive_source(source_id="bad", payload=b"x", event_time_utc=BASE) is None
    assert (
        s.capture(
            opportunity_id="x",
            symbol="X",
            side="long",
            feature_names=(),
            feature_values=(),
            feature_sources=(),
            sources=(),
            feature_contract_version="x",
            model_id="x",
            model_sha256="invalid",
            configuration_sha256="invalid",
        )
        is None
    )
    assert s.report()["status"] == "disabled"
    assert not s.records() and not list(tmp_path.iterdir())


def test_no_runtime_hook_or_ledger_contract_change() -> None:
    for name in [
        "smartcrypto/execution/signal_producer.py",
        "smartcrypto/qlib_engine/predictor.py",
        "smartcrypto/execution/signal_risk_gate.py",
    ]:
        assert "pretrade_causal_capture" not in (ROOT / name).read_text(encoding="utf-8")
    assert "decision_event_id" not in CaptureRecord.model_fields
    assert "available_at_utc" not in DecisionRecordV42.model_fields


def archive(tmp_path: Path, **kwargs: Any) -> EvidenceArchive:
    return EvidenceArchive(
        tmp_path / "external", authorized=True, forbidden_roots=(ROOT,), **kwargs
    )


def test_append_only_restart_idempotency_and_epoch_isolation(tmp_path: Path) -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    a = archive(tmp_path)
    assert a.drain(s) == 2
    before = {p: p.read_bytes() for p in a.root.glob("*/*.json")}
    restarted = archive(tmp_path)
    assert restarted.persist(c) is False
    assert restarted.persist(d) is False
    other = capture(session(epoch_id="fixture-epoch-2"))
    assert restarted.persist(other) is True
    assert len(restarted.read()) == 3
    assert all(p.read_bytes() == value for p, value in before.items())


def test_persisted_identity_conflict_never_overwrites(tmp_path: Path) -> None:
    a = archive(tmp_path)
    first = capture(session())
    second = capture(session(), feature_values=(0.9, 0.1))
    assert a.persist(first)
    with pytest.raises(ValueError, match="persisted_identity_conflict"):
        a.persist(second)
    assert a.read() == (first,)


def test_input_order_and_duplicate_records_do_not_change_coverage() -> None:
    s = session()
    c = capture(s)
    decide(s, c)
    a = coverage(s.records(), include_synthetic=True)
    b = coverage(tuple(reversed(s.records())) + s.records(), include_synthetic=True)
    assert a == b
    orphan = coverage(
        tuple(r for r in s.records() if isinstance(r, LifecycleRecord)), include_synthetic=True
    )
    assert orphan["status"] == "blocked"


def test_queue_saturation_is_nonblocking_and_cannot_change_sealed_decision() -> None:
    s = session(queue_records=1)
    c = capture(s)
    original = decision()
    before = original.model_dump_json()
    assert isinstance(s.clock, FixtureClock)
    s.clock.at = BASE + timedelta(seconds=6)
    assert s.transition(c, decision=original) is None
    assert s.report()["metrics"]["queue_saturated"] == 1
    assert original.model_dump_json() == before


def test_writer_failure_is_explicit_and_does_not_run_in_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    a = archive(tmp_path)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("sensitive operating system detail")

    monkeypatch.setattr(a, "persist", broken)
    assert a.drain(s) == 0
    assert s.report()["status"] == "blocked"
    assert s.report()["metrics"]["archive_persistence_failed"] == 1
    assert d.decision == decision()


def test_external_authorization_and_capacity_limits(tmp_path: Path) -> None:
    c = capture(session())
    denied = EvidenceArchive(tmp_path / "denied", forbidden_roots=(ROOT,))
    with pytest.raises(ValueError, match="authorization"):
        denied.persist(c)
    assert not denied.root.exists()
    a = archive(tmp_path, max_records=1)
    assert a.persist(c)
    with pytest.raises(ValueError, match="budget"):
        a.persist(capture(session(epoch_id="other-epoch")))


def test_reader_detects_tampering_and_disallows_git_root(tmp_path: Path) -> None:
    a = archive(tmp_path)
    a.persist(capture(session()))
    target = next(a.root.glob("*/*.json"))
    payload = json.loads(target.read_text())
    payload["configuration_sha256"] = "e" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        a.read()
    with pytest.raises(ValueError, match="git|project"):
        EvidenceArchive(ROOT / "data/forbidden", authorized=True, forbidden_roots=(ROOT,)).persist(
            capture(session())
        )


def test_source_and_opportunity_budgets_are_finite() -> None:
    s = session(max_sources=1)
    packet(s)
    assert s.receive_source(source_id="second", payload=b"x", event_time_utc=BASE) is None
    s = session(max_opportunities=1)
    capture(s)
    with pytest.raises(AssertionError):
        capture(s, opportunity_id="other-candidate")
    assert s.report()["metrics"]["opportunity_budget_exceeded"] == 1


def test_cli_default_reads_no_sources_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.audit_paper_pretrade_causal_capture_v1 import main

    assert main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == "explicit_archive_not_supplied"
    assert report["write_performed"] is False


def link_for(d: DecisionRecordV42, **changes: Any) -> Any:
    return seal_trade_link_record(
        {
            "event_id": "fixture-link",
            "parent_event_id": d.event_id,
            "signal_id": d.signal_id,
            "candidate_id": d.candidate_id,
            "trade_id": 676,
            "correlation_id": d.correlation_id,
            "idempotency_key": "fixture-link-676",
            "pair": d.pair,
            "symbol": d.symbol,
            "side": d.side,
            "decision_timestamp": d.decision_timestamp,
            "execution_timestamp": BASE + timedelta(seconds=6),
            "decision_payload_sha256": d.payload_sha256,
            "link_reason": "synthetic_fixture",
            **changes,
        }
    )


def reseal(
    record: CaptureRecord | LifecycleRecord, **changes: Any
) -> CaptureRecord | LifecycleRecord:
    data = {**record.model_dump(exclude={"record_sha256"}), **changes}
    if isinstance(record, CaptureRecord):
        return seal_capture(CaptureBody.model_validate(data))
    return seal_lifecycle(LifecycleBody.model_validate(data))


@pytest.mark.parametrize("field", ["candidate_id", "signal_id"])
def test_missing_ids_are_not_inferred_from_opportunity(field: str) -> None:
    s = session()
    c = capture(s, **{field: None})
    d = decide(s, c)
    assert "IDENTITY_MISMATCH" in d.quarantine_reasons
    assert s.report()["evidence_valid"] is False


@pytest.mark.parametrize(
    "field,value",
    [("model_id", "different-model"), ("feature_contract_version", "different-contract")],
)
def test_model_and_feature_contract_identity_must_match(field: str, value: str) -> None:
    s = session()
    assert "IDENTITY_MISMATCH" in decide(s, capture(s), **{field: value}).quarantine_reasons


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "different-candidate"),
        ("signal_id", "different-signal"),
        ("correlation_id", "different-correlation"),
        ("pair", "ETH/USDT:USDT"),
        ("symbol", "ETHUSDT"),
        ("side", "short"),
        ("parent_event_id", "different-event"),
        ("decision_payload_sha256", "f" * 64),
        ("decision_timestamp", BASE + timedelta(seconds=4)),
    ],
)
def test_trade_link_exact_identity_is_checked_by_session_and_reader(field: str, value: Any) -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    assert d.decision is not None
    wrong = link_for(d.decision, **{field: value})
    t = s.transition(c, decision=d.decision, trade_link=wrong)
    assert t is not None and "IDENTITY_MISMATCH" in t.quarantine_reasons
    # Even a validly sealed claim with stripped reasons cannot bypass reader validation.
    forged = reseal(t, eligibility="CAUSALLY_COMPLETE", quarantine_reasons=())
    result = coverage((c, d, forged), include_synthetic=True)
    assert result["status"] == "blocked"
    assert result["totals"].get("trade_linked", 0) == 0


def test_outcome_cannot_attach_a_different_sealed_decision() -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    assert d.decision is not None
    t = s.transition(c, decision=d.decision, trade_link=link_for(d.decision))
    assert t is not None
    assert isinstance(s.clock, FixtureClock)
    s.clock.at = BASE + timedelta(minutes=10)
    o = s.transition(
        c,
        decision=d.decision,
        trade_link=t.trade_link,
        close_time_utc=BASE + timedelta(minutes=5),
        reported_net_pnl=-1.0,
        financial_source_sha256="e" * 64,
    )
    assert o is not None
    forged = reseal(o, decision=decision(event_id="another-sealed-event"))
    result = coverage((c, d, t, forged), include_synthetic=True)
    assert result["status"] == "blocked"
    assert result["totals"].get("outcome_known", 0) == 0


def test_missing_parent_or_trade_link_cannot_produce_outcome() -> None:
    s = session()
    c = capture(s)
    assert session().transition(c, decision=decision()) is None
    assert s.transition(c, decision=decision(), trade_link=link_for(decision())) is None
    decide(s, c)
    assert (
        s.transition(
            c,
            decision=decision(),
            trade_link=link_for(decision()),
            close_time_utc=BASE + timedelta(seconds=6),
            reported_net_pnl=1.0,
            financial_source_sha256="e" * 64,
        )
        is None
    )


def test_future_execution_is_not_a_valid_trade_link() -> None:
    s = session()
    c = capture(s)
    d = decide(s, c)
    assert d.decision is not None
    t = s.transition(
        c,
        decision=d.decision,
        trade_link=link_for(d.decision, execution_timestamp=BASE + timedelta(minutes=5)),
    )
    assert t is not None and "DECISION_FUTURE" in t.quarantine_reasons


def test_reused_lineage_ids_never_double_count_causal_opportunities() -> None:
    s = session()
    c = capture(s)
    with pytest.raises(AssertionError):
        capture(s, opportunity_id="another-opportunity")
    assert s.report()["metrics"]["lineage_identity_conflict"] == 1
    alias = reseal(c, opportunity_id="another-opportunity")
    result = coverage((c, alias), include_synthetic=True)
    assert result["totals"]["quarantined"] == 2
    assert result == coverage((alias, c), include_synthetic=True)


def test_identity_conflict_report_is_deterministic_under_reordered_arrival() -> None:
    first = capture(session())
    second = reseal(first, configuration_sha256="e" * 64)
    a = coverage((first, second), include_synthetic=True)
    assert a == coverage((second, first), include_synthetic=True)
    assert a["status"] == "blocked"


@pytest.mark.parametrize(
    "bad_time", [None, datetime(2026, 7, 20), "invalid", "2026-07-20T12:00:00-03:00"]
)
def test_invalid_or_absent_source_timestamp_fails_closed(bad_time: Any) -> None:
    s = session()
    assert (
        s.receive_source(source_id="fixture", payload=b"fixture", event_time_utc=bad_time) is None
    )
    assert s.report()["evidence_valid"] is False


def test_source_availability_cannot_be_supplied_or_overwritten_by_caller() -> None:
    s = session()
    with pytest.raises(TypeError):
        s.receive_source(
            source_id="fixture", payload=b"fixture", event_time_utc=BASE, available_at_utc=BASE
        )  # type: ignore[call-arg]
    receipt = packet(s)
    assert receipt.available_at_utc > BASE
    with pytest.raises(ValidationError):
        receipt.available_at_utc = BASE  # type: ignore[misc]


def test_synthetic_clock_cannot_be_relabelled_as_real_coverage() -> None:
    with pytest.raises(AssertionError):
        capture(session(synthetic=False))
    with pytest.raises(ValidationError, match="explicit_epoch_required"):
        CaptureConfig(enabled=True)


def test_bounded_source_and_record_bytes_are_explicit() -> None:
    s = session(max_source_bytes=1)
    assert s.receive_source(source_id="fixture", payload=b"too big", event_time_utc=BASE) is None
    assert s.report()["metrics"]["source_budget_exceeded"] == 1
    s = session(max_record_bytes=1024)
    with pytest.raises(AssertionError):
        capture(s)
    assert s.report()["metrics"]["record_byte_budget_exceeded"] == 1


def test_writer_deadline_does_not_retry_or_block_producer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from smartcrypto.research.pretrade_causal_capture import archive as module

    s = session()
    c = capture(s)
    decide(s, c)
    clock = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    persisted = []
    a = archive(tmp_path)

    def persist_fixture(record: CaptureRecord | LifecycleRecord) -> bool:
        persisted.append(record)
        return True

    monkeypatch.setattr(a, "persist", persist_fixture)
    assert a.drain(s, deadline_seconds=0.1) == 1
    assert persisted == [c]
    assert s.report()["metrics"]["writer_deadline_exceeded"] == 1
    assert s.report()["queued_records"] == 1


def test_io_error_message_never_discloses_raw_exception_or_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = session()
    capture(s)
    a = archive(tmp_path)

    def broken(record: Any) -> bool:
        raise PermissionError("DO_NOT_DISCLOSE_FIXTURE_DETAIL")

    monkeypatch.setattr(a, "persist", broken)
    assert a.drain(s) == 0
    assert "DO_NOT_DISCLOSE_FIXTURE_DETAIL" not in caplog.text
    assert "archive_persistence_failed" in caplog.text


def test_disabled_archive_drain_never_creates_directories(tmp_path: Path) -> None:
    a = archive(tmp_path)
    assert a.drain(CaptureSession()) == 0
    assert a.write_performed is False and not a.root.exists()


def test_standalone_cli_from_other_cwd_is_no_write(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/audit_paper_pretrade_causal_capture_v1.py"),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 2 and result.stderr == ""
    assert json.loads(result.stdout)["write_performed"] is False
    assert not list(tmp_path.iterdir())


def test_cli_only_reads_explicit_fixture_archive_and_excludes_synthetic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.audit_paper_pretrade_causal_capture_v1 import main

    a = archive(tmp_path)
    a.persist(capture(session()))
    before = {p: p.read_bytes() for p in a.root.glob("*/*.json")}
    assert main(["--archive-root", str(a.root), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "no_data" and result["synthetic_records_excluded"] == 1
    assert result["real_prospective_collection_proven"] is False
    assert all(p.read_bytes() == value for p, value in before.items())


def test_disabled_mode_starts_no_threads_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import threading

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected network or worker")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    s = CaptureSession()
    assert s.receive_source(source_id="fixture", payload=b"fixture", event_time_utc=BASE) is None
    assert s.report()["status"] == "disabled" and not s.records()
