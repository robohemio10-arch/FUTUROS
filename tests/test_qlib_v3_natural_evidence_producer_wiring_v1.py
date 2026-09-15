from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from smartcrypto.execution import signal_producer
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42, seal_decision_record,
)
from smartcrypto.learning.paper_autolearning.feedback_store import normalize_closed_trade_row
from smartcrypto.learning.paper_autolearning.live_feedback_loop import (
    run_paper_autolearning_live_feedback_loop_v1,
)
from smartcrypto.learning.qlib_v3_prospective import natural_producer as producer, store
from smartcrypto.learning.qlib_v3_prospective.orchestrator import run_cycle

from test_qlib_v3_prospective_evidence_orchestrator_v1 import (
    BOUNDARY, NOW, signal as signal_fixture,
)
from test_paper_candidate_trade_lineage_signal_producer_materialization_v1 import (
    _install_stubs, _producer_config, _source_row,
)
from test_paper_candidate_trade_lineage_strict_decision_projection_v1 import (
    _source_row as strict_source,
)
from test_paper_autolearning_live_feedback_loop_v1 import (
    _create_realistic_paper_db, _eth_long_trade,
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
    config = {"enabled": True, "activation": str(fixture["activation_path"]),
              "freeze": str(fixture["freeze_path"])}
    return fixture, config


def inputs(context):
    fixture, config = context
    row = signal_fixture(fixture["expected"])
    record = DecisionRecordV42.model_validate(row["decision"])
    signal = {k: getattr(record, k) for k in (
        "signal_id", "candidate_id", "correlation_id", "pair", "symbol")}
    signal.update(side=record.side.value, risk_approved=True, decision_ledger={
        "decision_event_id": record.event_id, "decision_payload_sha256": record.payload_sha256})
    return {"project_root": fixture["project_root"], "signals": [signal], "decisions": [record],
            "invocation_started_at": BOUNDARY, "runtime_mode": "paper", "config_source": config}


def evidence_path(context):
    fixture, _ = context
    return store.location(fixture["project_root"], fixture["expected"])


def test_ex_ante_signal_survives_disappearance_and_consumer_accepts(context):
    kwargs = inputs(context)
    baseline = copy.deepcopy(kwargs["signals"])
    report = producer.observe_signal_batch(**kwargs)
    assert report.status == "ok" and report.new_signal_count == 1
    assert kwargs["signals"] == baseline
    kwargs["signals"].clear()
    observed = run_cycle(**context[0])
    assert observed["eligible_signal_count"] == 1
    assert observed["eligible_outcome_count"] == 0
    assert observed["write_performed"] is False


def test_signal_rerun_is_byte_identical_noop_and_conflict_blocks(context):
    kwargs = inputs(context)
    assert producer.observe_signal_batch(**kwargs).write_performed
    path = evidence_path(context)
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert producer.observe_signal_batch(**kwargs).write_performed is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    body = kwargs["decisions"][0].model_dump(mode="python", exclude={"payload_sha256"})
    body["qlib_score"] = 0.2
    changed = seal_decision_record(body)
    kwargs["decisions"] = [changed]
    kwargs["signals"][0]["decision_ledger"]["decision_payload_sha256"] = changed.payload_sha256
    report = producer.observe_signal_batch(**kwargs)
    assert report.status == "blocked"
    assert report.reason == "causal_identity_content_conflict"
    assert path.read_bytes() == before[0]


@pytest.mark.parametrize("case", ["missing", "bad_hash", "signal_id", "side", "risk", "live", "replay", "model"])
def test_missing_or_divergent_signal_lineage_fails_closed(context, case):
    kwargs = inputs(context)
    if case == "missing":
        kwargs["decisions"] = []
    elif case == "bad_hash":
        kwargs["signals"][0]["decision_ledger"]["decision_payload_sha256"] = "a" * 64
    elif case == "signal_id":
        kwargs["signals"][0]["signal_id"] = "unrelated"
    elif case == "side":
        kwargs["signals"][0]["side"] = "short"
    elif case == "risk":
        kwargs["signals"][0]["risk_approved"] = False
    elif case == "live":
        kwargs["runtime_mode"] = "live"
    elif case == "replay":
        kwargs["invocation_started_at"] += timedelta(microseconds=1)
    else:
        body = kwargs["decisions"][0].model_dump(mode="python", exclude={"payload_sha256"})
        body["model_hash"] = "a" * 64
        record = seal_decision_record(body)
        kwargs["decisions"] = [record]
        kwargs["signals"][0]["decision_ledger"]["decision_payload_sha256"] = record.payload_sha256
    assert producer.observe_signal_batch(**kwargs).status == "blocked"
    assert not evidence_path(context).exists()


def test_guard_and_corrupt_store_block_without_overwrite(context):
    kwargs = inputs(context)
    path = evidence_path(context)
    with store.exclusive(path):
        assert producer.observe_signal_batch(**kwargs).status == "blocked"
        assert not path.exists()
    path.write_text('{"schema_version":"wrong"}')
    before = path.read_bytes()
    assert producer.observe_signal_batch(**kwargs).status == "blocked"
    assert path.read_bytes() == before


def test_historical_948_outcomes_never_create_parent_or_store(context, monkeypatch):
    fixture, config = context
    def forbidden(*args):
        pytest.fail("No V3 parents: historical trades must not be queried")
    monkeypatch.setattr(producer, "_trade_rows", forbidden)
    report = producer.observe_feedback_close(
        project_root=fixture["project_root"], snapshot_db=fixture["project_root"] / "unused.sqlite",
        events=[{"trade_id": n} for n in range(1, 949)], config_source=config, write=True,
    )
    assert report.status == "ok" and report.skipped_without_parent == 948
    assert report.new_outcome_count == 0 and not report.write_performed
    assert not evidence_path(context).exists()


def closed_source(context, *, tag="decision_event_id=ds1", trade_id=1):
    root = context[0]["project_root"]
    db = root / "closed.sqlite"
    opened, closed = BOUNDARY + timedelta(minutes=1), BOUNDARY + timedelta(minutes=2)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY,pair TEXT,is_short INTEGER,"
                           "is_open INTEGER,open_date TEXT,close_date TEXT,enter_tag TEXT)")
        connection.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", (
            trade_id, "BTC/USDT:USDT", 0, 0, opened.isoformat(), closed.isoformat(), tag))
    event = normalize_closed_trade_row(
        {"trade_id": trade_id, "pair": "BTC/USDT:USDT", "side": "long", "net_pnl": 1.25,
         "open_time": opened.isoformat(), "close_time": closed.isoformat()},
        source_file=str(db), source_sha256=None, ingestion_run_id="test", source_row_index=1,
        created_at_utc=NOW.isoformat(),
    )
    assert event["validation_status"] == "ok"
    return {"project_root": root, "snapshot_db": db, "events": [event],
            "config_source": context[1], "write": True}


def test_exact_closure_e2e_noop_and_financial_conflict(context):
    assert producer.observe_signal_batch(**inputs(context)).write_performed
    kwargs = closed_source(context)
    original = copy.deepcopy(kwargs["events"])
    report = producer.observe_feedback_close(**kwargs)
    assert report.status == "ok" and report.new_outcome_count == 1
    assert kwargs["events"] == original
    path = evidence_path(context)
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert not producer.observe_feedback_close(**kwargs).write_performed
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    observed = run_cycle(**context[0])
    assert (observed["eligible_signal_count"], observed["eligible_outcome_count"]) == (1, 1)
    kwargs["events"][0]["net_pnl"] = 999.0
    assert producer.observe_feedback_close(**kwargs).reason == "causal_identity_content_conflict"
    assert path.read_bytes() == before[0]


@pytest.mark.parametrize("tag", [None, "decision_event_id=other", "decision_event_id=ds1|decision_event_id=ds1"])
def test_no_nearest_or_ambiguous_parent_matching(context, tag):
    producer.observe_signal_batch(**inputs(context))
    report = producer.observe_feedback_close(**closed_source(context, tag=tag))
    assert report.new_outcome_count == 0 and not report.write_performed
    assert run_cycle(**context[0])["eligible_outcome_count"] == 0
    if tag and tag.count("decision_event_id") == 2:
        assert report.status == "blocked"


@pytest.mark.parametrize("case", ["time", "pnl", "side", "open"])
def test_invalid_closed_trade_is_never_admitted(context, case):
    producer.observe_signal_batch(**inputs(context))
    kwargs = closed_source(context)
    if case == "time":
        kwargs["events"][0]["open_time_utc"] = (BOUNDARY + timedelta(seconds=61)).isoformat()
    elif case == "pnl":
        kwargs["events"][0]["net_pnl"] = float("nan")
    elif case == "side":
        with sqlite3.connect(kwargs["snapshot_db"]) as connection:
            connection.execute("UPDATE trades SET is_short=1")
    else:
        with sqlite3.connect(kwargs["snapshot_db"]) as connection:
            connection.execute("UPDATE trades SET is_open=1")
    report = producer.observe_feedback_close(**kwargs)
    assert report.new_outcome_count == 0 and not report.write_performed
    assert run_cycle(**context[0])["eligible_outcome_count"] == 0


def test_feedback_no_write_preserves_store_database_and_guard(context):
    producer.observe_signal_batch(**inputs(context))
    kwargs = closed_source(context)
    kwargs["write"] = False
    path, db = evidence_path(context), kwargs["snapshot_db"]
    before = path.read_bytes(), db.read_bytes()
    result = producer.observe_feedback_close(**kwargs)
    assert result.new_outcome_count == 1 and not result.write_performed
    assert (path.read_bytes(), db.read_bytes()) == before
    assert not path.with_suffix(".guard").exists()


def test_disabled_and_bad_configuration_have_no_financial_side_effect(context):
    kwargs = inputs(context)
    kwargs["config_source"] = {"enabled": False}
    assert producer.observe_signal_batch(**kwargs).status == "disabled"
    kwargs["config_source"] = {"enabled": True, "output": "elsewhere.json"}
    assert producer.observe_signal_batch(**kwargs).status == "blocked"
    assert not evidence_path(context).exists()


def test_real_producer_hook_is_before_publication_with_identical_operational_payload(context, monkeypatch):
    fixture, config = context
    monkeypatch.chdir(fixture["project_root"])
    instant = BOUNDARY + timedelta(seconds=2)
    monkeypatch.setattr(signal_producer, "utc_now", lambda: instant)
    row = _source_row(**strict_source(
        model_hash=fixture["expected"].model_artifact_sha256,
        signal_timestamp_utc=BOUNDARY.isoformat(),
        feature_timestamp_utc=(BOUNDARY - timedelta(minutes=1)).isoformat(),
    ))
    writes, _ = _install_stubs(monkeypatch, row)
    original_gate = signal_producer.apply_risk_manager_gate
    def current_risk_check(signals, **kwargs):
        result = original_gate(signals, **kwargs)
        for signal in result.approved_signals:
            signal["risk_checked_at_utc"] = instant.isoformat()
        return result
    monkeypatch.setattr(signal_producer, "apply_risk_manager_gate", current_risk_check)
    monkeypatch.setattr(signal_producer, "prepare_before_risk_manager",
                        lambda signals, **kw: SimpleNamespace(signals=signals, enabled=False))
    baseline = signal_producer.build_active_signals(_producer_config())
    assert baseline["signals_after"] == 1
    projection = baseline["paper_candidate_strict_decision_projection"]
    assert projection["status"] == "ok", json.dumps(projection)
    expected = copy.deepcopy(writes["pinned.json"])
    assert not evidence_path(context).exists()
    published_after_capture = []
    def publish(path, payload):
        if str(path) in ("primary.json", "pinned.json"):
            assert evidence_path(context).is_file()
            assert run_cycle(**fixture)["eligible_signal_count"] == 1
            published_after_capture.append(str(path))
        writes[str(path)] = dict(payload)
    monkeypatch.setattr(signal_producer, "atomic_write_json", publish)
    enabled = _producer_config()
    enabled["qlib_v3_natural_evidence"] = config
    report = signal_producer.build_active_signals(enabled)
    assert report["qlib_v3_natural_evidence"]["new_signal_count"] == 1
    assert published_after_capture == ["primary.json", "pinned.json"]
    assert writes["pinned.json"] == expected


def test_feedback_hook_preserves_canonical_schema_and_economics(context):
    fixture, config = context
    assert producer.observe_signal_batch(**inputs(context)).write_performed
    root = fixture["project_root"]
    db = root / "paper.sqlite"
    trade = _eth_long_trade(close_date=(BOUNDARY + timedelta(minutes=2)).isoformat())
    trade.update(pair="BTC/USDT:USDT", open_date=(BOUNDARY + timedelta(minutes=1)).isoformat())
    # The existing fixture is a local SQLite Paper-schema test database only.
    _create_realistic_paper_db(db, trades=[trade])
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE trades ADD COLUMN enter_tag TEXT")
        connection.execute("UPDATE trades SET pair='BTC/USDT:USDT',enter_tag='decision_event_id=ds1'")
    db_before = db.read_bytes()
    baseline = run_paper_autolearning_live_feedback_loop_v1(
        project_root=root, explicit_paper_db_path=db, write=True)
    outcome_path = root / "data/feedback/outcome_events.parquet"
    canonical = outcome_path.read_bytes()
    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=root, explicit_paper_db_path=db, write=True, qlib_v3_natural_evidence=config)
    assert report["qlib_v3_natural_evidence"]["new_outcome_count"] == 1
    assert outcome_path.read_bytes() == canonical
    assert db.read_bytes() == db_before
    assert baseline["projected_outcome_event_count"] == report["projected_outcome_event_count"] == 1
    assert run_cycle(**fixture)["eligible_outcome_count"] == 1
    payload = json.loads(evidence_path(context).read_text())
    assert payload["outcomes"][0]["net_pnl"] != 999.0
