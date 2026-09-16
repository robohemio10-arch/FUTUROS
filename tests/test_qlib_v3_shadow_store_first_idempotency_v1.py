from __future__ import annotations

import copy
from datetime import datetime

import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    seal_decision_record,
)
from smartcrypto.learning.qlib_v3_prospective import (
    economic_shadow_decision_producer as shadow,
)
from smartcrypto.learning.qlib_v3_prospective import natural_producer as producer
from smartcrypto.learning.qlib_v3_prospective import store

from test_qlib_v3_prospective_evidence_orchestrator_v1 import (
    BOUNDARY,
    NOW,
    signal as signal_fixture,
)


pytest_plugins = (
    "test_qlib_v3_prospective_evidence_orchestrator_v1",
)


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


def _inputs(context):
    fixture, config = context
    row = signal_fixture(fixture["expected"])
    record = DecisionRecordV42.model_validate(row["decision"])
    signal = {
        key: getattr(record, key)
        for key in (
            "signal_id",
            "candidate_id",
            "correlation_id",
            "pair",
            "symbol",
        )
    }
    signal.update(
        side=record.side.value,
        risk_approved=True,
        decision_ledger={
            "decision_event_id": record.event_id,
            "decision_payload_sha256": record.payload_sha256,
        },
    )
    return {
        "project_root": fixture["project_root"],
        "signals": [signal],
        "decisions": [record],
        "invocation_started_at": BOUNDARY,
        "runtime_mode": "paper",
        "config_source": config,
    }


def _evidence_path(context):
    fixture, _ = context
    return store.location(
        fixture["project_root"],
        fixture["expected"],
    )


def test_persisted_certified_v3_signal_is_store_first_noop_without_rescore(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = _inputs(context)
    first = producer.observe_signal_batch(**kwargs)
    assert first.status == "ok"
    assert first.new_signal_count == 1
    assert first.write_performed is True

    path = _evidence_path(context)
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    )

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **kwargs: pytest.fail(
            "store-first noop must not invoke shadow scorer"
        ),
    )

    repeated = producer.observe_signal_batch(**kwargs)

    assert repeated.status == "ok"
    assert repeated.reason == "no_new_natural_signals"
    assert repeated.new_signal_count == 0
    assert repeated.write_performed is False
    assert repeated.shadow_decision_source == "persisted_v3_signal"
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    ) == before


def test_persisted_v3_signal_rejects_changed_certified_v3_payload(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = _inputs(context)
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = _evidence_path(context)
    before = path.read_bytes()

    body = kwargs["decisions"][0].model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    body["qlib_score"] = 0.2
    changed = seal_decision_record(body)
    kwargs["decisions"] = [changed]
    kwargs["signals"][0]["decision_ledger"] = {
        "decision_event_id": changed.event_id,
        "decision_payload_sha256": changed.payload_sha256,
    }

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **kwargs: pytest.fail(
            "conflicting certified rerun must block before scorer"
        ),
    )

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "blocked"
    assert report.reason == "causal_identity_content_conflict"
    assert path.read_bytes() == before


def test_persisted_v3_shadow_parent_makes_legacy_model_rerun_noop(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-crosswalk V3 rows preserve the original store-first compatibility."""

    kwargs = _inputs(context)
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = _evidence_path(context)
    state = store.load(path, context[0]["expected"])
    assert len(state["signals"]) == 1
    state["signals"][0].pop("operational_crosswalk", None)
    store.persist(
        path,
        {
            "schema_version": store.SCHEMA,
            "identity": context[0]["expected"].mapping(),
            "signals": state["signals"],
            "outcomes": state["outcomes"],
        },
    )

    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    )

    legacy_body = kwargs["decisions"][0].model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    legacy_body["model_hash"] = "b" * 64
    legacy = seal_decision_record(legacy_body)
    kwargs["decisions"] = [legacy]
    kwargs["signals"][0]["decision_ledger"] = {
        "decision_event_id": legacy.event_id,
        "decision_payload_sha256": legacy.payload_sha256,
    }
    baseline = copy.deepcopy(kwargs["signals"])

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **kwargs: pytest.fail(
            "pre-crosswalk legacy rerun must not rescore"
        ),
    )

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "ok"
    assert report.reason == "no_new_natural_signals"
    assert report.write_performed is False
    assert kwargs["signals"] == baseline
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    ) == before
