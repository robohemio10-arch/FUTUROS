from __future__ import annotations

from datetime import datetime

import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    seal_decision_record,
)
from smartcrypto.learning.qlib_v3_prospective import (
    economic_shadow_decision_producer as shadow,
    natural_producer as producer,
    store,
)
from smartcrypto.learning.qlib_v3_prospective.economic_shadow_decision_producer import (
    ShadowDecisionBatch,
    ShadowDecisionReport,
)
from smartcrypto.learning.qlib_v3_prospective.trade_link_crosswalk import (
    validate_operational_crosswalk,
)

from test_qlib_v3_natural_evidence_producer_wiring_v1 import (
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
            return (
                NOW
                if tz is not None
                else NOW.replace(tzinfo=None)
            )

    monkeypatch.setattr(
        producer,
        "datetime",
        Clock,
    )
    monkeypatch.setattr(
        producer,
        "CANONICAL",
        fixture["expected"],
    )
    monkeypatch.delenv(
        producer.CONFIG_ENV,
        raising=False,
    )

    config = {
        "enabled": True,
        "activation": str(
            fixture["activation_path"]
        ),
        "freeze": str(
            fixture["freeze_path"]
        ),
    }
    return fixture, config


def _evidence_path(context):
    fixture, _ = context
    return store.location(
        fixture["project_root"],
        fixture["expected"],
    )


def _operational_model_mismatch(context):
    kwargs = inputs(context)
    certified_v3_record = kwargs["decisions"][0]

    body = certified_v3_record.model_dump(
        mode="python",
        exclude={"payload_sha256"},
    )
    body["model_hash"] = "a" * 64

    operational_record = seal_decision_record(
        body
    )
    kwargs["decisions"] = [
        operational_record
    ]
    kwargs["signals"][0][
        "decision_ledger"
    ] = {
        "decision_event_id": (
            operational_record.event_id
        ),
        "decision_payload_sha256": (
            operational_record.payload_sha256
        ),
    }

    return (
        kwargs,
        operational_record,
        certified_v3_record,
    )


def _blocked_shadow(
    reason: str,
) -> ShadowDecisionBatch:
    return ShadowDecisionBatch(
        (),
        (),
        ShadowDecisionReport(
            "blocked",
            reason,
            "blocked",
            candidate_count=1,
            scored_count=0,
            selected_count=0,
            control_count=0,
            market_source="blocked",
        ),
    )


def test_blocked_shadow_preserves_real_reason_with_operational_model_mismatch(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _, _ = (
        _operational_model_mismatch(
            context
        )
    )

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **_: _blocked_shadow(
            "shadow_canonical_refresh_snapshot_unavailable"
        ),
    )

    report = producer.observe_signal_batch(
        **kwargs
    )
    payload = report.to_dict()

    assert report.status == "blocked"
    assert (
        report.reason
        == "shadow_canonical_refresh_snapshot_unavailable"
    )
    assert (
        report.shadow_block_reason
        == "shadow_canonical_refresh_snapshot_unavailable"
    )
    assert (
        report.operational_model_mismatch_observed
        is True
    )
    assert (
        report.operational_model_mismatch_count
        == 1
    )
    assert (
        report.shadow_decision_source
        == "blocked"
    )
    assert (
        report.write_performed
        is False
    )
    assert (
        payload[
            "operational_model_mismatch_observed"
        ]
        is True
    )
    assert (
        payload[
            "operational_model_mismatch_count"
        ]
        == 1
    )
    assert (
        payload["shadow_block_reason"]
        == report.reason
    )
    assert (
        payload["operational_authority"]
        is False
    )
    assert (
        payload["paper_behavior_changed"]
        is False
    )
    assert payload["sends_orders"] is False
    assert (
        payload["historical_backfill_allowed"]
        is False
    )
    assert not _evidence_path(
        context
    ).exists()


def test_blocked_shadow_without_model_mismatch_keeps_mismatch_telemetry_false(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = inputs(context)

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        lambda **_: _blocked_shadow(
            "shadow_feature_matrix_non_finite"
        ),
    )

    report = producer.observe_signal_batch(
        **kwargs
    )

    assert report.status == "blocked"
    assert (
        report.reason
        == "shadow_feature_matrix_non_finite"
    )
    assert (
        report.shadow_block_reason
        == "shadow_feature_matrix_non_finite"
    )
    assert (
        report.operational_model_mismatch_observed
        is False
    )
    assert (
        report.operational_model_mismatch_count
        == 0
    )
    assert not _evidence_path(
        context
    ).exists()


def test_successful_shadow_scoring_with_operational_model_mismatch_is_not_blocked(
    context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        kwargs,
        operational_record,
        certified_v3_record,
    ) = _operational_model_mismatch(
        context
    )

    def resolve(**call):
        evidence_signal = dict(
            call["signals"][0]
        )
        evidence_signal[
            "decision_ledger"
        ] = {
            "decision_event_id": (
                certified_v3_record.event_id
            ),
            "decision_payload_sha256": (
                certified_v3_record.payload_sha256
            ),
        }

        return ShadowDecisionBatch(
            (evidence_signal,),
            (certified_v3_record,),
            ShadowDecisionReport(
                "ok",
                "certified_v3_shadow_decisions_projected",
                "certified_v3_shadow_booster",
                candidate_count=1,
                scored_count=1,
                selected_count=0,
                control_count=1,
                market_source=(
                    "test_canonical_cache"
                ),
            ),
        )

    monkeypatch.setattr(
        shadow,
        "resolve_shadow_decision_batch",
        resolve,
    )

    report = producer.observe_signal_batch(
        **kwargs
    )

    assert report.status == "ok"
    assert report.new_signal_count == 1
    assert report.write_performed is True
    assert (
        report.operational_model_mismatch_observed
        is True
    )
    assert (
        report.operational_model_mismatch_count
        == 1
    )
    assert (
        report.shadow_block_reason is None
    )

    state = store.load(
        _evidence_path(context),
        context[0]["expected"],
    )
    assert len(state["signals"]) == 1

    row = state["signals"][0]
    crosswalk = validate_operational_crosswalk(
        row["operational_crosswalk"],
        v3_decision=certified_v3_record,
    )

    assert crosswalk is not None
    assert (
        crosswalk[
            "operational_decision_event_id"
        ]
        == operational_record.event_id
    )
    assert (
        crosswalk[
            "operational_decision_payload_sha256"
        ]
        == operational_record.payload_sha256
    )


def test_new_telemetry_is_additive_and_default_safe() -> None:
    report = producer.ProducerReport(
        "ok",
        "diagnostic_default",
    )

    payload = report.to_dict()

    assert (
        payload[
            "operational_model_mismatch_observed"
        ]
        is False
    )
    assert (
        payload[
            "operational_model_mismatch_count"
        ]
        == 0
    )
    assert (
        payload["shadow_block_reason"]
        is None
    )
    assert (
        payload["schema_version"]
        == "qlib_v3_natural_evidence_producer_wiring_v1"
    )
    assert payload["research_only"] is True
    assert (
        payload["operational_authority"]
        is False
    )
    assert (
        payload["paper_behavior_changed"]
        is False
    )
    assert payload["sends_orders"] is False
    assert (
        payload["v2_evidence_imported"]
        is False
    )
    assert (
        payload["historical_backfill_allowed"]
        is False
    )
