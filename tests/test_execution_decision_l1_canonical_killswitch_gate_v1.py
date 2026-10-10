from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

from scripts import collect_execution_decision_l1_prospective_v1 as cli
from smartcrypto.ops.execution_decision_l1 import (
    archive,
    collector,
    deployment_preflight,
    transport,
)
from smartcrypto.ops.execution_decision_l1 import kill_switch_authority as authority
from smartcrypto.ops.execution_decision_l1.contracts import CollectorConfig, Symbol
from smartcrypto.risk.kill_switch_guard import KillSwitchGuard, default_state

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def entry(enabled: bool = False) -> dict[str, object]:
    return {
        "enabled": enabled,
        "reason": "isolated_fixture",
        "actor": "pytest",
        "updated_at": T0.isoformat(),
    }


def payload(**changes: object) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runtime_mode": "paper",
        "updated_at": T0.isoformat(),
        "global": entry(),
        "symbols": {},
        **changes,
    }


def state_path(root: Path) -> Path:
    return root / "data/runtime/kill_switch.json"


def replace_state(root: Path, value: object) -> None:
    path = state_path(root)
    temporary = path.with_suffix(".fixture-temp")
    temporary.write_text(json.dumps(value), encoding="ascii")
    temporary.replace(path)


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "paper"
    ledger = root / authority.LEDGER_RELATIVE
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"")
    replace_state(root, payload())
    return root


@pytest.fixture(autouse=True)
def isolation(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    # Shorter isolated-fixture deadlines, not a production CLI/config override.
    monkeypatch.setattr(authority, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(authority, "READ_DEADLINE_SECONDS", 0.15)
    monkeypatch.setattr(collector, "POLL_SECONDS", 0.01)
    yield


class Client:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
        self.calls += 1
        now = datetime.now(timezone.utc)
        raw = json.dumps(
            {
                "symbol": symbol,
                "bidPrice": "100",
                "askPrice": "101",
                "bidQty": "1",
                "askQty": "1",
                "time": int(now.timestamp() * 1000),
            }
        )
        return transport.ReceivedTicker(symbol, raw, now)


def session(
    runtime: Path,
    *,
    client: transport.PublicClient | None = None,
    output: archive.ExternalArchive | None = None,
) -> collector.Collector:
    return collector.Collector(
        runtime / authority.LEDGER_RELATIVE,
        CollectorConfig(shutdown_seconds=1),
        runtime_root=runtime,
        client=client or Client(),
        archive=output,
    )


def external(tmp_path: Path, runtime: Path) -> archive.ExternalArchive:
    return archive.ExternalArchive(
        tmp_path / "external",
        authorized=True,
        forbidden_roots=(runtime,),
        segment_records=1,
        max_bytes=1024 * 1024,
    )


@pytest.mark.parametrize(
    "value,reason",
    [
        (payload(global_=True), None),  # Explicit valid clear, with unrelated metadata.
        (payload(**{"global": entry(True)}), "authority_global_blocked"),
        (payload(symbols={"ETHUSDT": entry(True)}), "authority_symbol_blocked"),
        ({}, "authority_explicit_schema_v1_paper_required"),
        (payload(schema_version=True), "authority_explicit_schema_v1_paper_required"),
        (payload(schema_version=2), "authority_explicit_schema_v1_paper_required"),
        (payload(enabled=False), "authority_explicit_schema_v1_paper_required"),
        (payload(runtime_mode="live"), "authority_explicit_schema_v1_paper_required"),
        (
            payload(**{"global": {**entry(), "enabled": "false"}}),
            "authority_explicit_boolean_entry_required",
        ),
        (
            payload(**{"global": {**entry(), "reason": "default_clear"}}),
            "authority_default_clear_not_authorization",
        ),
        (
            payload(symbols={"btcusdt": entry(), "BTCUSDT": entry(True)}),
            "authority_symbol_identity_collision",
        ),
        (payload(updated_at="not-a-clock"), "authority_read_ValueError"),
        (payload(**{"global": {"enabled": False}}), "authority_explicit_entry_metadata_required"),
    ],
)
def test_strict_schema_and_all_requested_symbols_fail_closed(
    runtime: Path, value: object, reason: str | None
) -> None:
    replace_state(runtime, value)
    if reason is None:
        assert (
            authority.inspect_authority(
                runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT", "ETHUSDT")
            )["snapshot"]["status"]
            == "CLEAR"
        )
    else:
        with pytest.raises(authority.AuthorityDenied, match=reason):
            authority.inspect_authority(
                runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT", "ETHUSDT")
            )


@pytest.mark.parametrize(
    "problem", ["global", "symbol", "missing", "corrupt", "oversize", "duplicate", "default_clear"]
)
def test_blocked_startup_no_network_no_write_and_no_authority_mutation(
    runtime: Path, tmp_path: Path, problem: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if problem == "global":
        replace_state(runtime, payload(**{"global": entry(True)}))
    elif problem == "symbol":
        replace_state(runtime, payload(symbols={"ETHUSDT": entry(True)}))
    elif problem == "missing":
        state_path(runtime).unlink()
    elif problem == "default_clear":
        replace_state(runtime, default_state("paper"))
    else:
        state_path(runtime).write_bytes(
            {
                "corrupt": b"{",
                "oversize": b" " * 65537,
                "duplicate": b'{"schema_version":1,"schema_version":1}',
            }[problem]
        )
    before = {str(path): path.read_bytes() for path in runtime.rglob("*") if path.is_file()}
    client = Client()
    output = external(tmp_path, runtime)
    report = session(runtime, client=client, output=output).run(0.03)
    assert report["status"] == "blocked" and report["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "BLOCKED"
    assert report["shutdown_complete"] and client.calls == 0
    assert not output.root.exists() and not report["write_performed"]
    assert (
        cli.main(
            [
                "--runtime-root",
                str(runtime),
                "--collect",
                "--write-archive",
                "--output-root",
                str(output.root),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["network_calls_executed"] is False
    assert before == {str(path): path.read_bytes() for path in runtime.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "root_kind", ["missing", "relative", "unspecified", "wrong_ledger", "symlink"]
)
def test_runtime_root_binding_cannot_be_inferred_or_bypassed(
    runtime: Path, root_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = {"missing": runtime / "absent", "relative": Path("relative"), "unspecified": None}.get(
        root_kind, runtime
    )
    ledger = runtime / authority.LEDGER_RELATIVE
    if root_kind == "wrong_ledger":
        ledger = runtime / "unrelated.jsonl"
        ledger.write_bytes(b"")
    if root_kind == "symlink":
        original = Path.lstat
        import stat
        import os

        def symlink(path: Path) -> os.stat_result:
            info = original(path)
            return os.stat_result((stat.S_IFLNK | 0o777, *info[1:])) if path == runtime else info

        monkeypatch.setattr(Path, "lstat", symlink)
    monkeypatch.setenv("KILL_SWITCH_ENABLED", "false")
    monkeypatch.setenv("L1_IGNORE_KILL_SWITCH", "true")
    client = Client()
    value = collector.Collector(ledger, CollectorConfig(), runtime_root=root, client=client)
    assert value.run(0.01)["status"] == "blocked"
    assert client.calls == 0


def test_permission_denied_no_guard_logging_or_secret_leak(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.open

    def denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == state_path(runtime):
            raise PermissionError("secret-not-logged")
        return original(path, *args, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("read-only adapter must never create a Guard/event logger")

    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(KillSwitchGuard, "__init__", forbidden)
    report = session(runtime).run(0.01)
    assert report["reason"] == "authority_read_PermissionError"
    assert "secret-not-logged" not in json.dumps(report)
    assert not (runtime / "data/runtime/kill_switch_guard_events.jsonl").exists()


@pytest.mark.parametrize("change", ["global", "symbol", "corrupt", "reader_error"])
def test_revocation_during_collection_stops_producers_and_prevents_first_persistence(
    runtime: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    output = external(tmp_path, runtime)
    read = authority.read_authority
    failed = threading.Event()

    def reader(*args: Any) -> authority.AuthoritySnapshot:
        if failed.is_set():
            raise RuntimeError("secret-reader-message")
        return read(*args)

    monkeypatch.setattr(authority, "read_authority", reader)

    class Revoke(Client):
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            result = super().fetch(symbol, timeout)
            if change == "global":
                replace_state(runtime, payload(**{"global": entry(True)}))
            elif change == "symbol":
                replace_state(runtime, payload(symbols={"ETHUSDT": entry(True)}))
            elif change == "corrupt":
                state_path(runtime).write_bytes(b"{")
            else:
                failed.set()
            return result

    client = Revoke()
    report = session(runtime, client=client, output=output).run(0.5)
    assert client.calls == 1 and report["status"] == "blocked"
    assert report["canonical_kill_switch"]["reason"] is not None
    assert report["shutdown_complete"] and report["canonical_kill_switch"]["shutdown_complete"]
    assert not output.root.exists() and not report["write_performed"]
    assert "secret-reader-message" not in json.dumps(report)


def test_revocation_after_session_metadata_prevents_segment_and_final_manifest(
    runtime: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = external(tmp_path, runtime)
    writer = archive.atomic_write_json

    def revoke(path: Path, value: object, **kwargs: Any) -> Any:
        result = writer(path, value, **kwargs)
        if path.name == "session.json":
            replace_state(runtime, payload(**{"global": entry(True)}))
        return result

    monkeypatch.setattr(archive, "atomic_write_json", revoke)
    report = session(runtime, output=output).run(0.2)
    assert report["reason"] == "authority_global_blocked"
    assert report["write_performed"] and report["shutdown_complete"]
    assert (output.session / "session.json").is_file()
    assert not list(output.session.glob("segment-*.json"))
    assert not (output.session / "manifest.json").exists()
    assert not output.pending


def test_stuck_authority_reader_expires_and_incomplete_shutdown_is_failure(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = authority.read_authority
    entered, release, hang = threading.Event(), threading.Event(), threading.Event()

    def blocked(*args: Any) -> authority.AuthoritySnapshot:
        if hang.is_set():
            entered.set()
            release.wait(5)
        return read(*args)

    monkeypatch.setattr(authority, "read_authority", blocked)

    class Hang(Client):
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            hang.set()
            return super().fetch(symbol, timeout)

    value = session(runtime, client=Hang())
    try:
        report = value.run(2)
        assert entered.is_set()
        assert report["status"] == "blocked" and report["reason"] == "shutdown_incomplete"
        assert report["canonical_kill_switch"]["reason"] == "authority_read_deadline"
        assert not report["shutdown_complete"]
        assert report["COLLECTOR_KILLSWITCH_ENFORCEMENT"] != "PASS"
    finally:
        release.set()
        assert value.authority.close(1)


def test_pending_request_after_revocation_is_not_a_successful_shutdown(runtime: Path) -> None:
    release, returned = threading.Event(), threading.Event()

    class Pending(Client):
        def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
            replace_state(runtime, payload(**{"global": entry(True)}))
            release.wait(5)
            returned.set()
            return super().fetch(symbol, timeout)

    value = session(runtime, client=Pending())
    try:
        report = value.run(0.5)
        assert report["reason"] == "shutdown_incomplete" and not report["shutdown_complete"]
        assert report["canonical_kill_switch"]["reason"] == "authority_global_blocked"
    finally:
        release.set()
        assert returned.wait(1)


def test_clear_fixture_default_cli_no_network_no_write(
    runtime: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("default CLI must not instantiate collection or persistence")

    monkeypatch.setattr(cli, "Collector", forbidden)
    monkeypatch.setattr(cli, "ExternalArchive", forbidden)
    assert cli.main(["--runtime-root", str(runtime), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["network_calls_executed"] and not report["write_performed"]
    assert report["canonical_kill_switch"]["snapshot"]["status"] == "CLEAR"
    assert report["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "NOT_RUN"
    assert session(runtime).run(0.03)["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "PASS"


def test_unrequested_symbol_uses_existing_guard_policy(runtime: Path) -> None:
    replace_state(runtime, payload(symbols={"ETHUSDT": entry(True)}))
    assert (
        authority.inspect_authority(runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT",))[
            "snapshot"
        ]["status"]
        == "CLEAR"
    )


def test_preflight_diagnostic_cannot_bypass_blocked_authority(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replace_state(runtime, payload(**{"global": entry(True)}))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("blocked preflight must not make a network call")

    monkeypatch.setattr(deployment_preflight, "PublicHTTPClient", forbidden)
    report = deployment_preflight.audit_deployment(
        project_root=Path(__file__).resolve().parents[1],
        runtime_root=runtime,
        archive_root=None,
        diagnose_public_connectivity=True,
    )
    assert not report["network_calls_executed"]
    assert report["decision"] == "BLOCKED_PREFLIGHT_FAILURE"
    assert report["EXECUTION_READINESS"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"


def test_cancelled_public_child_is_killed_with_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = threading.Event()
    actions: list[object] = []

    class Process:
        returncode = -9
        stdout = stderr = None

        def __enter__(self) -> Process:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def communicate(self, timeout: float) -> tuple[str, str]:
            actions.append(timeout)
            stopped.set()
            raise subprocess.TimeoutExpired("fixture", timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            actions.append("kill")

        def wait(self, timeout: float) -> int:
            actions.append(("wait", timeout))
            return -9

    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: Process())
    client = transport.PublicHTTPClient()
    client.stop_event = stopped
    with pytest.raises(transport.FetchError, match="public_request_cancelled"):
        client.fetch("BTCUSDT", 1)
    assert actions[0] == 0.05 and actions[1:] == ["kill", ("wait", 0.5)]


def test_failed_child_cancellation_is_incomplete_shutdown(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        stdout = stderr = None

        def communicate(self, timeout: float) -> tuple[str, str]:
            replace_state(runtime, payload(**{"global": entry(True)}))
            raise subprocess.TimeoutExpired("fixture", timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            raise PermissionError("not-logged-secret")

    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: Process())
    value = session(runtime, client=transport.PublicHTTPClient())
    report = value.run(0.5)
    assert report["reason"] == "shutdown_incomplete"
    assert not report["shutdown_complete"]
    assert report["metrics"]["shutdown_public_children_unconfirmed"] == 1
    assert report["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "BLOCKED"


def test_unstarted_report_cannot_claim_enforcement_pass(runtime: Path) -> None:
    assert session(runtime).report()["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "NOT_RUN"


def test_changed_authority_during_read_is_not_a_clear_snapshot(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fstat = authority.os.fstat
    reads = 0

    def change(descriptor: int) -> Any:
        nonlocal reads
        result = fstat(descriptor)
        reads += 1
        if reads == 2:
            state_path(runtime).write_text(
                json.dumps(payload(**{"global": entry(True)})), encoding="ascii"
            )
        return result

    monkeypatch.setattr(authority.os, "fstat", change)
    with pytest.raises(authority.AuthorityDenied, match="authority_changed_during_read"):
        authority.read_authority(runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT",))


@pytest.mark.parametrize(
    "metadata",
    [
        {"updated_at": None},
        {"timestamp_utc": "2026-07-02T00:00:00Z"},
        {"operator": "different-actor"},
    ],
)
def test_canonical_entry_metadata_cannot_be_defaulted_or_conflicting(
    runtime: Path, metadata: dict[str, object]
) -> None:
    replace_state(runtime, payload(**{"global": {**entry(), **metadata}}))
    with pytest.raises(authority.AuthorityDenied):
        authority.inspect_authority(runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT",))


def test_nonstandard_json_constant_is_corruption(runtime: Path) -> None:
    state_path(runtime).write_text(json.dumps(payload(extra=float("nan"))), encoding="ascii")
    with pytest.raises(authority.AuthorityDenied, match="authority_invalid_json_constant"):
        authority.inspect_authority(runtime, runtime / authority.LEDGER_RELATIVE, ("BTCUSDT",))


def test_revocation_between_ledger_open_and_producer_creation(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tail = collector.LedgerTail
    handles: list[collector.LedgerTail] = []

    def revoke(path: Path) -> collector.LedgerTail:
        result = tail(path)
        handles.append(result)
        replace_state(runtime, payload(**{"global": entry(True)}))
        return result

    monkeypatch.setattr(collector, "LedgerTail", revoke)
    client = Client()
    report = session(runtime, client=client).run(0.03)
    assert report["reason"] == "authority_global_blocked" and client.calls == 0
    assert handles[0].handle.closed and report["shutdown_complete"]


def test_ctrl_c_during_authority_startup_closes_reader_without_network(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = authority.AuthorityMonitor.start

    def interrupt(self: authority.AuthorityMonitor) -> None:
        original(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(authority.AuthorityMonitor, "start", interrupt)
    client = Client()
    report = session(runtime, client=client).run(0.03)
    assert report["reason"] == "startup_KeyboardInterrupt"
    assert report["metrics"]["keyboard_interrupts"] == 1
    assert client.calls == 0 and report["shutdown_complete"]


def test_failed_producer_start_closes_ledger_and_authority_reader(
    runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = threading.Thread.start

    def start(thread: threading.Thread) -> None:
        if thread.name != "l1-canonical-authority":
            raise RuntimeError("fixture-start-failure")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    report = session(runtime).run(0.03)
    assert report["reason"] == "thread_start_failure"
    assert report["canonical_kill_switch"]["shutdown_complete"]
    assert report["shutdown_complete"] and not report["network_calls_executed"]
