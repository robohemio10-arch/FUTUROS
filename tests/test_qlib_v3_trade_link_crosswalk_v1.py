from __future__ import annotations

import copy
from datetime import datetime

import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    seal_decision_record,
)
from smartcrypto.learning.qlib_v3_prospective import (
    admission,
    economic_shadow_decision_producer as shadow,
    natural_producer as producer,
    store,
)
from smartcrypto.learning.qlib_v3_prospective.economic_shadow_decision_producer import (
    ShadowDecisionBatch,
    ShadowDecisionReport,
)
from smartcrypto.learning.qlib_v3_prospective.orchestrator import run_cycle
from smartcrypto.learning.qlib_v3_prospective.trade_link_crosswalk import (
    validate_operational_crosswalk,
)

from test_qlib_v3_natural_evidence_producer_wiring_v1 import (
    closed_source,
    evidence_path,
    inputs,
)
from test_qlib_v3_prospective_evidence_orchestrator_v1 import (
    NOW,
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


def _install_dual_lineage_shadow(
    context,
    monkeypatch: pytest.MonkeyPatch,
):
    kwargs = inputs(context)
    v3_record = kwargs["decisions"][0]

    body = v3_record.model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    body["event_id"] = "operational-decision-dual-lineage-v1"
    body["idempotency_key"] = "operational-decision-dual-lineage-v1"
    body["model_id"] = "legacy-paper-model"
    body["model_version"] = "legacy-paper-v1"
    body["model_hash"] = "b" * 64
    operational_record = seal_decision_record(body)

    kwargs["decisions"] = [operational_record]
    kwargs["signals"][0]["decision_ledger"] = {
        "decision_event_id": operational_record.event_id,
        "decision_payload_sha256": operational_record.payload_sha256,
    }

    def resolve(**call):
        evidence_signal = dict(call["signals"][0])
        evidence_signal["decision_ledger"] = {
            "decision_event_id": v3_record.event_id,
            "decision_payload_sha256": v3_record.payload_sha256,
        }
        return ShadowDecisionBatch(
            (evidence_signal,),
            (v3_record,),
            ShadowDecisionReport(
                "ok",
                "test_dual_lineage_projection",
                "test_v3_shadow",
                candidate_count=1,
                scored_count=1,
                selected_count=0,
                control_count=1,
                market_source="test_canonical_cache",
            ),
        )

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        resolve,
    )
    return kwargs, operational_record, v3_record


def test_new_signal_persists_exact_dual_lineage_crosswalk(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, operational, v3_record = _install_dual_lineage_shadow(
        context,
        monkeypatch,
    )
    baseline = copy.deepcopy(kwargs["signals"])

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "ok"
    assert report.new_signal_count == 1
    assert report.write_performed is True
    assert kwargs["signals"] == baseline

    path = evidence_path(context)
    state = store.load(path, context[0]["expected"])
    assert len(state["signals"]) == 1
    row = state["signals"][0]

    crosswalk = validate_operational_crosswalk(
        row["operational_crosswalk"],
        v3_decision=v3_record,
    )
    assert crosswalk is not None
    assert crosswalk["signal_id"] == v3_record.signal_id
    assert (
        crosswalk["operational_decision_event_id"]
        == operational.event_id
    )
    assert (
        crosswalk["operational_decision_payload_sha256"]
        == operational.payload_sha256
    )
    assert (
        crosswalk["v3_decision_event_id"]
        == v3_record.event_id
    )
    assert (
        crosswalk["v3_decision_payload_sha256"]
        == v3_record.payload_sha256
    )


def test_operational_enter_tag_resolves_to_true_v3_parent(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, operational, v3_record = _install_dual_lineage_shadow(
        context,
        monkeypatch,
    )
    assert producer.observe_signal_batch(**kwargs).write_performed

    close_kwargs = closed_source(
        context,
        tag=f"decision_event_id={operational.event_id}",
    )
    report = producer.observe_feedback_close(**close_kwargs)

    assert report.status == "ok"
    assert report.new_outcome_count == 1
    assert report.skipped_without_parent == 0
    assert report.write_performed is True

    path = evidence_path(context)
    state = store.load(path, context[0]["expected"])
    assert len(state["outcomes"]) == 1
    outcome = state["outcomes"][0]

    assert outcome["decision_event_id"] == v3_record.event_id
    assert (
        outcome["trade_link"]["parent_event_id"]
        == v3_record.event_id
    )
    assert (
        outcome["trade_link"]["decision_payload_sha256"]
        == v3_record.payload_sha256
    )
    assert (
        outcome["trade_link"]["link_reason"]
        == "explicit_operational_decision_event_id_crosswalk"
    )

    observed = run_cycle(**context[0])
    assert observed["eligible_signal_count"] == 1
    assert observed["eligible_outcome_count"] == 1


def test_pre_crosswalk_signal_is_not_backfilled_or_inferred(
    context,
) -> None:
    kwargs = inputs(context)
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = evidence_path(context)
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

    close_kwargs = closed_source(
        context,
        tag="decision_event_id=unmapped-operational-decision",
    )
    report = producer.observe_feedback_close(**close_kwargs)

    assert report.status == "ok"
    assert report.new_outcome_count == 0
    assert report.skipped_without_parent == 1
    assert report.write_performed is False

    after = store.load(path, context[0]["expected"])
    assert "operational_crosswalk" not in after["signals"][0]
    assert after["outcomes"] == []


def test_crosswalk_nested_hash_tamper_fails_closed(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _, _ = _install_dual_lineage_shadow(
        context,
        monkeypatch,
    )
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = evidence_path(context)
    state = store.load(path, context[0]["expected"])
    state["signals"][0]["operational_crosswalk"][
        "operational_decision_event_id"
    ] = "tampered-operational-decision"

    store.persist(
        path,
        {
            "schema_version": store.SCHEMA,
            "identity": context[0]["expected"].mapping(),
            "signals": state["signals"],
            "outcomes": state["outcomes"],
        },
    )

    close_kwargs = closed_source(
        context,
        tag="decision_event_id=tampered-operational-decision",
    )
    report = producer.observe_feedback_close(**close_kwargs)

    assert report.status == "blocked"
    assert report.reason == "operational_crosswalk_hash_mismatch"
    assert report.new_outcome_count == 0
    assert report.write_performed is False


def test_admission_preserves_absence_for_legacy_signal_rows(
    context,
) -> None:
    kwargs = inputs(context)
    record: DecisionRecordV42 = kwargs["decisions"][0]
    activation = producer._activation(
        context[0]["project_root"],
        context[1],
    )
    assert activation is not None

    row = {
        "epoch_version": "v3",
        "origin": "natural_paper_runtime",
        "identity": activation.identity.mapping(),
        "synthetic": False,
        "replayed": False,
        "backfilled": False,
        "signal_id": record.signal_id,
        "decision_event_id": record.event_id,
        "signal_timestamp_utc": record.decision_timestamp.isoformat(),
        "decision": record.model_dump(mode="json"),
    }

    normalized = admission.signal(
        row,
        activation,
        NOW,
    )

    assert "operational_crosswalk" not in normalized

def test_dual_lineage_rerun_is_store_first_exact_noop(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _, _ = _install_dual_lineage_shadow(
        context,
        monkeypatch,
    )
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = evidence_path(context)
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    )

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **kwargs: pytest.fail(
            "exact persisted dual-lineage rerun must not rescore"
        ),
    )

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "ok"
    assert report.reason == "no_new_natural_signals"
    assert report.new_signal_count == 0
    assert report.write_performed is False
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
    ) == before


@pytest.mark.parametrize(
    "mutation",
    ["event_id", "payload"],
)
def test_persisted_crosswalk_rejects_operational_lineage_drift_before_rescore(
    context,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    kwargs, operational, _ = _install_dual_lineage_shadow(
        context,
        monkeypatch,
    )
    assert producer.observe_signal_batch(**kwargs).write_performed

    path = evidence_path(context)
    before = path.read_bytes()

    body = operational.model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    if mutation == "event_id":
        body["event_id"] = "operational-decision-dual-lineage-v1-drift"
        body["idempotency_key"] = (
            "operational-decision-dual-lineage-v1-drift"
        )
    else:
        body["qlib_score"] = float(body["qlib_score"]) + 0.125

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
            "crosswalk drift must block before shadow rescoring"
        ),
    )

    report = producer.observe_signal_batch(**kwargs)

    assert report.status == "blocked"
    assert report.reason == "operational_crosswalk_content_conflict"
    assert report.new_signal_count == 0
    assert report.write_performed is False
    assert path.read_bytes() == before
