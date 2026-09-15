from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from smartcrypto.execution import signal_producer
from smartcrypto.execution.decision_ledger_v4_2.contracts import seal_decision_record
from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    finalize_after_risk_manager,
)
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.decision_projection import (
    CandidateLineageError,
    _require_utc_datetime,
)
from smartcrypto.learning.qlib_v3_prospective import natural_producer as producer
from smartcrypto.learning.qlib_v3_prospective.contracts import SAFETY

from test_decision_ledger_paper_observability_wiring_v1 import (
    MODEL_HASH,
    _identity,
    _prepared_enabled,
)
from test_qlib_v3_natural_evidence_producer_wiring_v1 import (
    _install_stubs,
    _producer_config,
    _source_row,
    closed_source,
    evidence_path,
    inputs,
    strict_source,
)
from test_qlib_v3_prospective_evidence_orchestrator_v1 import (
    BOUNDARY,
    NOW,
)

pytest_plugins = ("test_qlib_v3_prospective_evidence_orchestrator_v1",)


@pytest.fixture
def context(fixture, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(producer, "datetime", Clock)
    monkeypatch.setattr(producer, "CANONICAL", fixture["expected"])
    monkeypatch.delenv(producer.CONFIG_ENV, raising=False)
    config = {
        "enabled": True,
        "activation": str(fixture["activation_path"]),
        "freeze": str(fixture["freeze_path"]),
    }
    return fixture, config


@pytest.mark.parametrize(
    "relative,expected",
    [
        (
            "smartcrypto/execution/signal_producer.py",
            "3e81c7360b6c209aa189b24a1a52828167ad78fcb7ca70d2291992657a45f12f",
        ),
        (
            "smartcrypto/learning/paper_autolearning/live_feedback_loop.py",
            "d4f43985ac2a4680d6f24c2e47fd697362dfbcae0a87b640085b81d7de7eb26a",
        ),
        (
            "smartcrypto/execution/decision_ledger_paper_observability_wiring_v1/coordinator.py",
            "fc0af65059c7e165ef8f774d65dcea739fb015dfcd1bc1a1167f0de22c96d989",
        ),
    ],
)
def test_exact_recovered_runtime_source_parity(relative: str, expected: str) -> None:
    raw = (Path(__file__).resolve().parents[1] / relative).read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == expected


@pytest.mark.parametrize(
    "relative,expected",
    [
        (
            "smartcrypto/learning/qlib_v3_prospective/natural_producer.py",
            "619f81db163c9260597cf94ad4b45166540a61752430c5c5ec0c5f36bd71b136",
        ),
        (
            "smartcrypto/execution/paper_candidate_trade_lineage_propagation_v1/"
            "decision_projection.py",
            "ec3a8968ae4a7f2d20f0f4d76ec0ade0ee78a92df1799dbe02e24a17095370f9",
        ),
    ],
)
def test_recovered_source_integrity_after_deliberate_hardening(
    relative: str,
    expected: str,
) -> None:
    raw = (Path(__file__).resolve().parents[1] / relative).read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == expected


def test_model_mismatch_is_reported_without_rewriting_hash(context) -> None:
    kwargs = inputs(context)
    original = kwargs["decisions"][0].model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    original["model_hash"] = "bb998610" + "0" * 56
    decision = seal_decision_record(original)
    kwargs["decisions"] = [decision]
    kwargs["signals"][0]["decision_ledger"]["decision_payload_sha256"] = (
        decision.payload_sha256
    )
    before = copy.deepcopy(kwargs["signals"])

    report = producer.observe_signal_batch(**kwargs)

    assert report.reason == "decision_model_mismatch"
    assert report.status == "blocked"
    assert report.new_signal_count == 0
    assert decision.model_hash == original["model_hash"]
    assert kwargs["signals"] == before
    assert not evidence_path(context).exists()


@pytest.mark.parametrize("mode", ["live", "canary", "paper_candidate", "unknown"])
def test_nonpaper_mode_cannot_write_evidence(context, mode: str) -> None:
    kwargs = inputs(context)
    kwargs["runtime_mode"] = mode

    report = producer.observe_signal_batch(**kwargs)

    assert report.reason == "natural_paper_runtime_required"
    assert not report.write_performed
    assert not evidence_path(context).exists()


def test_default_disabled_and_safety_contract(context) -> None:
    kwargs = inputs(context)
    kwargs["config_source"] = None

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "disabled"
    assert not report.write_performed
    assert not evidence_path(context).exists()

    for flag in (
        "historical_backfill_allowed",
        "v2_evidence_imported",
        "operational_authority",
        "sends_orders",
        "paper_behavior_changed",
    ):
        assert report.to_dict()[flag] is False

    for flag in ("changes_risk", "live", "canary", "exchange_private_access"):
        assert SAFETY[flag] is False


@pytest.mark.parametrize("failure", ["model", "persistence", "config"])
def test_evidence_failure_preserves_publication_and_order(
    context,
    monkeypatch,
    failure: str,
) -> None:
    fixture_data, config = context
    monkeypatch.chdir(fixture_data["project_root"])
    instant = BOUNDARY + timedelta(seconds=2)
    monkeypatch.setattr(signal_producer, "utc_now", lambda: instant)

    model_hash = (
        "bb998610" + "0" * 56
        if failure == "model"
        else fixture_data["expected"].model_artifact_sha256
    )
    row = _source_row(
        **strict_source(
            model_hash=model_hash,
            signal_timestamp_utc=BOUNDARY.isoformat(),
            feature_timestamp_utc=(BOUNDARY - timedelta(minutes=1)).isoformat(),
        )
    )
    writes, _ = _install_stubs(monkeypatch, row)
    original_gate = signal_producer.apply_risk_manager_gate

    def risk_check(signals, **kwargs):
        result = original_gate(signals, **kwargs)
        for signal in result.approved_signals:
            signal["risk_checked_at_utc"] = instant.isoformat()
        return result

    monkeypatch.setattr(signal_producer, "apply_risk_manager_gate", risk_check)
    monkeypatch.setattr(
        signal_producer,
        "prepare_before_risk_manager",
        lambda signals, **kw: SimpleNamespace(signals=signals, enabled=False),
    )

    baseline = signal_producer.build_active_signals(_producer_config())
    assert baseline["signals_after"] == 1
    expected = copy.deepcopy(writes["pinned.json"])
    order = []
    original_observer = producer.observe_signal_batch

    def observe(**kwargs):
        order.append("observe")
        return original_observer(**kwargs)

    def publish(path, payload):
        if str(path) in ("primary.json", "pinned.json"):
            order.append(str(path))
        writes[str(path)] = copy.deepcopy(payload)

    def fail_persist(*args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(signal_producer, "observe_signal_batch", observe)
    monkeypatch.setattr(signal_producer, "atomic_write_json", publish)

    if failure == "persistence":
        monkeypatch.setattr(producer, "_persist", fail_persist)

    enabled = _producer_config()
    enabled["qlib_v3_natural_evidence"] = (
        config if failure != "config" else {"enabled": "true"}
    )

    report = signal_producer.build_active_signals(enabled)

    assert report["qlib_v3_natural_evidence"]["status"] == "blocked"
    assert report["qlib_v3_natural_evidence"]["reason"] == {
        "model": "decision_model_mismatch",
        "persistence": "producer_boundary_failed:OSError",
        "config": "producer_enabled_must_be_boolean",
    }[failure]
    assert report["qlib_v3_natural_evidence"]["new_signal_count"] == 0
    assert report["status"] == baseline["status"] == "ok"
    assert report["written_primary"]
    assert report["written_pinned"]
    assert order == ["observe", "primary.json", "pinned.json"]
    assert writes["pinned.json"] == expected
    assert writes["primary.json"] == expected
    assert row["model_hash"] == model_hash
    assert not evidence_path(context).exists()


def test_enabled_coordinator_exports_original_sealed_records(tmp_path: Path) -> None:
    _, prepared, risk_gate = _prepared_enabled(tmp_path)

    result = finalize_after_risk_manager(
        prepared,
        risk_gate=risk_gate,
        project_root=tmp_path,
        identity=_identity(),
        decision_timestamp=datetime(2026, 7, 21, 12, 0, 2, tzinfo=UTC),
    )

    assert result.report.status == "ok"
    assert len(result.decision_records) == 2
    assert {record.final_decision.value for record in result.decision_records} == {
        "ALLOW",
        "BLOCK",
    }

    envelope = result.active_signals[0]["decision_ledger"]
    original = next(
        record
        for record in result.decision_records
        if record.event_id == envelope["decision_event_id"]
    )
    assert original.payload_sha256 == envelope["decision_payload_sha256"]
    assert original.model_hash == MODEL_HASH


@pytest.mark.parametrize(
    "tz,reason",
    [
        (None, "strict_decision_timestamp_not_timezone_aware"),
        (timezone(timedelta(hours=-3)), "strict_decision_timestamp_not_utc"),
        (UTC, None),
    ],
)
def test_timestamp_narrowing_preserves_original_rejections(tz, reason) -> None:
    instant = datetime(2026, 9, 12, 13, 17, 28, 798985, tzinfo=tz)

    if reason:
        with pytest.raises(CandidateLineageError, match=reason):
            _require_utc_datetime(instant, "test")
    else:
        assert _require_utc_datetime(instant, "test") == instant


def test_sqlite_ids_are_bound_and_database_is_read_only(context, monkeypatch) -> None:
    kwargs = closed_source(context)
    path = kwargs["snapshot_db"]
    before = path.read_bytes()
    connect = producer.sqlite3.connect
    queries = []

    class Connection:
        def __init__(self, database, **options):
            assert database.endswith("?mode=ro")
            assert options["uri"] is True
            self.wrapped = connect(database, **options)
            self.row_factory = None

        def execute(self, query, parameters=()):
            queries.append((query, parameters))
            self.wrapped.row_factory = self.row_factory
            return self.wrapped.execute(query, parameters)

        def close(self):
            self.wrapped.close()

    monkeypatch.setattr(producer.sqlite3, "connect", Connection)

    rows = producer._trade_rows(path, [1])

    assert len(rows) == 1
    assert queries[0] == ("PRAGMA query_only=ON", ())
    assert queries[1][1] == [1]
    assert "WHERE id IN (?)" in queries[1][0]
    assert path.read_bytes() == before
