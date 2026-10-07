"""Offline tests for non-authoritative Windows/Docker DNS telemetry."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.capture_windows_docker_dns_causal_telemetry_v1 import main
from smartcrypto.ops.dns_causal_telemetry import (
    SystemProbes,
    classify_sample,
    collect_sample,
    persist_sample,
)
from smartcrypto.ops.dns_causal_telemetry import collector


class FakeProbes:
    def __init__(
        self,
        *,
        host_status: str = "ok",
        direct_status: str = "ok",
        container_status: str = "ok",
    ) -> None:
        self.host_status = host_status
        self.direct_status = direct_status
        self.container_status = container_status
        self.requested_container: str | None = None

    def host_resolution(self, timeout_seconds: float) -> dict[str, Any]:
        return {"status": self.host_status, "addresses": ["1.2.3.4"]}

    def windows_network(self, timeout_seconds: float) -> dict[str, Any]:
        return {"status": "ok", "dns_servers": ["1.1.1.1"], "adapters": []}

    def direct_dns(self, server: str, timeout_seconds: float) -> dict[str, Any]:
        return {"server": server, "status": self.direct_status, "addresses": ["1.2.3.4"]}

    def docker(self, container_name: str | None, timeout_seconds: float) -> dict[str, Any]:
        self.requested_container = container_name
        return {"status": self.container_status, "container_name": container_name}

    def windows_events(self, now: datetime, timeout_seconds: float) -> dict[str, Any]:
        return {"status": "ok", "channels": []}


@pytest.mark.parametrize(
    ("host", "direct", "container", "expected"),
    [
        ("ok", "ok", "ok", "HOST_AND_DIRECT_DNS_OK"),
        ("failed", "ok", "failed", "HOST_RESOLUTION_FAILED_DIRECT_DNS_OK"),
        ("ok", "unavailable", "failed", "HOST_OK_CONTAINER_FAILED"),
        ("failed", "failed", "unavailable", "HOST_AND_DIRECT_DNS_FAILED"),
        ("failed", "unavailable", "failed", "HOST_AND_CONTAINER_FAILED"),
        ("unavailable", "unavailable", "ok", "CONTAINER_OK"),
        ("unavailable", "unavailable", "unavailable", "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_deterministic_classification(
    host: str, direct: str, container: str, expected: str
) -> None:
    result = classify_sample(
        {"status": host}, [{"status": direct}], {"status": container}
    )
    assert result == expected


def test_host_and_container_ok_collection_has_no_root_cause_claim() -> None:
    fake = FakeProbes()
    sample = collect_sample(
        fake,
        container_name="exact-supervisor",
        timeout_seconds=5,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    assert sample["target"] == "fapi.binance.com"
    assert sample["classification"] == "HOST_AND_DIRECT_DNS_OK"
    assert sample["classification_is_root_cause"] is False
    assert fake.requested_container == "exact-supervisor"
    assert sample["source_reads_only"] is True
    assert sample["writes_runtime"] is False
    assert sample["runtime_changed"] is False
    assert sample["paper_behavior_changed"] is False
    assert sample["sends_orders"] is False


def test_partial_direct_dns_does_not_claim_direct_dns_ok() -> None:
    assert classify_sample(
        {"status": "failed"},
        [{"status": "ok"}, {"status": "failed"}],
        {"status": "unavailable"},
    ) == "INSUFFICIENT_EVIDENCE"


def test_missing_command_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _: None)
    probes = SystemProbes()
    assert probes.direct_dns("1.1.1.1", 1)["status"] == "unavailable"
    assert probes.docker("exact-container", 1)["error"] == "docker_missing"


def test_docker_requires_exact_identity_and_sanitizes_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _: "docker")
    calls: list[str] = []
    raw_resolver = "nameserver 127.0.0.11\nsearch internal.example\n"

    def fake_run(self: SystemProbes, args: list[str], timeout: float) -> dict[str, Any]:
        calls.append(args[1])
        if args[1] == "version":
            return {"status": "ok", "stdout": "29.0.0\n", "duration_ms": 1}
        if args[1] == "inspect":
            return {
                "status": "ok",
                "stdout": "/supervisor\ntrue\n{\"paper_net\":{}}\n",
                "duration_ms": 1,
            }
        return {
            "status": "ok",
            "stdout": json.dumps({
                "resolver_status": "ok",
                "resolver_sha256": "a" * 64,
                "resolver_content": raw_resolver,
                "resolution_status": "ok",
                "addresses": ["1.2.3.4"],
            }),
            "duration_ms": 1,
        }

    monkeypatch.setattr(SystemProbes, "_run", fake_run)
    result = SystemProbes().docker("supervisor", 5)
    assert calls == ["version", "inspect", "exec"]
    assert result["engine_status"] == "ok"
    assert result["container_name"] == "supervisor"
    assert result["networks"] == ["paper_net"]
    assert result["resolver_content_sanitized"] == [
        "nameserver 127.0.0.11", "search <redacted>"
    ]
    assert "internal.example" not in json.dumps(result)


def test_docker_missing_container_is_distinct_from_engine_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _: "docker")

    def fake_run(self: SystemProbes, args: list[str], timeout: float) -> dict[str, Any]:
        if args[1] == "version":
            return {"status": "ok", "stdout": "29.0.0\n", "duration_ms": 1}
        return {"status": "unavailable", "error": "exit_1:No such object: supervisor", "duration_ms": 1}

    monkeypatch.setattr(SystemProbes, "_run", fake_run)
    result = SystemProbes().docker("supervisor", 5)
    assert result["engine_status"] == "ok"
    assert result["container_status"] == "missing"


def test_docker_inspect_name_mismatch_never_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _: "docker")
    commands: list[str] = []

    def fake_run(self: SystemProbes, args: list[str], timeout: float) -> dict[str, Any]:
        commands.append(args[1])
        if args[1] == "version":
            return {"status": "ok", "stdout": "29.0.0\n", "duration_ms": 1}
        return {"status": "ok", "stdout": "/other-container\ntrue\n{}\n", "duration_ms": 1}

    monkeypatch.setattr(SystemProbes, "_run", fake_run)
    result = SystemProbes().docker("supervisor", 5)
    assert result["error"] == "container_identity_unverified"
    assert commands == ["version", "inspect"]


def test_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(collector.subprocess, "run", timed_out)
    assert SystemProbes().host_resolution(1)["status"] == "timeout"


def test_permission_denied_event_channel_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"channel": "Microsoft-Windows-DNS-Client/Operational", "status": "unavailable",
                "reason": "UnauthorizedAccessException", "events": []}]
    monkeypatch.setattr(
        SystemProbes,
        "_powershell",
        lambda self, script, timeout: {"status": "ok", "stdout": json.dumps(payload), "duration_ms": 1},
    )
    evidence = SystemProbes().windows_events(datetime(2026, 9, 30, tzinfo=timezone.utc), 1)
    assert evidence["channels"][0]["status"] == "unavailable"
    assert evidence["channels"][0]["reason"] == "UnauthorizedAccessException"


def test_resolver_and_error_sanitization() -> None:
    content = "# ExtServers: [host(192.168.65.7)]\nnameserver 127.0.0.11\nsearch private.internal\noptions ndots:0 rotate\n"
    assert collector.sanitize_resolver(content) == [
        "nameserver 127.0.0.11", "search <redacted>", "options ndots:0"
    ]
    error = "proxy=http://user:password@example.test token=synthetic-secret"
    sanitized = collector.sanitize_error(error)
    assert "synthetic-secret" not in sanitized
    assert "user:password" not in sanitized
    assert "synthetic-bearer" not in collector.sanitize_error("Bearer synthetic-bearer")


def test_network_profile_identity_is_hashed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"adapter": "Ethernet", "profile_id": "private-profile-id",
                "dns_servers": ["1.1.1.1", "fe80::1%unsafe';Write-Output secret;#"],
                "default_routes": []}]
    monkeypatch.setattr(
        SystemProbes,
        "_powershell",
        lambda self, script, timeout: {"status": "ok", "stdout": json.dumps(payload), "duration_ms": 1},
    )
    result = SystemProbes().windows_network(1)
    assert result["dns_servers"] == ["1.1.1.1"]
    assert result["invalid_dns_server_count"] == 1
    assert result["adapters"][0]["dns_servers"] == ["1.1.1.1", "<invalid>"]
    assert len(result["adapters"][0]["profile_id_sha256"]) == 64
    assert "private-profile-id" not in json.dumps(result)
    assert "Write-Output" not in json.dumps(result)


def test_unsafe_scoped_dns_server_never_reaches_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SystemProbes, "_powershell",
        lambda self, script, timeout: pytest.fail("unsafe server reached PowerShell"),
    )
    result = SystemProbes().direct_dns("fe80::1%unsafe';Write-Output secret;#", 1)
    assert result["status"] == "unavailable"
    assert result["error"] == "invalid_dns_server"


def test_jsonl_append_and_atomic_snapshot(tmp_path: Path) -> None:
    first = collect_sample(
        FakeProbes(), container_name=None, timeout_seconds=1,
        now=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc),
    )
    second = collect_sample(
        FakeProbes(host_status="failed"), container_name=None, timeout_seconds=1,
        now=datetime(2026, 9, 30, 0, 1, tzinfo=timezone.utc),
    )
    jsonl_path, snapshot_path = persist_sample(tmp_path / "external", first)
    persist_sample(tmp_path / "external", second)
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["sampled_at_utc"] for line in lines] == [
        "2026-09-30T00:00:00Z", "2026-09-30T00:01:00Z"
    ]
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == second
    assert sorted(path.name for path in (tmp_path / "external").iterdir()) == [
        "dns_causal_telemetry_v1.jsonl", "dns_causal_telemetry_v1.latest.json"
    ]


def test_snapshot_failure_preserves_append_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample = {"classification": "INSUFFICIENT_EVIDENCE"}
    monkeypatch.setattr(collector.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("replace_failed")))
    with pytest.raises(OSError, match="replace_failed"):
        persist_sample(tmp_path / "external", sample)
    destination = tmp_path / "external"
    assert json.loads((destination / "dns_causal_telemetry_v1.jsonl").read_text()) == sample
    assert sorted(path.name for path in destination.iterdir()) == ["dns_causal_telemetry_v1.jsonl"]


def test_git_worktree_output_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    with pytest.raises(ValueError, match="outside_git_worktree"):
        persist_sample(root / "data" / "reports", {"classification": "INSUFFICIENT_EVIDENCE"})
    assert not (root / "data").exists()


def test_cli_once_uses_fake_probes_without_real_dns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    assert main(["--once", "--container", "exact-supervisor", "--output-dir", str(tmp_path), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["classification"] == "HOST_AND_DIRECT_DNS_OK"
    assert Path(output["jsonl_path"]).is_file()


def test_cli_help_needs_no_optional_data_dependencies() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "capture_windows_docker_dns_causal_telemetry_v1.py"
    )
    result = subprocess.run(
        [sys.executable, "-S", str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_legacy_ops_exports_keep_function_identity() -> None:
    import smartcrypto.ops as ops
    from smartcrypto.ops import paper_session

    assert ops.build_session_state is paper_session.build_session_state
    assert ops.collect_evidence is paper_session.collect_evidence


def test_cli_explicit_output_overrides_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    monkeypatch.setenv(collector.OUTPUT_DIR_ENV, "relative-output")
    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    destination = tmp_path / "explicit"
    assert main(["--once", "--output-dir", str(destination), "--json"]) == 0
    assert Path(json.loads(capsys.readouterr().out)["jsonl_path"]).parent == destination


def test_cli_uses_absolute_environment_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    destination = tmp_path / "configured"
    monkeypatch.setenv(collector.OUTPUT_DIR_ENV, str(destination))
    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    assert main(["--once", "--json"]) == 0
    assert Path(json.loads(capsys.readouterr().out)["jsonl_path"]).parent == destination


def test_cli_uses_portable_home_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    monkeypatch.delenv(collector.OUTPUT_DIR_ENV, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    assert main(["--once", "--json"]) == 0
    destination = tmp_path / "FUTUROS_LOCAL_CHECKPOINTS" / "DNS_CAUSAL_TELEMETRY_V1"
    assert Path(json.loads(capsys.readouterr().out)["jsonl_path"]).parent == destination


def test_cli_relative_environment_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    monkeypatch.setenv(collector.OUTPUT_DIR_ENV, "relative-output")
    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    assert main(["--once", "--json"]) == 2
    assert "output_dir_must_be_absolute" in capsys.readouterr().err
    assert not (tmp_path / "relative-output").exists()


def test_cli_git_worktree_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.capture_windows_docker_dns_causal_telemetry_v1 as cli

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setenv(collector.OUTPUT_DIR_ENV, str(root / "data" / "reports"))
    monkeypatch.setattr(cli, "SystemProbes", FakeProbes)
    assert main(["--once", "--json"]) == 2
    assert "outside_git_worktree" in capsys.readouterr().err
    assert not (root / "data").exists()


def test_operational_python_has_no_windows_project_root_literal() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders = [
        path.relative_to(root).as_posix()
        for base in (root / "scripts", root / "smartcrypto")
        for path in base.rglob("*.py")
        if "E:/FUTUROS" in path.read_text(encoding="utf-8")
        or "E:\\FUTUROS" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
