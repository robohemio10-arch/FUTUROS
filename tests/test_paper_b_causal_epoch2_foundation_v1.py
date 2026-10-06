from __future__ import annotations

from copy import deepcopy
import json

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.canonical_treatment.causal_epoch2_foundation import (
    EPOCH1_ID,
    EPOCH2_ID,
    EPOCH2_SAFETY_FLAGS,
    CausalEpoch2FoundationError,
    _persist_report,
    _validated_jsonl_row_count,
    build_registration,
    drift_categories,
    fingerprint_differences,
    run_foundation,
    validate_current_epoch2_runtime,
    validate_registration,
)


def _epoch1_baseline() -> dict:
    return {
        "baseline_sha256": "b" * 64,
        "fix_deployed_at_utc": "2026-09-24T22:44:17.720829Z",
    }


def _epoch1_manifest() -> dict:
    return {
        "manifest_sha256": "a" * 64,
        "formal_activation_utc": "2026-09-23T23:33:32.351408Z",
    }


def _fingerprints() -> dict:
    return {
        "control": {
            "started_at": "2026-09-28T13:06:10.771041Z",
            "image": "image-control",
        },
        "monitor": {
            "started_at": "2026-09-28T13:06:10.838671Z",
        },
        "publisher": {
            "started_at": "2026-09-28T13:06:10.841853Z",
            "selector": {
                "container_id": "new-selector",
                "started_at": "2026-09-29T22:15:46.820601Z",
                "source_sha256": {
                    "natural_producer.py": "7" * 64,
                    "admission_lineage_probe.py": "6" * 64,
                },
            },
        },
        "treatment": {
            "started_at": "2026-09-28T13:06:10.785843Z",
            "live_container_spec_sha256": "4" * 64,
            "live_container_spec_snapshot": {
                "host_config_sha256": "5" * 64,
            },
        },
    }


def _current_audit() -> dict:
    fingerprints = _fingerprints()
    return {
        "generated_at_utc": "2026-10-05T20:00:00.123456Z",
        "status": "ok",
        "parity_status": "PASS",
        "policy_version": "economic_projection_live_spec_v3",
        "audit_sha256": "c" * 64,
        "fingerprints": fingerprints,
        "fingerprints_sha256": digest(fingerprints),
        "git": {
            "commit": "d" * 40,
            "tree": "e" * 40,
        },
        "join_contract": {
            "prospective_only": True,
        },
    }


def _opening() -> dict:
    return {
        "operational_decision_ledger": {
            "row_count": 1000,
            "sha256": "1" * 64,
        },
        "treatment_decision_ledger": {
            "row_count": 700,
            "sha256": "2" * 64,
        },
        "qlib_v3_store": {
            "signal_count": 1200,
            "outcome_count": 100,
            "store_sha256": "3" * 64,
        },
    }


def _differences() -> list[dict]:
    old = deepcopy(_fingerprints())
    old["publisher"]["selector"]["container_id"] = "old-selector"
    old["publisher"]["selector"]["started_at"] = "2026-09-23T12:02:07.694148Z"
    old["publisher"]["selector"]["source_sha256"] = {
        "natural_producer.py": "f" * 64,
    }
    return fingerprint_differences(old, _fingerprints())


def _registration() -> dict:
    return build_registration(
        epoch1_baseline=_epoch1_baseline(),
        epoch1_manifest=_epoch1_manifest(),
        current_audit=_current_audit(),
        opening_snapshot=_opening(),
        differences=_differences(),
    )


def test_fingerprint_differences_are_sorted_and_exact() -> None:
    rows = _differences()
    paths = [row["path"] for row in rows]
    assert paths == sorted(paths)
    assert "publisher.selector.source_sha256.admission_lineage_probe.py" in paths
    assert "publisher.selector.source_sha256.natural_producer.py" in paths


def test_drift_categories_identify_selector_source_change() -> None:
    categories = drift_categories(_differences())
    assert categories["selector_source_changed"] is True
    assert categories["selector_container_changed"] is True
    assert categories["selector_restart_observed"] is True


def test_build_registration_seals_epoch2_without_cross_epoch_carryover() -> None:
    registration = _registration()
    assert registration["epoch_id"] == EPOCH2_ID
    assert registration["predecessor_epoch_id"] == EPOCH1_ID
    assert registration["predecessor_closeout"]["reason"] == "causal_runtime_drift"
    assert registration["predecessor_closeout"]["historical_evidence_remains_immutable"] is True
    assert registration["predecessor_closeout"]["historical_population_carryover_allowed"] is False
    policy = registration["epoch_baseline"]["population_policy"]
    assert policy["predecessor_population_carryover_allowed"] is False
    assert policy["cross_epoch_counter_aggregation_allowed"] is False
    assert policy["pre_activation_rows_eligible"] is False


def test_build_registration_uses_current_audit_as_new_activation() -> None:
    registration = _registration()
    manifest = registration["epoch_manifest"]
    assert manifest["formal_activation_utc"] == "2026-10-05T20:00:00.123456Z"
    assert manifest["registration_audit_sha256"] == "c" * 64
    assert manifest["fingerprints_sha256"] == digest(manifest["fingerprints"])
    assert manifest["qlib_v3_identity"] == CANONICAL.mapping()


def test_first_proven_incompatible_runtime_is_selector_start() -> None:
    registration = _registration()
    assert (
        registration["predecessor_closeout"]["first_proven_incompatible_runtime_utc"]
        == "2026-09-29T22:15:46.820601Z"
    )


def test_registration_safety_has_no_operational_authority() -> None:
    registration = _registration()
    assert registration["safety"] == EPOCH2_SAFETY_FLAGS
    assert registration["safety"]["operational_authority"] is False
    assert registration["safety"]["sends_orders"] is False
    assert registration["safety"]["changes_risk"] is False
    assert registration["safety"]["writes_runtime"] is False


def test_registration_hash_tamper_fails_closed() -> None:
    registration = _registration()
    registration["predecessor_closeout"]["reason"] = "changed"
    with pytest.raises(CausalEpoch2FoundationError, match="registration_hash_invalid"):
        validate_registration(registration)


def test_manifest_fingerprint_tamper_fails_closed_after_resealing_outer_hash() -> None:
    registration = _registration()
    registration["epoch_manifest"]["fingerprints"]["control"]["image"] = "changed"
    registration["registration_sha256"] = digest(
        {key: value for key, value in registration.items() if key != "registration_sha256"}
    )
    with pytest.raises(
        CausalEpoch2FoundationError,
        match="manifest_hash_invalid|fingerprint_hash_invalid",
    ):
        validate_registration(registration)


def test_selector_source_change_is_mandatory_for_this_epoch2_recovery() -> None:
    differences = [
        {
            "path": "control.started_at",
            "registered": "old",
            "current": "new",
        }
    ]
    with pytest.raises(
        CausalEpoch2FoundationError,
        match="requires_proven_selector_source_change",
    ):
        build_registration(
            epoch1_baseline=_epoch1_baseline(),
            epoch1_manifest=_epoch1_manifest(),
            current_audit=_current_audit(),
            opening_snapshot=_opening(),
            differences=differences,
        )


def test_opening_snapshot_counts_must_be_non_negative() -> None:
    registration = _registration()
    registration["epoch_baseline"]["opening_snapshot"]["qlib_v3_store"]["signal_count"] = -1
    baseline = registration["epoch_baseline"]
    baseline["epoch_baseline_sha256"] = digest(
        {key: value for key, value in baseline.items() if key != "epoch_baseline_sha256"}
    )
    registration["registration_sha256"] = digest(
        {key: value for key, value in registration.items() if key != "registration_sha256"}
    )
    with pytest.raises(CausalEpoch2FoundationError, match="store_count_invalid"):
        validate_registration(registration)


def test_validate_current_epoch2_runtime_accepts_equivalent_runtime(monkeypatch) -> None:
    registration = _registration()
    audit = _current_audit()
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.causal_epoch2_foundation.governance.validate_audit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.causal_epoch2_foundation.governance._same_runtime_after_selector_restart",
        lambda *_args, **_kwargs: True,
    )
    validate_current_epoch2_runtime(registration, audit)


def test_validate_current_epoch2_runtime_blocks_future_drift(monkeypatch) -> None:
    registration = _registration()
    audit = _current_audit()
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.causal_epoch2_foundation.governance.validate_audit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.causal_epoch2_foundation.governance._same_runtime_after_selector_restart",
        lambda *_args, **_kwargs: False,
    )
    with pytest.raises(CausalEpoch2FoundationError, match="epoch2_runtime_drift"):
        validate_current_epoch2_runtime(registration, audit)


def test_registration_temporal_order_is_strict() -> None:
    audit = _current_audit()
    audit["generated_at_utc"] = "2026-09-29T22:15:46.820600Z"
    with pytest.raises(CausalEpoch2FoundationError, match="drift_boundary_invalid"):
        build_registration(
            epoch1_baseline=_epoch1_baseline(),
            epoch1_manifest=_epoch1_manifest(),
            current_audit=audit,
            opening_snapshot=_opening(),
            differences=_differences(),
        )


def test_operational_jsonl_rows_are_parsed_before_counting() -> None:
    raw = b'{"event_id":"a"}\n\n{"event_id":"b"}\n'
    assert _validated_jsonl_row_count(raw) == 2


def test_operational_jsonl_malformed_row_fails_closed() -> None:
    raw = b'{"event_id":"a"}\n{"event_id":'
    with pytest.raises(CausalEpoch2FoundationError, match="operational_ledger_json_invalid"):
        _validated_jsonl_row_count(raw)


def test_operational_jsonl_non_object_row_fails_closed() -> None:
    with pytest.raises(CausalEpoch2FoundationError, match="operational_ledger_row_not_object"):
        _validated_jsonl_row_count(b'[]\n')


def test_persist_report_writes_same_final_payload_returned(tmp_path) -> None:
    path = tmp_path / "epoch2_report.json"
    report = {
        "schema_version": "test",
        "status": "ok",
        "write_requested": True,
        "write_performed": False,
    }
    persisted = _persist_report(path, report)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == persisted
    assert persisted["write_performed"] is True
    assert persisted["report_path"] == str(path)
    assert persisted["report_sha256"] == digest(
        {key: value for key, value in persisted.items() if key != "report_sha256"}
    )


def test_registration_and_report_path_collision_is_blocked(tmp_path) -> None:
    report = run_foundation(
        project_root=tmp_path,
        runtime_root=tmp_path,
        epoch1_project_root=tmp_path,
        epoch1_baseline_path=tmp_path / "missing-baseline.json",
        evidence_root=tmp_path,
        registration_path="same.json",
        report_path="same.json",
        write_report=True,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "registration_report_path_collision"
    assert report["write_performed"] is False

