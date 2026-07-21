from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_v4_2 import LedgerWriteError, RuntimePathDeniedError
from smartcrypto.execution.decision_ledger_runtime_integration_v1 import (
    DisabledProjectionSink,
    InMemoryProjectionSink,
    LegacyWriterGuardError,
    ProjectionWriteDisabledError,
    SandboxFileProjectionSink,
    SandboxIntegrationConfigV1,
    SignalSourceValidationError,
    attach_decision_envelope,
    build_decision_index,
    build_default_projection_sink,
    build_runtime_decision_input,
    canonical_signal_sha256,
    classify_sink_health,
    inspect_legacy_strategy_writer,
    preview_after_risk_manager,
    preview_trade_link,
    validate_migration_mode,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1 import map_runtime_decision

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "decision_ledger_runtime_integration_v1"
DECISION_TIME = datetime(2026, 7, 20, 18, 0, 2, tzinfo=timezone.utc)


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def approved_signal() -> dict[str, object]:
    return load_fixture("approved_signal.json")


def rejected_signal() -> dict[str, object]:
    return load_fixture("rejected_signal.json")


def projection():
    return map_runtime_decision(
        build_runtime_decision_input(approved_signal(), decision_timestamp=DECISION_TIME)
    )


def preview_config() -> SandboxIntegrationConfigV1:
    return SandboxIntegrationConfigV1(mode="preview", enabled=True)


def test_default_config_is_disabled() -> None:
    config = SandboxIntegrationConfigV1()
    assert config.mode == "disabled"
    assert config.enabled is False
    assert config.writer_enabled is False


def test_preview_config_is_explicit() -> None:
    config = preview_config()
    assert config.mode == "preview"
    assert config.enabled is True


def test_config_rejects_inconsistent_mode() -> None:
    with pytest.raises(ValidationError, match="enabled_must_match_preview_mode"):
        SandboxIntegrationConfigV1(mode="preview", enabled=False)


def test_config_rejects_writer_enable() -> None:
    with pytest.raises(ValidationError):
        SandboxIntegrationConfigV1(writer_enabled=True)


def test_source_adapter_builds_certified_input() -> None:
    result = build_runtime_decision_input(approved_signal(), decision_timestamp=DECISION_TIME)
    assert result.signal_id == "signal-p04c-0001"
    assert result.risk_approved is True
    assert result.runtime_integration is False


def test_source_adapter_reports_all_missing_fields() -> None:
    payload = approved_signal()
    del payload["feature_hash"]
    del payload["model_hash"]
    with pytest.raises(SignalSourceValidationError) as captured:
        build_runtime_decision_input(payload, decision_timestamp=DECISION_TIME)
    assert captured.value.missing_fields == ("feature_hash", "model_hash")


def test_source_adapter_requires_exact_boolean() -> None:
    payload = approved_signal()
    payload["risk_approved"] = 1
    with pytest.raises(SignalSourceValidationError, match="exact_bool"):
        build_runtime_decision_input(payload, decision_timestamp=DECISION_TIME)


def test_signal_hash_is_deterministic() -> None:
    payload = approved_signal()
    assert canonical_signal_sha256(payload) == canonical_signal_sha256(dict(reversed(list(payload.items()))))


def test_envelope_does_not_mutate_source() -> None:
    payload = approved_signal()
    before = json.dumps(payload, sort_keys=True)
    result = attach_decision_envelope(payload, projection())
    assert json.dumps(payload, sort_keys=True) == before
    assert "decision_ledger" in result


def test_envelope_contains_canonical_hash() -> None:
    result = attach_decision_envelope(approved_signal(), projection())
    envelope = result["decision_ledger"]
    assert envelope["decision_payload_sha256"] == projection().target_payload.payload_sha256
    assert envelope["writer_invoked"] is False


def test_envelope_rejects_blocked_signal() -> None:
    with pytest.raises(ValueError, match="risk_approved"):
        attach_decision_envelope(rejected_signal(), projection())


def test_disabled_preview_preserves_approved_signals() -> None:
    payload = approved_signal()
    result = preview_after_risk_manager(
        approved_signals=[payload],
        rejected_signals=[],
        decision_timestamp=DECISION_TIME,
    )
    assert result.status == "disabled"
    assert result.active_signals == (payload,)
    assert result.projected_decision_count == 0


def test_preview_projects_approved_and_rejected() -> None:
    result = preview_after_risk_manager(
        approved_signals=[approved_signal()],
        rejected_signals=[rejected_signal()],
        decision_timestamp=DECISION_TIME,
        config=preview_config(),
    )
    assert result.status == "ok"
    assert result.projected_decision_count == 2
    assert result.active_envelope_count == 1
    assert result.writer_invoked is False


def test_preview_fail_closed_drops_unmappable_approved() -> None:
    payload = approved_signal()
    del payload["feature_hash"]
    result = preview_after_risk_manager(
        approved_signals=[payload],
        rejected_signals=[],
        decision_timestamp=DECISION_TIME,
        config=preview_config(),
    )
    assert result.status == "blocked"
    assert result.active_envelope_count == 0
    assert result.projection_failure_count == 1


def test_rejected_projection_failure_does_not_create_active_signal() -> None:
    payload = rejected_signal()
    del payload["model_hash"]
    result = preview_after_risk_manager(
        approved_signals=[approved_signal()],
        rejected_signals=[payload],
        decision_timestamp=DECISION_TIME,
        config=preview_config(),
    )
    assert result.status == "ok"
    assert result.active_envelope_count == 1
    assert result.projection_failure_count == 1


def test_partition_mismatch_is_detected() -> None:
    result = preview_after_risk_manager(
        approved_signals=[rejected_signal()],
        rejected_signals=[],
        decision_timestamp=DECISION_TIME,
        config=preview_config(),
    )
    assert result.status == "blocked"
    assert result.failures[0].error_type == "ValueError"


def test_default_sink_is_disabled() -> None:
    sink = build_default_projection_sink()
    assert isinstance(sink, DisabledProjectionSink)
    with pytest.raises(ProjectionWriteDisabledError):
        sink.append(projection())


def test_disabled_sink_health_is_not_runtime_ready() -> None:
    health = classify_sink_health(build_default_projection_sink())
    assert health["status"] == "disabled"
    assert health["ready_for_runtime"] is False


def test_memory_sink_idempotency() -> None:
    sink = InMemoryProjectionSink()
    first = sink.append(projection())
    second = sink.append(projection())
    assert first.duplicate is False
    assert second.duplicate is True
    assert len(sink.records()) == 1


def test_memory_sink_thread_safety() -> None:
    sink = InMemoryProjectionSink()
    receipts = []
    lock = threading.Lock()

    def worker() -> None:
        receipt = sink.append(projection())
        with lock:
            receipts.append(receipt)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not item.duplicate for item in receipts) == 1
    assert len(sink.records()) == 1


def test_sandbox_file_sink_append_and_health(tmp_path: Path) -> None:
    sink = SandboxFileProjectionSink(
        allowed_root=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
    )
    receipt = sink.append(projection())
    assert receipt.duplicate is False
    assert sink.health()["status"] == "healthy"


def test_sandbox_file_sink_duplicate_is_suppressed(tmp_path: Path) -> None:
    sink = SandboxFileProjectionSink(
        allowed_root=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
    )
    sink.append(projection())
    duplicate = sink.append(projection())
    assert duplicate.duplicate is True
    assert len((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_sandbox_sink_reloads_idempotency_index(tmp_path: Path) -> None:
    kwargs = {
        "allowed_root": tmp_path,
        "ledger_path": tmp_path / "ledger.jsonl",
        "health_path": tmp_path / "health.json",
    }
    SandboxFileProjectionSink(**kwargs).append(projection())
    duplicate = SandboxFileProjectionSink(**kwargs).append(projection())
    assert duplicate.duplicate is True


def test_sandbox_sink_denies_data_runtime_path(tmp_path: Path) -> None:
    runtime_root = tmp_path / "data" / "runtime"
    with pytest.raises(RuntimePathDeniedError):
        SandboxFileProjectionSink(
            allowed_root=tmp_path,
            ledger_path=runtime_root / "ledger.jsonl",
            health_path=runtime_root / "health.json",
        )


def test_sandbox_sink_lock_timeout_is_visible(tmp_path: Path) -> None:
    sink = SandboxFileProjectionSink(
        allowed_root=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
        lock_timeout_seconds=0.02,
    )
    sink.writer.lock_path.write_text("occupied", encoding="utf-8")
    with pytest.raises(LedgerWriteError, match="LedgerLockError"):
        sink.append(projection())
    assert sink.health()["status"] == "degraded"


def test_sandbox_sink_permission_failure_is_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = SandboxFileProjectionSink(
        allowed_root=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
    )

    def fail_append(_: bytes) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(sink.writer, "_append_bytes", fail_append)
    with pytest.raises(LedgerWriteError, match="PermissionError"):
        sink.append(projection())
    assert sink.health()["last_error_type"] == "PermissionError"


def test_fsync_is_exercised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    original = os.fsync

    def record(descriptor: int) -> None:
        calls.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", record)
    sink = SandboxFileProjectionSink(
        allowed_root=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        health_path=tmp_path / "health.json",
        fsync_enabled=True,
    )
    sink.append(projection())
    assert len(calls) >= 2


def test_trade_link_preview() -> None:
    decision = projection()
    result = preview_trade_link(
        decision_index=build_decision_index([decision]),
        request={
            "decision_event_id": decision.target_payload.event_id,
            "trade_observation": load_fixture("trade_observation.json"),
        },
    )
    assert result.status == "ok"
    assert result.projection is not None
    assert result.projection.target_payload.parent_event_id == decision.target_payload.event_id


def test_trade_link_unknown_decision_is_blocked() -> None:
    result = preview_trade_link(
        decision_index={},
        request={
            "decision_event_id": "decision-event:missing",
            "trade_observation": load_fixture("trade_observation.json"),
        },
    )
    assert result.status == "blocked"
    assert result.reason == "decision_event_not_found"


def test_trade_link_pair_mismatch_is_rejected() -> None:
    decision = projection()
    observation = load_fixture("trade_observation.json")
    observation["observed_pair"] = "ETH/USDT:USDT"
    with pytest.raises(ValueError, match="trade_pair_mismatch"):
        preview_trade_link(
            decision_index=build_decision_index([decision]),
            request={
                "decision_event_id": decision.target_payload.event_id,
                "trade_observation": observation,
            },
        )


def test_decision_index_rejects_duplicates() -> None:
    item = projection()
    with pytest.raises(ValueError, match="duplicate_decision_event_id"):
        build_decision_index([item, item])


def test_legacy_writer_guard_detects_fail_silent_pattern() -> None:
    report = inspect_legacy_strategy_writer(FIXTURE_DIR / "legacy_strategy_writer_sample.py")
    assert report["legacy_writer_method_found"] is True
    assert report["broad_continue_handler_count"] == 1
    assert report["canonical_writer"] is False


def test_migration_guard_allows_legacy_only() -> None:
    report = inspect_legacy_strategy_writer(FIXTURE_DIR / "legacy_strategy_writer_sample.py")
    result = validate_migration_mode(mode="legacy_only", report=report)
    assert result["legacy_writer_retained"] is True
    assert result["canonical_writer_enabled"] is False


def test_migration_guard_blocks_canonical_only() -> None:
    report = inspect_legacy_strategy_writer(FIXTURE_DIR / "legacy_strategy_writer_sample.py")
    with pytest.raises(LegacyWriterGuardError, match="canonical_only"):
        validate_migration_mode(mode="canonical_only", report=report)


def test_no_runtime_or_order_capability_imports() -> None:
    package_root = Path(__file__).resolve().parents[1] / "smartcrypto" / "execution" / "decision_ledger_runtime_integration_v1"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
    for token in ("import ccxt", "import requests", "import redis", "create_order(", "send_order(", "sqlite3"):
        assert token not in text
