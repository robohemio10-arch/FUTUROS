from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.audit_runtime_shared_report_writers_v2 import main as writer_audit_main
from scripts.build_runtime_integrity_traceability_ledger_v2 import (
    main as correlation_cli_main,
)
from smartcrypto.data.paper_trade_lifecycle import write_json as phase14_write_json
from smartcrypto.dashboard.trade_event_notifications_runtime_panel import (
    load_json_object as load_trade_notification_report,
)
from smartcrypto.ops.trade_event_notifications import write_report
from smartcrypto.qlib_engine.common import write_json as qlib_write_json
from smartcrypto.runtime.phase14_feedback_sync_healthcheck import (
    _read_report as read_phase14_health_report,
)
from smartcrypto.runtime.qlib_refresh_supervisor_healthcheck import (
    read_report as read_qlib_health_report,
)
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWriteError,
    AtomicWritePolicy,
    ConsistentReadError,
    CorrelationLedgerReportV2,
    atomic_append_jsonl,
    atomic_write_json,
    atomic_write_text,
    build_correlation_ledger,
    read_json_consistent,
)
from smartcrypto.runtime.integrity_traceability_v2 import atomic_writer
from smartcrypto.runtime.integrity_traceability_v2.writer_audit import (
    AUTHORITIES,
    audit_runtime_shared_report_writers,
    audit_source_text,
)

ROOT = Path(__file__).resolve().parents[1]


def policy_for(root: Path) -> AtomicWritePolicy:
    return AtomicWritePolicy.restricted((root,), working_directory=root)


def complete_events(
    *,
    correlation_id: str = "correlation-1",
    market_event_id: str = "market-1",
) -> list[dict[str, Any]]:
    common = {
        "correlation_id": correlation_id,
        "match_method": "explicit_correlation_id",
    }
    return [
        {
            **common,
            "source_type": "market_event",
            "source_reference": "market.json:1",
            "market_event_id": market_event_id,
        },
        {
            **common,
            "source_type": "prediction",
            "source_reference": "prediction.json:1",
            "prediction_id": f"prediction-{correlation_id}",
            "model_version": "model-v1",
        },
        {
            **common,
            "source_type": "shadow_decision",
            "source_reference": "shadow.jsonl:1",
            "shadow_decision_id": f"shadow-{correlation_id}",
        },
        {
            **common,
            "source_type": "risk_decision",
            "source_reference": "risk.json:1",
            "risk_decision_id": f"risk-{correlation_id}",
        },
        {
            **common,
            "source_type": "signal",
            "source_reference": "signals.json:1",
            "signal_id": f"signal-{correlation_id}",
            "decision_event_id": f"decision-{correlation_id}",
        },
        {
            **common,
            "source_type": "freqtrade_trade",
            "source_reference": "trades.sqlite:1",
            "freqtrade_trade_id": f"trade-{correlation_id}",
            "order_ids": [f"order-{correlation_id}"],
        },
        {
            **common,
            "source_type": "feedback",
            "source_reference": "feedback.jsonl:1",
            "feedback_event_id": f"feedback-{correlation_id}",
        },
        {
            **common,
            "source_type": "training_sample",
            "source_reference": "microbatch.parquet:1",
            "training_sample_id": f"sample-{correlation_id}",
        },
    ]


def run_concurrent_json_stress(
    target: Path,
    writer: Any,
    *,
    iterations: int = 80,
) -> list[str]:
    errors: list[str] = []
    stop = threading.Event()

    def read_loop() -> None:
        while not stop.is_set():
            if not target.exists():
                continue
            try:
                read_json_consistent(
                    target,
                    policy=policy_for(target.parent),
                )
            except (ConsistentReadError, OSError, json.JSONDecodeError) as exc:
                errors.append(type(exc).__name__)

    reader = threading.Thread(target=read_loop, daemon=True)
    reader.start()
    try:
        for sequence in range(iterations):
            writer({"sequence": sequence, "payload": "x" * 2048}, target)
    finally:
        stop.set()
        reader.join(timeout=5)
    return errors


def test_concurrent_reader_never_observes_empty_or_partial_json(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared.json"

    def writer(payload: dict[str, Any], path: Path) -> None:
        atomic_write_json(path, payload, policy=policy_for(tmp_path))

    assert run_concurrent_json_stress(target, writer) == []
    assert json.loads(target.read_text(encoding="utf-8"))["sequence"] == 79


def test_failure_before_replace_preserves_previous_valid_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report.json"
    policy = policy_for(tmp_path)
    atomic_write_json(target, {"version": "old"}, policy=policy)
    previous = target.read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("synthetic replace failure")

    with pytest.raises(AtomicWriteError, match="atomic_file_write_failed"):
        atomic_write_text(
            target,
            '{"version":"new"}\n',
            policy=policy,
            replace=fail_replace,
        )

    assert target.read_bytes() == previous
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": "old"}


def test_file_fsync_failure_is_fail_closed_and_preserves_previous_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report.json"
    policy = policy_for(tmp_path)
    atomic_write_json(target, {"version": "old"}, policy=policy)
    previous = target.read_bytes()

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    with pytest.raises(AtomicWriteError, match="atomic_file_write_failed"):
        atomic_write_text(
            target,
            '{"version":"new"}\n',
            policy=policy,
            fsync=fail_fsync,
        )

    assert target.read_bytes() == previous


def test_temporary_file_is_created_in_target_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.json"
    observed_directories: list[str | None] = []

    def observed_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        observed_directories.append(kwargs.get("dir"))
        return tempfile.mkstemp(*args, **kwargs)

    result = atomic_write_text(
        target,
        "{}\n",
        policy=policy_for(tmp_path),
        mkstemp=observed_mkstemp,
    )

    assert result.temporary_same_directory is True
    assert observed_directories == [str(target.parent)]


def test_symlink_component_is_blocked_without_platform_symlink_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "linked" / "report.json"
    original = atomic_writer._path_is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == target.parent or original(path)

    monkeypatch.setattr(atomic_writer, "_path_is_symlink", fake_is_symlink)
    with pytest.raises(AtomicWriteError, match="symlink_path_component_forbidden"):
        atomic_write_json(target, {"blocked": True}, policy=policy_for(tmp_path))


def test_path_traversal_and_outside_root_are_blocked(tmp_path: Path) -> None:
    policy = policy_for(tmp_path)
    with pytest.raises(AtomicWriteError, match="path_traversal_forbidden"):
        atomic_write_json("../escape.json", {"blocked": True}, policy=policy)
    with pytest.raises(AtomicWriteError, match="target_outside_authorized_roots"):
        atomic_write_json(
            tmp_path.parent / "escape.json",
            {"blocked": True},
            policy=policy,
        )


def test_cleanup_removes_only_own_temporary(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    sentinel = tmp_path / ".report.json.other-invocation.tmp"
    sentinel.write_text("other", encoding="utf-8")
    owned: list[Path] = []

    def observed_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        descriptor, raw_path = tempfile.mkstemp(*args, **kwargs)
        owned.append(Path(raw_path))
        return descriptor, raw_path

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("synthetic replace failure")

    with pytest.raises(AtomicWriteError):
        atomic_write_text(
            target,
            "{}\n",
            policy=policy_for(tmp_path),
            mkstemp=observed_mkstemp,
            replace=fail_replace,
        )

    assert sentinel.read_text(encoding="utf-8") == "other"
    assert owned and all(not path.exists() for path in owned)


def test_multiple_writers_are_serialized_without_corruption(tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    policy = policy_for(tmp_path)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def write_one(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            atomic_write_json(
                target,
                {"writer": index, "payload": str(index) * 4096},
                policy=policy,
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it
            errors.append(exc)

    threads = [threading.Thread(target=write_one, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["writer"] in range(8)
    assert payload["payload"] == str(payload["writer"]) * 4096


def test_lock_contention_recognizes_windows_and_posix_errors() -> None:
    windows_error = OSError("sharing violation")
    windows_error.winerror = 33  # type: ignore[attr-defined]
    assert atomic_writer._lock_contention(windows_error) is True
    assert atomic_writer._lock_contention(BlockingIOError()) is True


def test_jsonl_append_is_atomic_and_rejects_corrupt_existing_log(
    tmp_path: Path,
) -> None:
    target = tmp_path / "events.jsonl"
    policy = policy_for(tmp_path)
    atomic_append_jsonl(target, ({"event_id": "one"},), policy=policy)
    atomic_append_jsonl(target, ({"event_id": "two"},), policy=policy)
    assert [json.loads(line)["event_id"] for line in target.read_text().splitlines()] == [
        "one",
        "two",
    ]

    target.write_text('{"event_id":', encoding="utf-8")
    with pytest.raises(AtomicWriteError, match="existing_jsonl_missing_terminal_newline"):
        atomic_append_jsonl(target, ({"event_id": "three"},), policy=policy)


@pytest.mark.parametrize(
    "writer",
    (write_report, phase14_write_json, qlib_write_json),
)
def test_migrated_runtime_report_writers_are_atomic_under_concurrent_read(
    tmp_path: Path,
    writer: Any,
) -> None:
    target = tmp_path / "reports" / "shared.json"

    def adapted(payload: dict[str, Any], path: Path) -> None:
        if writer is write_report:
            writer(payload, path)
        else:
            writer(path, payload)

    assert run_concurrent_json_stress(target, adapted, iterations=40) == []


def test_trade_notification_writer_does_not_touch_state_or_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "reports" / "trade_notifications.json"
    state_db = tmp_path / "state.sqlite"
    state_db.write_bytes(b"read-only-state-sentinel")
    before = hashlib.sha256(state_db.read_bytes()).hexdigest()

    def forbidden_dispatch(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("notification dispatch must not be called by report writer")

    monkeypatch.setattr(
        "smartcrypto.ops.trade_event_notifications.dispatch_trade_events",
        forbidden_dispatch,
    )
    write_report(
        {
            "status": "ok",
            "reason": "test",
            "events": [],
            "sends_orders": False,
        },
        report,
    )

    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "ok"
    assert hashlib.sha256(state_db.read_bytes()).hexdigest() == before


def test_trade_notification_daemon_writer_and_monitor_reader_stress(
    tmp_path: Path,
) -> None:
    target = tmp_path / "reports" / "trade_notifications.json"
    errors: list[str] = []
    stop = threading.Event()

    def monitor() -> None:
        while not stop.is_set():
            if not target.exists():
                continue
            payload = load_trade_notification_report(target)
            if payload["status"] != "ok":
                errors.append(str(payload["reason"]))

    reader = threading.Thread(target=monitor, daemon=True)
    reader.start()
    try:
        for sequence in range(60):
            write_report(
                {
                    "status": "ok",
                    "reason": "daemon_iteration_complete",
                    "daemon_iteration": sequence,
                    "events": [],
                },
                target,
            )
    finally:
        stop.set()
        reader.join(timeout=5)

    assert errors == []


@pytest.mark.parametrize(
    ("writer", "reader"),
    (
        (phase14_write_json, read_phase14_health_report),
        (qlib_write_json, read_qlib_health_report),
    ),
)
def test_phase14_and_qlib_health_readers_never_observe_partial_reports(
    tmp_path: Path,
    writer: Any,
    reader: Any,
) -> None:
    target = tmp_path / "reports" / "health.json"
    errors: list[str] = []
    stop = threading.Event()

    def read_loop() -> None:
        while not stop.is_set():
            if not target.exists():
                continue
            try:
                payload = reader(target)
                if not isinstance(payload, dict):
                    errors.append("not_object")
            except RuntimeError as exc:
                errors.append(str(exc))

    monitor = threading.Thread(target=read_loop, daemon=True)
    monitor.start()
    try:
        for sequence in range(50):
            writer(target, {"status": "ok", "sequence": sequence})
    finally:
        stop.set()
        monitor.join(timeout=5)

    assert errors == []


def test_consistent_reader_remains_fail_closed_for_persistently_invalid_json(
    tmp_path: Path,
) -> None:
    target = tmp_path / "invalid.json"
    target.write_text('{"partial":', encoding="utf-8")

    with pytest.raises(ConsistentReadError, match="json_target_invalid_json"):
        read_json_consistent(
            target,
            policy=policy_for(tmp_path),
            attempts=2,
            sleep=lambda _seconds: None,
        )


def test_complete_explicit_identifier_chain_is_deterministic() -> None:
    events = complete_events()
    first = build_correlation_ledger(
        events,
        generated_at_utc="2026-07-29T00:00:00Z",
    )
    second = build_correlation_ledger(
        list(reversed(events)),
        generated_at_utc="2026-07-29T00:00:00Z",
    )

    assert first.status == "ok"
    assert first.complete_chain_count == 1
    assert first.quarantine_count == 0
    assert first.records[0].model_dump(mode="json") == second.records[0].model_dump(
        mode="json"
    )
    assert first.ids_synthesized_count == 0


def test_duplicate_identifier_across_chains_is_quarantined() -> None:
    events = [
        *complete_events(correlation_id="a", market_event_id="shared-market"),
        *complete_events(correlation_id="b", market_event_id="shared-market"),
    ]
    report = build_correlation_ledger(events)

    assert report.status == "blocked"
    assert report.complete_chain_count == 0
    assert report.duplicate_identifier_count == 2
    assert {
        item.reason for item in report.quarantine
    } == {"duplicate_identifier_across_chains"}


def test_ambiguous_identifier_chain_is_quarantined() -> None:
    events = complete_events()
    events[0]["prediction_id"] = "prediction-conflict"
    report = build_correlation_ledger(events)

    assert report.status == "blocked"
    assert report.quarantine[0].reason == "ambiguous_identifier_chain"
    assert report.quarantine[0].ambiguous_fields == ("prediction_id",)


def test_timestamp_only_matching_is_rejected() -> None:
    event = complete_events()[0]
    event["match_method"] = "timestamp_only"
    report = build_correlation_ledger([event])

    assert report.status == "blocked"
    assert report.timestamp_only_rejection_count == 1
    assert report.quarantine[0].reason == "timestamp_only_matching_forbidden"


def test_missing_ids_and_source_events_are_not_synthesized() -> None:
    events = complete_events()[:-1]
    report = build_correlation_ledger(events)

    assert report.status == "blocked"
    assert report.ids_synthesized_count == 0
    assert report.quarantine[0].reason == "incomplete_source_event_chain"
    assert report.quarantine[0].missing_fields == ("training_sample",)


def test_ledger_schema_round_trip_preserves_safety_contract() -> None:
    report = build_correlation_ledger(
        complete_events(),
        generated_at_utc="2026-07-29T00:00:00Z",
    )
    restored = CorrelationLedgerReportV2.model_validate_json(report.model_dump_json())

    assert restored == report
    safety = restored.safety_flags.model_dump(mode="json")
    assert safety["sends_orders"] is False
    assert safety["changes_risk"] is False
    assert safety["publishes_active_signals"] is False
    assert safety["operational_authority"] is False


def test_ledger_report_write_stays_in_research_report_boundary(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "events.json"
    report_path = tmp_path / "data" / "reports" / "ledger.json"
    input_path.write_text(json.dumps({"events": complete_events()}), encoding="utf-8")

    exit_code = correlation_cli_main(
        [
            "--project-root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--report",
            str(report_path),
            "--write-report",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["complete_chain_count"] == 1
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "active_freqtrade_signals.json").exists()


def test_correlation_cli_without_input_blocks_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = correlation_cli_main(
        ["--project-root", str(tmp_path), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "blocked"
    assert payload["complete_chain_count"] == 0
    assert payload["write_performed"] is False
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    ("source", "operation"),
    (
        (
            "from pathlib import Path\n"
            "def rogue():\n"
            "    Path('data/reports/shared.json').write_text('{}')\n",
            "write_text",
        ),
        (
            "def rogue():\n"
            "    with open('data/reports/shared.json', 'w') as handle:\n"
            "        handle.write('{}')\n",
            "open_write",
        ),
        (
            "from pathlib import Path\n"
            "def rogue():\n"
            "    Path('temp.json').replace(Path('data/reports/shared.json'))\n",
            "path.replace",
        ),
    ),
)
def test_static_auditor_blocks_direct_shared_writers(
    source: str,
    operation: str,
) -> None:
    findings = audit_source_text(source, path="smartcrypto/ops/rogue_writer.py")
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].operation == operation


def test_static_auditor_allows_institutional_writer_call() -> None:
    findings = audit_source_text(
        "def approved(path, payload):\n"
        "    atomic_write_json(path, payload)\n",
        path="smartcrypto/ops/approved_writer.py",
    )
    assert findings == []


def test_authority_registry_is_exact_and_contains_no_broad_patterns() -> None:
    assert AUTHORITIES
    for authority in AUTHORITIES:
        assert "*" not in authority.path
        assert "*" not in authority.function_or_class
        assert authority.path.endswith(".py")
        assert authority.function_or_class
        assert authority.operation
        assert authority.justification

    sibling_source = (
        "from pathlib import Path\n"
        "def _atomic_replace_bytes_locked():\n"
        "    Path('temp.json').write_text('{}')\n"
    )
    assert audit_source_text(
        sibling_source,
        path="smartcrypto/runtime/integrity_traceability_v2/atomic_writer_copy.py",
    )


def test_real_repository_writer_audit_has_no_critical_or_high() -> None:
    report = audit_runtime_shared_report_writers(ROOT)
    assert report["status"] == "ok"
    assert report["critical_count"] == 0
    assert report["high_count"] == 0
    assert report["authority_registry_exact_match_only"] is True
    assert report["wildcard_authority_allowed"] is False
    assert report["directory_authority_allowed"] is False


def test_writer_audit_cli_is_static_no_write_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = writer_audit_main(
        ["--project-root", str(ROOT), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False
    assert payload["changes_risk"] is False


def test_writer_audit_persisted_report_records_explicit_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "data" / "reports" / "writer_audit.json"
    exit_code = writer_audit_main(
        [
            "--project-root",
            str(tmp_path),
            "--report",
            str(report_path),
            "--write-report",
            "--json",
        ]
    )
    capsys.readouterr()
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert payload["writes_runtime"] is False


def test_migrated_modules_delegate_to_single_institutional_writer() -> None:
    migrated = (
        "scripts/export_freqtrade_paper_db_snapshot.py",
        "smartcrypto/data/paper_trade_lifecycle.py",
        "smartcrypto/execution/signal_producer.py",
        "smartcrypto/execution/signal_store.py",
        "smartcrypto/ml/model_decision_logger.py",
        "smartcrypto/ml/outcome_tracker.py",
        "smartcrypto/ops/trade_event_notifications.py",
        "smartcrypto/qlib_engine/common.py",
        "smartcrypto/qlib_engine/paper_refresh_supervisor.py",
    )
    for relative in migrated:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "integrity_traceability_v2" in source


def test_no_shell_true_or_runtime_activation_in_new_package() -> None:
    package = ROOT / "smartcrypto" / "runtime" / "integrity_traceability_v2"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
    )
    assert "shell=True" not in source
    assert "active_freqtrade_signals.json" not in source
    assert "ccxt" not in source
    assert "subprocess" not in source
    assert "ORDER_SUBMISSION_ENABLED=true" not in source
