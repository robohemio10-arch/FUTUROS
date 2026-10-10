from __future__ import annotations

import json
import builtins
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import audit_execution_decision_l1_deployment_preflight_v1 as cli
from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1.contracts import (
    RuntimeIdentityEvidenceV1,
)
from smartcrypto.execution.decision_ledger_v4_2.contracts import seal_decision_record
from smartcrypto.ops.execution_decision_l1 import deployment_preflight as audit
from smartcrypto.ops.execution_decision_l1.collector import Collector
from smartcrypto.ops.execution_decision_l1.contracts import (
    CollectorConfig,
    Symbol,
    canonical_sha256,
)
from smartcrypto.ops.execution_decision_l1.transport import FetchError, ReceivedTicker

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
FIXTURE = ROOT / "tests/fixtures/decision_ledger_v4_2/valid_decision.json"


def record(**changes: object) -> bytes:
    payload = json.loads(FIXTURE.read_bytes())
    payload.pop("payload_sha256")
    payload.update(
        decision_timestamp=NOW - timedelta(seconds=1), feature_timestamp=NOW - timedelta(seconds=2)
    )
    payload.update(changes)
    return seal_decision_record(payload).model_dump_json().encode() + b"\n"


def runtime(tmp_path: Path, *, kill: object = False) -> Path:
    root = tmp_path / "paper"
    path = root / audit.LEDGER_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_bytes(record())
    (root / "data/runtime/kill_switch.json").write_text(
        json.dumps({"runtime_mode": "paper", "enabled": kill, "reason": "unit-test"}),
        encoding="utf-8",
    )
    return root


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit,
        "inspect_current_identity",
        lambda: RuntimeIdentityEvidenceV1(
            source="windows_admin_api",
            verified=True,
            elevated=False,
            effective_uid=None,
            reason="non_root_identity_verified",
        ),
    )
    monkeypatch.setattr(
        audit,
        "local_clock_diagnostic",
        lambda: {
            "status": "UNPROVEN",
            "reason": "cross_domain_clock_alignment_not_proven",
            "network_executed": False,
        },
    )
    for key in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"):
        monkeypatch.delenv(key, raising=False)


def run(root: Path | None = None, archive: Path | None = None) -> dict[str, Any]:
    return audit.audit_deployment(
        project_root=ROOT, runtime_root=root, archive_root=archive, as_of_utc=NOW
    )


def test_default_has_no_network_collector_writer_or_runtime_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = runtime(tmp_path)
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unexpected collector or public transport initialization")

    monkeypatch.setattr(Collector, "__init__", forbidden)
    monkeypatch.setattr(audit, "PublicHTTPClient", forbidden)
    result = run(root, tmp_path / "external-not-created")
    after = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert not (tmp_path / "external-not-created").exists()
    assert result["network_calls_executed"] is False
    assert result["write_performed"] is False
    assert result["collector_initialized"] is False
    assert result["collection_started"] is False
    assert result["manual_activation_allowed"] is False
    assert result["EXECUTION_READINESS"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"
    assert result["report_sha256"] == canonical_sha256(
        {key: value for key, value in result.items() if key != "report_sha256"}
    )


def test_institutional_kill_switch_is_inspected_never_cleared(tmp_path: Path) -> None:
    root = runtime(tmp_path, kill=True)
    path = root / "data/runtime/kill_switch.json"
    original = path.read_bytes()
    result = run(root)
    assert result["decision"] == "BLOCKED_PREFLIGHT_FAILURE"
    assert "canonical_paper_kill_switch_enabled" in result["failures"]
    assert "collector_does_not_consume_canonical_kill_switch" in result["failures"]
    assert path.read_bytes() == original


def test_real_code_static_contracts_do_not_claim_external_kill_switch() -> None:
    metadata, checks = audit.inspect_code(ROOT)
    by_id = {check.check_id: check for check in checks}
    assert by_id["opt_in_default"].status == "PROVEN"
    assert by_id["collector_stop_contract"].status == "PROVEN"
    assert by_id["external_kill_switch"].status == "FAILED"
    assert len(metadata["source_sha256"]) == 5
    assert metadata["paper_or_collector_initialized"] is False


def test_ledger_seal_identity_counts_and_no_fill_promotion(tmp_path: Path) -> None:
    path = runtime(tmp_path) / audit.LEDGER_RELATIVE
    data = audit.inspect_ledger(path, NOW)
    assert data["decision_count"] == 1
    assert data["trade_link_count"] == 0
    assert data["identity_collisions"] == 0
    assert data["latest_decision"]["decision_id"] == "evt_decision_000001"
    assert data["latest_decision"]["candidate_id"] == "candidate_000001"
    assert data["latest_decision_age_seconds"] == 1
    assert data["classification"] == "OBSERVED_DECISIONS_NOT_EXCHANGE_FILLS"
    assert data["used_for_trading_replay"] is False


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "empty",
        "partial",
        "invalid_hash",
        "future",
        "collision",
        "idempotency",
        "oversized",
        "record_budget",
    ],
)
def test_missing_corrupt_future_or_oversized_ledger_is_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = runtime(tmp_path)
    path = root / audit.LEDGER_RELATIVE
    if mode == "missing":
        path.unlink()
    elif mode == "empty":
        path.write_bytes(b"")
    elif mode == "partial":
        path.write_bytes(record().rstrip(b"\n"))
    elif mode == "invalid_hash":
        payload = json.loads(record())
        payload["payload_sha256"] = "a" * 64
        path.write_bytes(json.dumps(payload).encode() + b"\n")
    elif mode == "future":
        path.write_bytes(record(decision_timestamp=NOW + timedelta(seconds=1)))
    elif mode == "collision":
        path.write_bytes(record() + record(candidate_id="other"))
    elif mode == "idempotency":
        path.write_bytes(record() + record(event_id="other-event"))
    elif mode == "oversized":
        path.write_bytes(b"x" * (audit.MAX_LINE_BYTES + 1) + b"\n")
    else:
        monkeypatch.setattr(audit, "MAX_LEDGER_RECORDS", 1)
        path.write_bytes(record() + record())
    result = run(root)
    check = next(item for item in result["checks"] if item["check_id"] == "decision_ledger")
    assert check["status"] == "FAILED"
    assert result["decision"] == "BLOCKED_PREFLIGHT_FAILURE"


def test_duplicate_same_seal_counted_not_synthetic_collision(tmp_path: Path) -> None:
    path = runtime(tmp_path) / audit.LEDGER_RELATIVE
    path.write_bytes(record() + record())
    assert audit.inspect_ledger(path, NOW)["exact_duplicate_records"] == 1


def test_changing_source_fail_closed_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = runtime(tmp_path)
    stable = audit._read_stable

    def changing(path: Path) -> tuple[bytes, dict[str, Any]]:
        if path.name == "decision_ledger_v4_2.jsonl":
            raise ValueError("sensitive-not-logged")
        return stable(path)

    monkeypatch.setattr(audit, "_read_stable", changing)
    result = run(root)
    assert "decision_ledger_ValueError" in result["failures"]
    assert "sensitive-not-logged" not in json.dumps(result)


@pytest.mark.parametrize("value", ["false", None, {}, 0])
def test_invalid_kill_switch_never_defaults_to_clear(tmp_path: Path, value: object) -> None:
    root = runtime(tmp_path, kill=value)
    result = run(root)
    assert any(
        item["check_id"] == "paper_kill_switch" and item["status"] == "FAILED"
        for item in result["checks"]
    )


def test_missing_kill_switch_does_not_create_default_state(tmp_path: Path) -> None:
    root = runtime(tmp_path)
    path = root / "data/runtime/kill_switch.json"
    path.unlink()
    assert run(root)["decision"] == "BLOCKED_PREFLIGHT_FAILURE"
    assert not path.exists()


def test_existing_writable_archive_hint_never_proves_write_contract(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    facts, check = audit.inspect_archive(root, (tmp_path / "paper", ROOT), CollectorConfig())
    assert check.status == "PROVEN"
    assert facts["write_permission"] == "UNPROVEN"
    assert facts["atomic_write_fsync"] == "UNPROVEN"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "mode", ["relative", "runtime", "git", "not_directory", "low_space", "reparse"]
)
def test_external_archive_path_or_capacity_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = runtime(tmp_path)
    path = tmp_path / "archive"
    if mode == "relative":
        path = Path("relative")
    elif mode == "runtime":
        path = root / "data/reports"
    elif mode == "git":
        path.mkdir()
        (path / ".git").write_text("gitdir: another", encoding="ascii")
    elif mode == "not_directory":
        path.write_text("file", encoding="ascii")
    elif mode == "low_space":
        usage = audit.shutil.disk_usage(tmp_path)
        monkeypatch.setattr(audit.shutil, "disk_usage", lambda target: type(usage)(1024, 1024, 0))
    else:
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda target: target == path or original(target))
    result = run(root, path)
    check = next(item for item in result["checks"] if item["check_id"] == "archive_path")
    assert check["status"] == "FAILED"
    assert result["manual_activation_allowed"] is False


def all_proven() -> list[audit.Check]:
    code = {"code_contracts", "opt_in_default", "collector_stop_contract", "external_kill_switch"}
    return [
        audit.Check(
            key, "PROVEN", "unit-fixture-not-a-host-report", "CODE" if key in code else "HOST"
        )
        for key in sorted(audit.REQUIRED_CHECKS)
    ]


def test_gate_reducer_requires_complete_unique_host_checklist() -> None:
    checks = all_proven()
    assert audit.decide(checks) == "PREFLIGHT_READY_FOR_MANUAL_OPT_IN"
    assert audit.decide(checks[:-1]) == "BLOCKED_HOST_UNVERIFIED"
    assert audit.decide(checks + [checks[0]]) == "BLOCKED_HOST_UNVERIFIED"
    assert (
        audit.decide([audit.Check(row.check_id, row.status, row.reason, "CODE") for row in checks])
        == "BLOCKED_HOST_UNVERIFIED"
    )


@pytest.mark.parametrize("key", sorted(audit.REQUIRED_CHECKS))
def test_every_mandatory_unknown_blocks_and_critical_failure_has_priority(key: str) -> None:
    checks = [
        audit.Check(
            row.check_id,
            "UNPROVEN" if row.check_id == key else row.status,
            row.reason,
            row.evidence_scope,
        )
        for row in all_proven()
    ]
    assert audit.decide(checks) == "BLOCKED_HOST_UNVERIFIED"
    checks.append(audit.Check("critical", "FAILED", "confirmed-failure", "HOST"))
    assert audit.decide(checks) == "BLOCKED_PREFLIGHT_FAILURE"


def test_missing_host_root_and_paths_are_explicitly_unverified() -> None:
    result = run()
    assert any(
        row["check_id"] == "decision_ledger" and row["status"] == "UNPROVEN"
        for row in result["checks"]
    )
    assert result["host"]["target_binding"] == "LOCAL_FILES_ONLY_NOT_RUNNING_PAPER_PROCESS"
    assert result["sources"]["transport"]["network_calls_executed"] is False


@pytest.mark.parametrize(
    "flag", ["LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"]
)
def test_unsafe_auditor_environment_blocks_without_dumping_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv(flag, "secret-unknown-value")
    monkeypatch.setenv("API_SECRET", "never-include-this")
    result = run(runtime(tmp_path))
    assert "unsafe_auditor_authority_flags" in result["failures"]
    assert "secret-unknown-value" not in json.dumps(result)
    assert "never-include-this" not in json.dumps(result)


def test_elevated_identity_is_critical_and_unverified_is_not_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "inspect_current_identity",
        lambda: RuntimeIdentityEvidenceV1(
            source="windows_admin_api",
            verified=True,
            elevated=True,
            effective_uid=None,
            reason="elevated_identity_detected",
        ),
    )
    assert "elevated_identity_detected" in run()["failures"]


class PublicFixture:
    def __init__(
        self, *, age: float = 0, error: Exception | None = None, symbol: str = "BTCUSDT"
    ) -> None:
        self.calls = 0
        self.age = age
        self.error = error
        self.symbol = symbol

    def fetch(self, symbol: Symbol, timeout: float) -> ReceivedTicker:
        self.calls += 1
        assert 0 < timeout <= 10
        if self.error is not None:
            raise self.error
        received = datetime.now(timezone.utc)
        body = json.dumps(
            {
                "symbol": self.symbol,
                "bidPrice": "100",
                "askPrice": "101",
                "bidQty": "2",
                "askQty": "3",
                "time": int((received.timestamp() - self.age) * 1000),
            }
        )
        return ReceivedTicker(symbol, body, received)


def test_public_diagnostic_not_instantiated_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        pytest.fail("network default must stay off")

    monkeypatch.setattr(audit, "PublicHTTPClient", forbidden)
    result = audit.public_diagnostic(CollectorConfig(), authorized=False)
    assert result["status"] == "UNPROVEN"
    assert result["network_calls_executed"] is False


@pytest.mark.parametrize(
    "age,error,symbol,expected",
    [
        (0, None, "BTCUSDT", "PROVEN"),
        (10, None, "BTCUSDT", "FAILED"),
        (0, FetchError("http_429-sensitive"), "BTCUSDT", "FAILED"),
        (0, FetchError("public_request_deadline"), "BTCUSDT", "FAILED"),
        (0, None, "ETHUSDT", "FAILED"),
    ],
)
def test_opt_in_one_public_get_only_no_market_message_in_output(
    age: float, error: Exception | None, symbol: str, expected: str
) -> None:
    client = PublicFixture(age=age, error=error, symbol=symbol)
    result = audit.public_diagnostic(CollectorConfig(), authorized=True, client=client)
    assert client.calls == 1
    assert result["status"] == expected
    assert result["market_messages_archived"] is False
    assert "bidPrice" not in json.dumps(result) and "raw_body" not in result
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize(
    "output,exit_code,expected",
    [
        (b"Leap Indicator: 3 (not synchronized)", 0, "FAILED"),
        (b"Leap Indicator: 0", 0, "UNPROVEN"),
        (b"localized unrecognized output", 0, "UNPROVEN"),
        (b"", 5, "UNPROVEN"),
    ],
)
def test_clock_exit_zero_is_not_alignment_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes, exit_code: int, expected: str
) -> None:
    monkeypatch.setattr(audit, "WINDOWS_HOST", True)
    original = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda target: target.name == "w32tm.exe" or original(target)
    )
    calls: list[object] = []

    def command(argv: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, options))
        assert argv[1:] == ["/query", "/status", "/verbose"]
        assert options["timeout"] == 3 and "shell" not in options
        return subprocess.CompletedProcess(argv, exit_code, output, b"secret-output-not-reported")

    monkeypatch.setattr(audit.subprocess, "run", command)
    # Access the original implementation rather than the autouse host fixture.
    result = ORIGINAL_CLOCK()
    assert calls
    assert result["status"] == expected
    assert result["cross_domain_offset_bound_seconds"] is None
    assert "secret-output" not in json.dumps(result)


ORIGINAL_CLOCK = audit.local_clock_diagnostic


def test_cli_no_write_or_collect_flags_and_blocked_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--runtime-root", str(runtime(tmp_path)), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["write_performed"] is False
    assert result["collection_started"] is False
    assert result["manual_activation_allowed"] is False
    for forbidden in ("--write", "--collect", "--assume-safe"):
        with pytest.raises(SystemExit) as caught:
            cli.main([forbidden])
        assert caught.value.code == 2


def test_drifted_project_binding_is_critical(tmp_path: Path) -> None:
    result = audit.audit_deployment(
        project_root=tmp_path, runtime_root=None, archive_root=None, as_of_utc=NOW
    )
    assert "project_root_not_loaded_auditor_checkout" in result["failures"]
    assert result["manual_activation_allowed"] is False


def test_missing_dependency_returns_structured_blocked_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = builtins.__import__

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "smartcrypto.ops.execution_decision_l1.deployment_preflight":
            raise ModuleNotFoundError("do-not-log-this-secret")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    assert cli.main(["--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "BLOCKED_PREFLIGHT_FAILURE"
    assert report["network_calls_executed"] is False
    assert "do-not-log" not in json.dumps(report)


def test_unavailable_archive_drive_no_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit, "validate_external_root", lambda path, forbidden: path)
    monkeypatch.setattr(Path, "exists", lambda target: False)
    with pytest.raises(audit.AuditFailure, match="existing_ancestor_unavailable"):
        audit.inspect_archive(tmp_path / "absent", (), CollectorConfig())


def test_known_unsynchronized_clock_is_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        audit,
        "local_clock_diagnostic",
        lambda: {
            "status": "FAILED",
            "reason": "local_clock_unsynchronized",
            "network_executed": False,
        },
    )
    report = run(runtime(tmp_path))
    assert "local_clock_unsynchronized" in report["failures"]
    assert report["decision"] == "BLOCKED_PREFLIGHT_FAILURE"


def test_relative_runtime_root_has_full_unique_blocked_checklist() -> None:
    report = run(Path("relative"))
    checks = report["checks"]
    assert {item["check_id"] for item in checks} == audit.REQUIRED_CHECKS
    assert len(checks) == len(audit.REQUIRED_CHECKS)
    assert report["manual_activation_allowed"] is False
