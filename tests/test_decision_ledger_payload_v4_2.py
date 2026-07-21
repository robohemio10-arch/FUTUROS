from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_v4_2 import (
    DecisionLedgerWriter,
    LedgerWriteError,
    RuntimePathDeniedError,
    canonical_json_bytes,
    parse_payload_record,
    seal_decision_record,
    seal_trade_link_record,
)
from smartcrypto.execution.decision_ledger_v4_2.schema import (
    SCHEMA_DIALECT,
    build_payload_json_schema,
    load_bundled_payload_json_schema,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "decision_ledger_v4_2"


def _load_fixture(name: str):
    return parse_payload_record((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _decision_body() -> dict[str, object]:
    payload = json.loads((FIXTURE_DIR / "valid_decision.json").read_text(encoding="utf-8"))
    payload.pop("payload_sha256")
    return payload


def _trade_link_body() -> dict[str, object]:
    payload = json.loads((FIXTURE_DIR / "valid_trade_link.json").read_text(encoding="utf-8"))
    payload.pop("payload_sha256")
    return payload


def test_valid_fixtures_are_sealed_and_linked() -> None:
    decision = _load_fixture("valid_decision.json")
    trade_link = _load_fixture("valid_trade_link.json")

    assert decision.record_type == "decision"
    assert trade_link.record_type == "trade_link"
    assert trade_link.parent_event_id == decision.event_id
    assert trade_link.decision_payload_sha256 == decision.payload_sha256
    assert len(decision.payload_sha256) == 64
    assert len(trade_link.payload_sha256) == 64


def test_contract_is_immutable_and_rejects_unknown_fields() -> None:
    decision = _load_fixture("valid_decision.json")
    with pytest.raises(ValidationError):
        decision.model_copy(update={"unknown_field": "x"}, deep=True).model_validate(
            {**decision.model_dump(mode="python"), "unknown_field": "x"}
        )
    with pytest.raises(ValidationError):
        decision.pair = "ETH/USDT:USDT"  # type: ignore[misc]


def test_canonical_serialization_is_deterministic() -> None:
    first = seal_decision_record(_decision_body())
    second_payload = dict(reversed(list(_decision_body().items())))
    second = seal_decision_record(second_payload)

    assert first.payload_sha256 == second.payload_sha256
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_tampered_payload_hash_is_rejected() -> None:
    payload = json.loads((FIXTURE_DIR / "valid_decision.json").read_text(encoding="utf-8"))
    payload["payload_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="payload_sha256_mismatch"):
        parse_payload_record(payload)


def test_decision_timestamp_must_not_precede_feature_timestamp() -> None:
    payload = _decision_body()
    payload["decision_timestamp"] = "2026-07-20T11:59:59Z"
    with pytest.raises(ValidationError, match="decision_timestamp_before_feature_timestamp"):
        seal_decision_record(payload)


def test_timestamps_must_use_utc_offset_zero() -> None:
    payload = _decision_body()
    payload["feature_timestamp"] = "2026-07-20T09:00:00-03:00"
    with pytest.raises(ValidationError, match="timestamp_must_use_utc_offset_zero"):
        seal_decision_record(payload)


def test_final_allow_requires_risk_manager_approval() -> None:
    payload = _decision_body()
    payload["risk_decision"] = "REJECTED"
    payload["risk_reasons"] = ["pair_not_allowed"]
    with pytest.raises(ValidationError, match="final_allow_requires_risk_approved"):
        seal_decision_record(payload)


def test_final_allow_requires_positive_stake_and_leverage() -> None:
    payload = _decision_body()
    payload["approved_stake_usdt"] = 0.0
    with pytest.raises(ValidationError, match="final_allow_requires_positive_approved_stake"):
        seal_decision_record(payload)

    payload = _decision_body()
    payload["approved_leverage"] = 0.0
    with pytest.raises(ValidationError, match="final_allow_requires_positive_approved_leverage"):
        seal_decision_record(payload)


def test_trade_link_is_append_only_reference_with_ordered_timestamps() -> None:
    payload = _trade_link_body()
    payload["execution_timestamp"] = "2026-07-20T11:59:59Z"
    with pytest.raises(ValidationError, match="execution_timestamp_before_decision_timestamp"):
        seal_trade_link_record(payload)

    payload = _trade_link_body()
    payload["parent_event_id"] = payload["event_id"]
    with pytest.raises(ValidationError, match="trade_link_parent_must_reference_distinct"):
        seal_trade_link_record(payload)


def test_writer_appends_two_records_and_persists_healthy_state(tmp_path: Path) -> None:
    writer = DecisionLedgerWriter(
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
        allowed_root=tmp_path,
        design_only=True,
    )
    decision = _load_fixture("valid_decision.json")
    trade_link = _load_fixture("valid_trade_link.json")

    first = writer.append(decision)
    second = writer.append(trade_link)

    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert [parse_payload_record(line).record_type for line in lines] == [
        "decision",
        "trade_link",
    ]
    health = writer.read_health()
    assert health.status == "healthy"
    assert health.total_successes == 2
    assert health.total_failures == 0
    assert health.consecutive_failures == 0
    assert health.last_event_id == trade_link.event_id
    assert first.bytes_written > 0
    assert second.bytes_written > 0


def test_writer_failure_is_visible_and_counter_is_monotonic(tmp_path: Path) -> None:
    ledger_directory = tmp_path / "ledger_as_directory"
    ledger_directory.mkdir()
    writer = DecisionLedgerWriter(
        ledger_path=ledger_directory,
        health_path=tmp_path / "health.json",
        allowed_root=tmp_path,
        design_only=True,
    )
    decision = _load_fixture("valid_decision.json")

    with pytest.raises(LedgerWriteError, match="ledger_append_failed"):
        writer.append(decision)
    first_health = writer.read_health()

    with pytest.raises(LedgerWriteError, match="ledger_append_failed"):
        writer.append(decision)
    second_health = writer.read_health()

    assert first_health.status == "degraded"
    assert first_health.total_failures == 1
    assert first_health.consecutive_failures == 1
    assert second_health.total_failures == 2
    assert second_health.consecutive_failures == 2
    assert second_health.last_error_type is not None
    assert second_health.last_error_message_sha256 is not None


def test_runtime_paths_are_denied_in_design_only_mode(tmp_path: Path) -> None:
    runtime_root = tmp_path / "data" / "runtime"
    with pytest.raises(RuntimePathDeniedError, match="runtime_path_denied"):
        DecisionLedgerWriter(
            ledger_path=runtime_root / "ledger.jsonl",
            health_path=runtime_root / "health.json",
            allowed_root=runtime_root,
            design_only=True,
        )


def test_bundled_schema_declares_draft_2020_12_and_both_record_types() -> None:
    bundled = load_bundled_payload_json_schema()
    generated = build_payload_json_schema()

    assert bundled["$schema"] == SCHEMA_DIALECT
    assert generated["$schema"] == SCHEMA_DIALECT
    assert bundled["x-schema-version"] == "decision_ledger_payload_v4_2"
    serialized = json.dumps(bundled, sort_keys=True)
    assert '"decision"' in serialized
    assert '"trade_link"' in serialized
    assert "payload_sha256" in serialized
    assert "additionalProperties" in serialized


def test_safety_flags_are_structurally_false() -> None:
    for record in (
        _load_fixture("valid_decision.json"),
        _load_fixture("valid_trade_link.json"),
    ):
        assert record.operational_authority is False
        assert record.runtime_integration is False
        assert record.sends_orders is False
        assert record.exchange_private_access is False
