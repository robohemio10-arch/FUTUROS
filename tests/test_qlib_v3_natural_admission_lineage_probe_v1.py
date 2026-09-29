"""Prospective, non-authoritative observer trace at the natural V3 boundary."""

from __future__ import annotations

import json

from smartcrypto.learning.qlib_v3_prospective import (
    admission_lineage_probe as lineage,
    economic_shadow_decision_producer as shadow_producer,
    natural_producer as producer,
)
from test_qlib_v3_natural_evidence_producer_wiring_v1 import evidence_path, inputs

pytest_plugins = ("test_qlib_v3_natural_evidence_producer_wiring_v1",)


def _trace(context):
    path = context[0]["project_root"] / "data/reports" / lineage.REPORT_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _stages(row):
    return [(item["stage"], item["result"]) for item in row["stages"]]


def test_healthy_signal_reaches_persisted_with_exact_lineage(context):
    kwargs = inputs(context)
    report = producer.observe_signal_batch(**kwargs)
    assert report.status == "ok" and report.new_signal_count == 1
    row = _trace(context)[0]
    assert row["schema_version"] == lineage.SCHEMA_VERSION
    assert row["signal_id"] == kwargs["signals"][0]["signal_id"]
    assert row["decision_event_id"] == kwargs["decisions"][0].event_id
    assert row["candidate_id"] == kwargs["decisions"][0].candidate_id
    assert row["correlation_id"] == kwargs["decisions"][0].correlation_id
    assert row["shadow_report_status"] == "ok"
    assert row["shadow_signal_present"] is True
    assert row["shadow_decision_present"] is True
    assert row["crosswalk_created"] is True
    assert row["persisted_to_v3_store"] is True
    assert row["first_failed_stage"] is None
    assert [stage for stage, _ in _stages(row)] == [
        "input_received", "input_validated", "prior_store_checked",
        "pending_for_shadow", "shadow_resolution_started",
        "shadow_resolution_completed", "crosswalk_created",
        "admission_validated", "persistence_started", "persisted",
    ]
    assert row["operational_authority"] is False
    assert row["sends_orders"] is False


def test_shadow_blocked_is_terminal_at_resolution(context, monkeypatch):
    def blocked(**_kwargs):
        return shadow_producer.ShadowDecisionBatch(
            (), (), shadow_producer.ShadowDecisionReport(
                "blocked", "shadow_fixture_blocked", "test", candidate_count=1,
            ),
        )

    monkeypatch.setattr(shadow_producer, "resolve_shadow_decision_batch", blocked)
    report = producer.observe_signal_batch(**inputs(context))
    row = _trace(context)[0]
    assert report.status == "blocked" and report.reason == "shadow_fixture_blocked"
    assert row["first_failed_stage"] == "shadow_resolution_completed"
    assert _stages(row)[-1] == ("shadow_resolution_completed", "blocked")
    assert row["shadow_report_reason"] == "shadow_fixture_blocked"
    assert not row["crosswalk_created"] and not row["persisted_to_v3_store"]
    assert not evidence_path(context).exists()


def test_missing_signal_in_shadow_response_is_not_silent(context, monkeypatch):
    def missing(**_kwargs):
        return shadow_producer.ShadowDecisionBatch(
            (), (), shadow_producer.ShadowDecisionReport("ok", "fixture_empty", "test"),
        )

    monkeypatch.setattr(shadow_producer, "resolve_shadow_decision_batch", missing)
    report = producer.observe_signal_batch(**inputs(context))
    row = _trace(context)[0]
    assert report.status == "ok" and report.new_signal_count == 0
    assert row["shadow_signal_present"] is False
    assert row["shadow_decision_present"] is False
    assert row["first_failed_stage"] == "shadow_resolution_completed"
    assert ("shadow_resolution_completed", "blocked") in _stages(row)
    assert row["stages"][-1]["reason"] == "shadow_signal_missing"
    assert not row["crosswalk_created"] and not row["persisted_to_v3_store"]


def test_missing_decision_in_shadow_response_is_explicit(context, monkeypatch):
    def missing_decision(**kwargs):
        return shadow_producer.ShadowDecisionBatch(
            (dict(kwargs["signals"][0]),), (),
            shadow_producer.ShadowDecisionReport("ok", "fixture_no_decision", "test"),
        )

    monkeypatch.setattr(shadow_producer, "resolve_shadow_decision_batch", missing_decision)
    report = producer.observe_signal_batch(**inputs(context))
    row = _trace(context)[0]
    assert report.status == "blocked"
    assert row["shadow_signal_present"] is True
    assert row["shadow_decision_present"] is False
    assert row["first_failed_stage"] == "shadow_resolution_completed"
    assert any(item["reason"] == "shadow_decision_missing" for item in row["stages"])
    assert not row["crosswalk_created"] and not row["persisted_to_v3_store"]


def test_input_validation_failure_is_attributed_without_shadow_call(context):
    kwargs = inputs(context)
    kwargs["signals"][0]["risk_approved"] = False
    report = producer.observe_signal_batch(**kwargs)
    row = _trace(context)[0]
    assert report.status == "blocked"
    assert row["first_failed_stage"] == "input_validated"
    assert row["stages"][-1]["stage"] == "blocked/error"
    assert "shadow_resolution_started" not in [stage for stage, _ in _stages(row)]


def test_persistence_failure_keeps_crosswalk_but_does_not_claim_persisted(context, monkeypatch):
    def fail(*_args):
        raise OSError("synthetic persistence failure")

    monkeypatch.setattr(producer, "_persist", fail)
    report = producer.observe_signal_batch(**inputs(context))
    row = _trace(context)[0]
    assert report.status == "blocked" and report.write_performed is False
    assert row["crosswalk_created"] is True
    assert row["persisted_to_v3_store"] is False
    assert row["first_failed_stage"] == "persistence_started"
    assert row["stages"][-1]["reason"] == "persistence_failed"
    assert not evidence_path(context).exists()


def test_diagnostic_write_failure_does_not_change_admission(context, monkeypatch, caplog):
    real_open = lineage.os.open

    def fail_probe_only(path, flags, mode=0o777):
        if str(path).endswith(lineage.REPORT_NAME):
            raise OSError("synthetic diagnostic failure")
        return real_open(path, flags, mode)

    monkeypatch.setattr(lineage.os, "open", fail_probe_only)
    report = producer.observe_signal_batch(**inputs(context))
    assert report.status == "ok" and report.new_signal_count == 1
    assert evidence_path(context).exists()
    assert "qlib_v3_admission_probe_write_failed" in caplog.text
    assert "synthetic diagnostic failure" not in caplog.text


def test_store_first_idempotency_preserved_and_reobserved(context):
    kwargs = inputs(context)
    assert producer.observe_signal_batch(**kwargs).new_signal_count == 1
    path = evidence_path(context)
    before = path.read_bytes()
    report = producer.observe_signal_batch(**kwargs)
    assert report.status == "ok" and report.write_performed is False
    assert path.read_bytes() == before
    first, second = _trace(context)
    assert first["invocation_id"] != second["invocation_id"]
    assert ("prior_store_checked", "already_present") in _stages(second)
    assert second["persisted_to_v3_store"] is True
    assert "shadow_resolution_started" not in [stage for stage, _ in _stages(second)]
