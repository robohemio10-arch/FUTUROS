from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from scripts import collect_execution_decision_l1_prospective_v1 as cli
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    seal_decision_record,
)
from smartcrypto.ops.execution_decision_l1 import archive, collector, transport
from smartcrypto.ops.execution_decision_l1 import kill_switch_authority as authority
from smartcrypto.ops.execution_decision_l1.contracts import (
    CollectorConfig,
    DecisionL1Association,
    GapEvidence,
    L1Quote,
    Symbol,
    build_quote,
    canonical_sha256,
    schema_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/decision_ledger_v4_2/valid_decision.json"
T0 = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def decision(seconds: float = 2, suffix: str = "1", **changes: object) -> DecisionRecordV42:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.pop("payload_sha256")
    payload.update(
        event_id=f"decision-{suffix}",
        candidate_id=f"candidate-{suffix}",
        signal_id=f"signal-{suffix}",
        idempotency_key=f"decision:signal-{suffix}:v1",
        decision_timestamp=at(seconds),
        feature_timestamp=T0,
    )
    payload.update(changes)
    return seal_decision_record(payload)


def raw(seconds: float = 1, **changes: object) -> str:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "bidPrice": "100",
        "askPrice": "101",
        "bidQty": "2",
        "askQty": "3",
        "time": int(at(seconds).timestamp() * 1000),
    }
    payload.update(changes)
    return json.dumps(payload, sort_keys=True)


def quote(
    event: float = 1,
    received: float = 1.1,
    available: float = 1.2,
    mono: int = 1,
    **changes: object,
) -> L1Quote:
    return build_quote(raw(event, **changes), at(received), at(available), mono)


def state(**changes: object) -> collector.L1State:
    return collector.L1State(CollectorConfig.model_validate(changes), T0, collector.Metrics())


def association(value: collector.L1State, seconds: float = 2) -> DecisionL1Association:
    result = value.associate(decision(seconds), at(seconds + 1))
    assert result is not None
    return result


def ledger(tmp_path: Path, initial: bytes = b"") -> Path:
    path = tmp_path / authority.LEDGER_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(initial)
    (tmp_path / "data/runtime/kill_switch.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_mode": "paper",
                "updated_at": T0.isoformat(),
                "global": {
                    "enabled": False,
                    "reason": "isolated_fixture",
                    "actor": "pytest",
                    "updated_at": T0.isoformat(),
                },
                "symbols": {},
            }
        ),
        encoding="ascii",
    )
    return path


@pytest.fixture(autouse=True)
def bounded_fixture_monitors(monkeypatch: pytest.MonkeyPatch) -> Any:
    monitors: list[authority.AuthorityMonitor] = []
    original = authority.AuthorityMonitor.__init__

    def initialize(self: authority.AuthorityMonitor, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        monitors.append(self)

    monkeypatch.setattr(authority.AuthorityMonitor, "__init__", initialize)
    monkeypatch.setattr(authority, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(authority, "READ_DEADLINE_SECONDS", 0.2)
    yield
    for monitor in monitors:
        assert monitor.close(1)


def append(path: Path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)
        handle.flush()


def external(tmp_path: Path, **changes: object) -> archive.ExternalArchive:
    options: dict[str, Any] = dict(
        authorized=True,
        forbidden_roots=(tmp_path / "runtime",),
        segment_records=1,
        max_bytes=1024 * 1024,
    )
    options.update(changes)
    return archive.ExternalArchive(tmp_path / "external", **options)


def observer(tmp_path: Path, **config: object) -> collector.Collector:
    return collector.Collector(
        ledger(tmp_path),
        CollectorConfig.model_validate(config),
        now=lambda: at(3),
        runtime_root=tmp_path,
    )


def test_quote_exact_projection_hash_schema_and_explicit_spread() -> None:
    value = quote()
    assert value.event_time_utc == at(1)
    assert value.raw_sha256 == hashlib.sha256(value.raw_body.encode()).hexdigest()
    assert value.quote_id == canonical_sha256(value.model_dump(mode="json", exclude={"quote_id"}))
    assert value.spread_bps == Decimal(10000) / Decimal("100.5")
    assert "spread_bps" in value.model_dump(mode="json")
    assert value.clock_synchronization == "UNPROVEN"
    assert schema_sha256() == schema_sha256()
    assert len(schema_sha256()) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"askPrice": "99"},
        {"askPrice": "100"},
        {"bidPrice": "0"},
        {"bidPrice": "NaN"},
        {"askPrice": "Infinity"},
        {"bidQty": "-1"},
        {"symbol": "UNKNOWN"},
        {"time": True},
        {"api_key": "not-a-real-key"},
    ],
)
def test_invalid_public_schema_never_becomes_evidence(changes: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        quote(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"best_bid": "99"},
        {"raw_sha256": "a" * 64},
        {"quote_id": "b" * 64},
        {"spread_bps": "1"},
        {"available_at_utc": at(1)},
        {"event_time_utc": at(0)},
        {"receive_time_utc": T0.replace(tzinfo=None)},
    ],
)
def test_tampered_quote_or_invalid_clocks_fail_closed(changes: dict[str, object]) -> None:
    payload = quote().model_dump(mode="python")
    payload.update(changes)
    with pytest.raises(ValidationError):
        L1Quote.model_validate(payload)


def test_event_ahead_of_receive_is_not_assumed_clock_synchronized() -> None:
    with pytest.raises(ValidationError, match="exchange_clock_ahead"):
        quote(event=2)


def test_exact_decision_identity_and_strict_available_at() -> None:
    value = state()
    accepted = quote()
    value.accept(accepted)
    # The newest transaction happened earlier, but was observed after the decision.
    value.accept(quote(event=1.5, received=2.1, available=2.2, mono=2))
    result = association(value)
    parent = decision()
    assert result.quote == accepted
    assert result.decision_id == parent.event_id
    assert result.candidate_id == parent.candidate_id
    assert result.signal_id == parent.signal_id
    assert result.decision_payload_sha256 == parent.payload_sha256
    assert result.feature_age_seconds == 1
    assert result.status == "MATCHED_PIT"


@pytest.mark.parametrize("received,available", [(2.1, 2.2), (1.1, 2.2)])
def test_receive_or_queue_delay_cannot_be_backdated(received: float, available: float) -> None:
    value = state()
    value.accept(quote(received=received, available=available))
    assert association(value).reason == "NO_CAUSAL_QUOTE"


@pytest.mark.parametrize("seconds,expected", [(6, "MATCHED_PIT"), (6.001, "UNAVAILABLE")])
def test_freshness_boundary(seconds: float, expected: str) -> None:
    value = state()
    value.accept(quote())
    result = association(value, seconds)
    assert result.status == expected
    if expected == "UNAVAILABLE":
        assert result.reason == "QUOTE_STALE"
        assert result.quote is None and result.feature_age_seconds is None


def test_event_time_absent_does_not_get_synthetic_timestamp() -> None:
    value = state()
    value.accept(quote(time=None))
    assert association(value).reason == "EVENT_TIME_UNAVAILABLE"


def test_unknown_symbol_does_not_match_other_symbol() -> None:
    value = state()
    value.accept(quote())
    result = value.associate(decision(symbol="ETHUSDT", pair="ETH/USDT:USDT"), at(3))
    assert result is not None and result.reason == "NO_CAUSAL_QUOTE"


@pytest.mark.parametrize(
    "seconds,reason", [(2, "SOURCE_GAP_AFTER_QUOTE"), (2.4, "STRICT_PIT_MATCH")]
)
def test_gap_blocks_old_quote_and_new_snapshot_recovers(seconds: float, reason: str) -> None:
    value = state()
    value.accept(quote())
    value.gap(at(1.5), "http_503", "BTCUSDT")
    value.accept(quote(event=2, received=2.1, available=2.2, mono=2))
    assert association(value, seconds).reason == reason


def test_later_gap_does_not_hide_prior_gap_for_delayed_decision() -> None:
    value = state()
    value.accept(quote())
    value.gap(at(1.5), "http_503")
    value.gap(at(4), "http_503")
    assert association(value).reason == "SOURCE_GAP_AFTER_QUOTE"


def test_old_received_queued_quote_does_not_heal_a_gap() -> None:
    value = state()
    value.gap(at(1.5), "http_503")
    value.accept(quote(received=1.1, available=1.8))
    assert association(value).reason == "SOURCE_GAP_AFTER_QUOTE"


def test_gap_history_eviction_is_conservatively_blocked() -> None:
    value = state(history_per_symbol=1)
    value.accept(quote())
    value.gap(at(1.5), "http_503")
    value.gap(at(4), "http_503")
    assert association(value).reason == "GAP_HISTORY_EVICTED"


@pytest.mark.parametrize("available,mono", [(1.19, 2), (1.3, 1)])
def test_local_clock_or_monotonic_regression_latches(available: float, mono: int) -> None:
    value = state()
    value.accept(quote())
    assert not value.accept(quote(available=available, mono=mono))
    assert association(value).reason == "LOCAL_CLOCK_REGRESSION"


def test_market_time_regression_repeated_snapshots_and_bounded_history() -> None:
    value = state(history_per_symbol=1)
    value.accept(quote())
    assert not value.accept(quote(event=0.9, available=1.3, mono=2))
    assert value.metrics.snapshot()["market_time_regressions"] == 1
    assert value.accept(quote(available=1.4, mono=3))
    assert len(value.history["BTCUSDT"]) == 1
    assert value.metrics.snapshot()["repeated_snapshots"] == 1
    assert value.metrics.snapshot()["history_evictions"] == 1


def test_pre_session_duplicate_and_identity_budget() -> None:
    value = state(max_decisions=1)
    old = decision(
        decision_timestamp=T0 - timedelta(seconds=1), feature_timestamp=T0 - timedelta(seconds=2)
    )
    assert value.associate(old, at(3)) is None
    assert association(value).status == "UNAVAILABLE"
    assert value.associate(decision(), at(3)) is None
    with pytest.raises(ValueError, match="identity_budget"):
        value.associate(decision(suffix="2"), at(3))


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": "decision-1", "candidate_id": "another"},
        {"idempotency_key": "decision:signal-1:v1"},
    ],
)
def test_divergent_exact_id_or_idempotency_collision(changes: dict[str, Any]) -> None:
    value = state()
    association(value)
    with pytest.raises(ValueError, match="identity_collision"):
        value.associate(decision(suffix="2", **changes), at(3))
    assert value.metrics.snapshot()["identity_collisions"] == 1


def test_association_contract_rejects_future_or_unavailable_quote() -> None:
    value = state()
    value.accept(quote())
    payload = association(value).model_dump(mode="python")
    payload["decision_timestamp_utc"] = at(1)
    with pytest.raises(ValidationError, match="not_available_at_decision"):
        DecisionL1Association.model_validate(payload)
    payload["status"] = "UNAVAILABLE"
    with pytest.raises(ValidationError, match="cannot_contain_quote"):
        DecisionL1Association.model_validate(payload)


def test_ledger_eof_only_complete_lines_and_no_replay(tmp_path: Path) -> None:
    row = decision().model_dump_json().encode()
    path = ledger(tmp_path, row + b"\n")
    tail = collector.LedgerTail(path)
    try:
        assert tail.poll() == []
        append(path, row[:50])
        assert tail.poll() == []
        append(path, row[50:] + b"\n")
        assert tail.poll() == [decision()]
        assert tail.poll() == []
    finally:
        tail.close()
    assert path.read_bytes() == row + b"\n" + row + b"\n"


def test_ledger_initial_partial_skipped(tmp_path: Path) -> None:
    row = decision().model_dump_json().encode()
    path = ledger(tmp_path, row[:10])
    tail = collector.LedgerTail(path)
    try:
        append(path, row[10:] + b"\n" + row + b"\n")
        assert tail.poll() == [decision()]
    finally:
        tail.close()


@pytest.mark.parametrize("mode", ["truncated", "invalid_seal", "oversized"])
def test_ledger_change_invalid_seal_or_size_halts(tmp_path: Path, mode: str) -> None:
    path = ledger(tmp_path, b"existing\n")
    tail = collector.LedgerTail(path)
    try:
        if mode == "truncated":
            path.write_bytes(b"")
        elif mode == "invalid_seal":
            payload = decision().model_dump(mode="json")
            payload["payload_sha256"] = "a" * 64
            append(path, json.dumps(payload).encode() + b"\n")
        else:
            append(path, b"x" * (tail.MAX_LINE_BYTES + 1))
        with pytest.raises(ValueError):
            tail.poll()
    finally:
        tail.close()


def test_ledger_rotation_halts_without_reopening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ledger(tmp_path)
    tail = collector.LedgerTail(path)
    tail.identity = (-1, -1)
    try:
        with pytest.raises(ValueError, match="rotation_or_truncation"):
            tail.poll()
    finally:
        tail.close()


def mock_transport(
    monkeypatch: pytest.MonkeyPatch, payload: object, returncode: int = 0
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **options})
        return subprocess.CompletedProcess(command, returncode, json.dumps(payload), "")

    monkeypatch.setattr(transport.subprocess, "run", run)
    return calls


def test_public_client_fixed_endpoint_deadline_isolated_no_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = mock_transport(
        monkeypatch, {"status": "ok", "raw_body": raw(), "receive_time_utc": at(1.1).isoformat()}
    )
    result = transport.PublicHTTPClient().fetch("BTCUSDT", 3)
    assert result.receive_time_utc == at(1.1)
    call = calls[0]
    assert call["command"][:4] == [sys.executable, "-I", "-B", "-c"]
    assert call["command"][-2:] == ["BTCUSDT", "3"]
    assert call["timeout"] == 3 and call.get("shell", False) is False
    script = call["command"][4]
    assert 'HTTPSConnection("fapi.binance.com"' in script
    assert '"/fapi/v1/ticker/bookTicker?symbol="' in script
    for forbidden in ("os.environ", "apiKey", "Authorization", "/order", "requests", "redirect"):
        assert forbidden not in script


@pytest.mark.parametrize(
    "error,reason",
    [
        (subprocess.TimeoutExpired("child", 1), "public_request_deadline"),
        (FileNotFoundError("secret-not-logged"), "public_client_FileNotFoundError"),
    ],
)
def test_transport_deadline_or_missing_command(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(transport.subprocess, "run", run)
    with pytest.raises(transport.FetchError, match=reason) as caught:
        transport.PublicHTTPClient().fetch("BTCUSDT", 1)
    assert "secret-not-logged" not in str(caught.value)


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"status": "failed", "reason": "gaierror"}, "gaierror"),
        ({"status": "failed", "reason": "http_429", "retry_after_seconds": 30}, "http_429"),
        ({"status": "ok", "raw_body": "{}"}, "public_client_schema_invalid"),
        ([], "public_client_schema_invalid"),
    ],
)
def test_transport_resolution_rate_limit_and_bad_output(
    monkeypatch: pytest.MonkeyPatch, payload: object, reason: str
) -> None:
    mock_transport(monkeypatch, payload)
    with pytest.raises(transport.FetchError, match=reason) as caught:
        transport.PublicHTTPClient().fetch("BTCUSDT", 1)
    if reason == "http_429":
        assert caught.value.retry_after_seconds == 30


@pytest.mark.parametrize(
    "queue_name,counter",
    [
        ("quotes", "quote_queue_drops"),
        ("decisions", "decision_queue_drops"),
        ("notices", "notice_queue_drops"),
        ("records", "archive_queue_drops"),
    ],
)
def test_all_queues_bounded_drop_nonblocking_and_no_complete_coverage(
    tmp_path: Path, queue_name: str, counter: str
) -> None:
    value = observer(tmp_path, queue_capacity=1)
    queue = getattr(value, queue_name)
    assert value.enqueue(queue, object(), counter)
    assert not value.enqueue(queue, object(), counter)
    value.metrics.add("decisions_observed")
    value.metrics.add("matched_decisions")
    report = value.report()
    assert report["metrics"][counter] == 1
    assert report["pit_coverage_pct"] is None
    assert report["coverage_status"] == "PARTIAL_LOSS"


def test_consumer_loss_barrier_and_receive_clock_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path)
    records: list[object] = []
    monkeypatch.setattr(value, "record", records.append)
    value.metrics.add("notice_queue_drops")
    value.quotes.put(transport.ReceivedTicker("BTCUSDT", raw(), at(4)))
    value.decisions.put(decision())
    value.producers_done.set()
    current = state()
    value._consume(current)
    assert current.clock_fault
    assert any(
        isinstance(row, GapEvidence) and row.reason == "INGESTION_LOSS_BARRIER" for row in records
    )
    assert any(
        isinstance(row, DecisionL1Association) and row.reason == "LOCAL_CLOCK_REGRESSION"
        for row in records
    )
    assert value.consumer_done.is_set()


def test_market_regression_and_poll_gap_are_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path)
    records: list[object] = []
    monkeypatch.setattr(value, "record", records.append)
    current = state()
    current.accept(quote())
    value.quotes.put(transport.ReceivedTicker("BTCUSDT", raw(0.5), at(1)))
    value.producers_done.set()
    value._consume(current)
    assert any(
        isinstance(row, GapEvidence) and row.reason == "QUOTE_MONOTONICITY_FAILURE"
        for row in records
    )
    # A fresh snapshot after a polling gap can recover; missing message count remains unknown.
    value.now = lambda: at(10)
    value.quotes.put(transport.ReceivedTicker("BTCUSDT", raw(9), at(9.1)))
    value._consume(current)
    assert any(
        isinstance(row, GapEvidence)
        and row.reason == "POLLING_GAP"
        and row.missing_market_message_count is None
        for row in records
    )


def test_unexpected_worker_error_preserves_frames_not_secret_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    value = observer(tmp_path)

    def faulty() -> None:
        raise RuntimeError("sensitive-message-not-logged")

    value._guarded(faulty)
    assert value.stop_event.is_set()
    assert value.failure == "unexpected_worker_RuntimeError"
    assert "sensitive-message-not-logged" not in caplog.text
    assert caplog.records[0].traceback_frames


class FixtureClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
        self.calls += 1
        return transport.ReceivedTicker(symbol, raw(symbol=symbol), at(1.1))


def test_manual_session_no_write_no_ledger_changes_and_single_use(tmp_path: Path) -> None:
    path = ledger(tmp_path, decision().model_dump_json().encode() + b"\n")
    before = path.read_bytes()
    client = FixtureClient()
    value = collector.Collector(
        path,
        CollectorConfig(symbols=("BTCUSDT",)),
        runtime_root=tmp_path,
        client=client,
        now=lambda: at(3),
    )
    report = value.run(0.04)
    assert client.calls == 1
    assert report["write_performed"] is False
    assert report["source_status"] == "OBSERVED"
    assert report["metrics"].get("decisions_observed", 0) == 0
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["data"]
    assert report["execution_readiness"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"
    assert report["safety"]["observed_fills_provided"] is False
    with pytest.raises(ValueError, match="single_use"):
        value.run(0.01)


def test_reconnect_backoff_is_interruptible_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path, symbols=("BTCUSDT",))
    value.authority.start()

    class Flaky:
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            if value.metrics.snapshot()["public_requests"] == 1:
                raise transport.FetchError("http_429", 30)
            value.stop_event.set()
            return transport.ReceivedTicker(symbol, raw(), at(1.1))

    waits: list[float] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return value.stop_event.is_set()

    value.client = Flaky()
    monkeypatch.setattr(value.stop_event, "wait", wait)
    value._network()
    assert waits[0] == 30
    assert value.metrics.snapshot()["public_request_failures"] == 1
    assert value.metrics.snapshot()["quotes_discarded_at_shutdown"] == 1
    assert value.notices.get_nowait().reason == "http_429"


def test_successful_reconnection_count_and_quote_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path, symbols=("BTCUSDT",))
    value.authority.start()

    class Recovery:
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            if value.metrics.snapshot()["public_requests"] == 1:
                raise transport.FetchError("gaierror")
            return transport.ReceivedTicker(symbol, raw(), at(1.1))

    def wait(seconds: float) -> bool:
        if value.metrics.snapshot()["public_requests"] == 2:
            value.stop_event.set()
        return value.stop_event.is_set()

    value.client = Recovery()
    monkeypatch.setattr(value.stop_event, "wait", wait)
    value._network()
    assert value.metrics.snapshot()["reconnections"] == 1
    assert value.quotes.get_nowait().raw_body == raw()


def test_ctrl_c_drains_and_closes_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path, symbols=("BTCUSDT",))
    value.client = FixtureClient()
    original = value.stop_event.wait

    def wait(seconds: float) -> bool:
        if threading.current_thread() is threading.main_thread():
            raise KeyboardInterrupt
        return original(seconds)

    monkeypatch.setattr(value.stop_event, "wait", wait)
    report = value.run(0.03)
    assert report["metrics"]["keyboard_interrupts"] == 1
    assert report["shutdown_complete"]
    assert report["write_performed"] is False
    assert report["safety"]["runtime_activation_performed"] is False


def test_opt_in_archive_integration_drains_exact_association(tmp_path: Path) -> None:
    output = external(tmp_path)
    value = observer(tmp_path, symbols=("BTCUSDT",))
    value.archive = output

    class ProspectiveFixture:
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            value.decisions.put_nowait(decision(seconds=3))
            return transport.ReceivedTicker(symbol, raw(), at(1.1))

    value.client = ProspectiveFixture()
    report = value.run(0.05)
    assert report["status"] == "ok"
    assert report["shutdown_complete"]
    assert report["write_performed"]
    assert report["metrics"]["matched_decisions"] == 1
    assert report["pit_coverage_pct"] == 100
    manifest = json.loads((output.session / "manifest.json").read_bytes())
    assert manifest["summary"]["write_performed"] is True
    assert manifest["summary"]["shutdown_validation"] == "PROCESS_REPORT_REQUIRED"
    records = [
        record
        for item in manifest["segments"]
        for record in json.loads((output.session / item["filename"]).read_bytes())["records"]
    ]
    result = next(
        row for row in records if row["schema_version"] == "execution_decision_l1_association_v1"
    )
    assert result["decision_id"] == "decision-1" and result["candidate_id"] == "candidate-1"
    assert result["quote"]["available_at_utc"] == result["decision_timestamp_utc"]


def test_empty_opt_in_archive_reports_write_not_false_no_write(tmp_path: Path) -> None:
    output = external(tmp_path)
    value = observer(tmp_path)
    value.archive = output
    value.authority.start()
    value.consumer_done.set()
    value._write()
    manifest = json.loads((output.session / "manifest.json").read_bytes())
    assert manifest["summary"]["write_performed"] is True
    assert value.report()["write_performed"] is True


def test_thread_start_failure_is_blocked_and_ledger_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = observer(tmp_path)
    handles: list[collector.LedgerTail] = []
    original_tail = collector.LedgerTail

    def tail(path: Path) -> collector.LedgerTail:
        result = original_tail(path)
        handles.append(result)
        return result

    def failed_start(thread: threading.Thread) -> None:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(collector, "LedgerTail", tail)
    monkeypatch.setattr(threading.Thread, "start", failed_start)
    assert value.run(0.01)["reason"] == "authority_monitor_start_failure"
    assert not handles


def test_shutdown_budget_reports_blocked_not_complete(tmp_path: Path) -> None:
    release = threading.Event()
    returned = threading.Event()

    class Slow:
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            release.wait(5)
            returned.set()
            return transport.ReceivedTicker(symbol, raw(), at(1.1))

    value = collector.Collector(
        ledger(tmp_path),
        CollectorConfig(symbols=("BTCUSDT",), shutdown_seconds=1),
        runtime_root=tmp_path,
        client=Slow(),
    )
    try:
        report = value.run(0.03)
        assert report["status"] == "blocked"
        assert report["reason"] == "shutdown_incomplete"
        assert report["pit_coverage_pct"] is None
    finally:
        release.set()
        assert returned.wait(1)


def test_archive_authorization_paths_and_lazy_no_write(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="authorization"):
        external(tmp_path, authorized=False)
    with pytest.raises(ValueError, match="absolute"):
        archive.validate_external_root(Path("relative"), ())
    with pytest.raises(ValueError, match="project_or_runtime"):
        archive.validate_external_root(tmp_path / "runtime/out", (tmp_path / "runtime",))
    git = tmp_path / "checkout"
    git.mkdir()
    (git / ".git").write_text("gitdir: other", encoding="ascii")
    with pytest.raises(ValueError, match="inside_git"):
        archive.validate_external_root(git / "out", ())
    value = external(tmp_path)
    assert not value.session.exists()
    assert value.write_performed is False


def test_reparse_or_symlink_archive_is_blocked_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "external"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    with pytest.raises(ValueError, match="reparse_or_symlink"):
        external(tmp_path)
    assert not target.exists()


def test_external_archive_hash_chain_atomic_manifest_and_closed_guard(tmp_path: Path) -> None:
    value = external(tmp_path)
    value.record(quote())
    value.record(GapEvidence(observed_at_utc=at(3), reason="http_503"))
    value.close({"status": "ok", "execution_readiness": "BLOCKED_MISSING_EXECUTION_EVIDENCE"})
    manifest = json.loads((value.session / "manifest.json").read_bytes())
    assert manifest.pop("manifest_sha256") == canonical_sha256(manifest)
    previous = "0" * 64
    for entry in manifest["segments"]:
        content = (value.session / entry["filename"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        payload = json.loads(content)
        assert payload.pop("segment_sha256") == canonical_sha256(payload)
        assert payload["previous_segment_sha256"] == previous
        previous = entry["segment_sha256"]
        assert payload["schema_sha256"] == schema_sha256()
    assert value.write_performed
    assert not list(value.session.glob("*.tmp"))
    with pytest.raises(ValueError, match="already_closed"):
        value.record(quote())
    assert external(tmp_path).session != value.session


def test_archive_disk_budget_fail_closed_without_final_manifest(tmp_path: Path) -> None:
    value = external(tmp_path, max_bytes=1024)
    with pytest.raises(ValueError, match="byte_budget"):
        value.record(quote())
    assert value.write_performed
    assert not (value.session / "manifest.json").exists()


def test_archive_write_failure_blocks_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = external(tmp_path)
    value = observer(tmp_path)
    value.archive = output
    value.authority.start()
    value.records.put(quote())
    value.consumer_done.set()

    def failed(*args: object, **kwargs: object) -> None:
        raise PermissionError("not-logged-sensitive-message")

    monkeypatch.setattr(archive, "atomic_write_json", failed)
    value._write()
    assert value.report()["status"] == "blocked"
    assert value.report()["write_performed"] is True
    assert not (output.session / "manifest.json").exists()


def test_cli_default_preflight_no_network_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = ledger(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("collector must not run in preflight")

    monkeypatch.setattr(cli.Collector, "run", forbidden)
    assert cli.main(["--runtime-root", str(tmp_path), "--ledger-path", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["network_calls_executed"] is False
    assert report["write_performed"] is False
    assert report["collector_gate"] == "COLLECTOR_READY_FOR_OPT_IN"
    assert report["execution_readiness"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["data"]


@pytest.mark.parametrize(
    "flags",
    [
        ["--write-archive"],
        ["--write-archive", "--collect"],
        ["--output-root", "external"],
    ],
)
def test_cli_persistence_requires_explicit_complete_authorization(
    tmp_path: Path, flags: list[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--ledger-path", str(ledger(tmp_path)), *flags])
    assert caught.value.code == 2


def test_cli_schema_is_available_without_ledger_network_or_write(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--schema"]) == 0
    assert "$defs" in json.loads(capsys.readouterr().out)


def test_cli_invalid_config_fails_closed_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(["--ledger-path", str(ledger(tmp_path)), "--queue-capacity", "0", "--json"]) == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["write_performed"] is False
    assert report["collector_gate"] == "BLOCKED_COLLECTOR_PREFLIGHT"


def test_cli_external_archive_cannot_enter_runtime_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "runtime/data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    output = tmp_path / "runtime/data/reports/forbidden"
    assert (
        cli.main(
            [
                "--ledger-path",
                str(path),
                "--collect",
                "--write-archive",
                "--output-root",
                str(output),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["write_performed"] is False
    assert not output.exists()
