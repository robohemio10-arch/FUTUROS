from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError, digest
from smartcrypto.research.canonical_treatment import causal_governance as g

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def snapshots() -> dict:
    config = {
        "dry_run": True,
        "trading_mode": "futures",
        "stake_amount": 50,
        "exchange": {"key": "", "secret": "", "pair_whitelist": ["BTC/USDT:USDT"]},
    }
    env = dict.fromkeys(g._FALSE_ENV, "false") | {"SMARTCRYPTO_RUNTIME_MODE": "paper"}
    return {
        role: {
            "status": "running",
            "health": "healthy",
            "issues": [],
            "safety_environment": env.copy(),
            "config": deepcopy(config),
            "image": "sha256:" + "a" * 64,
            "strategy_sha256": "b" * 64,
            "strategy_defaults": {},
            "economic_environment_sha256": "c" * 64,
            "economic_command": ["trade"],
            "db_identity": {"source": role},
            "signal_source_identity": {"source_host": "/runtime/" + role},
            "container_id": role,
            "config_sha256": "d" * 64,
            "source_sha256": {"publisher.py": "e" * 64},
        }
        for role in g.CONTAINERS
    }


def audit(at: datetime = NOW) -> dict:
    return g.audit_snapshots(snapshots(), git={"commit": "a" * 40, "tree": "b" * 40}, generated=at)


def selector_fingerprint(started: datetime) -> dict:
    return {
        "container_id": "selector-container",
        "image": "sha256:" + "a" * 64,
        "config_sha256": "b" * 64,
        "activation_file_sha256": "c" * 64,
        "freeze_file_sha256": "d" * 64,
        "frozen_artifact_sha256": {"model.txt": "e" * 64},
        "source_sha256": {"natural_producer.py": "f" * 64},
        "mounts": [{"Type": "bind", "Source": "/certified", "RW": False}],
        "environment_evidence_source": "proc_pid1",
        "started_at": started.isoformat(),
    }


@pytest.fixture
def registered_selector(tmp_path: Path, monkeypatch) -> tuple[Path, dict, dict]:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    source = snapshots()
    for item in source.values():
        item["started_at"] = (NOW - timedelta(hours=1)).isoformat()
    source["publisher"]["selector"] = selector_fingerprint(NOW - timedelta(hours=1))
    path = tmp_path / "manifest.json"
    g.activate_once(path, g.audit_snapshots(source, git={}, generated=NOW))
    # Reading the sealed bytes prevents shared fixture dictionaries from concealing tampering.
    return path, json.loads(path.read_text()), source


def test_same_selector_restart_preserves_manifest_and_raw_audit_provenance(
    registered_selector, monkeypatch
) -> None:
    path, manifest, source = registered_selector
    before = path.read_bytes()
    source["publisher"]["selector"]["started_at"] = (NOW + timedelta(seconds=1)).isoformat()
    observed = NOW + timedelta(seconds=10)
    current = g.audit_snapshots(source, git={}, generated=observed)
    before_current = deepcopy(current)
    assert current["fingerprints_sha256"] != manifest["fingerprints_sha256"]
    g.validate_manifest(manifest, current, observed)
    monkeypatch.setattr(g, "now_utc", lambda: observed)
    assert g.activate_once(path, current) == manifest
    assert path.read_bytes() == before
    assert current == before_current
    assert current["fingerprints_sha256"] == digest(current["fingerprints"])
    assert manifest["fingerprints_sha256"] == digest(manifest["fingerprints"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "another-selector"),
        ("image", "sha256:" + "1" * 64),
        ("config_sha256", "1" * 64),
        ("activation_file_sha256", "1" * 64),
        ("freeze_file_sha256", "1" * 64),
        ("frozen_artifact_sha256", {"model.txt": "1" * 64}),
        ("source_sha256", {"natural_producer.py": "1" * 64}),
        ("mounts", [{"Type": "bind", "Source": "/changed", "RW": False}]),
        ("environment_evidence_source", "docker_inspect_config_env"),
        ("unknown_new_field", "unattested-change"),
    ],
)
def test_selector_restart_rejects_any_other_provenance_change(
    registered_selector, field: str, value: object
) -> None:
    path, manifest, source = registered_selector
    before = path.read_bytes()
    source["publisher"]["selector"].update(
        started_at=(NOW + timedelta(seconds=1)).isoformat()
    )
    source["publisher"]["selector"][field] = value
    observed = NOW + timedelta(seconds=10)
    current = g.audit_snapshots(source, git={}, generated=observed)
    with pytest.raises(EvidenceError, match="causal_runtime_drift"):
        g.validate_manifest(manifest, current, observed)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "started",
    [
        None,
        "not-a-timestamp",
        (NOW + timedelta(seconds=11)).isoformat(),
        "2026-10-01T00:00:10.000000001Z",
        (NOW - timedelta(hours=2)).isoformat(),
        (NOW - timedelta(minutes=1)).isoformat(),
    ],
    ids=[
        "missing", "invalid", "future", "future-one-nanosecond", "regressed", "before-registration"
    ],
)
def test_selector_restart_rejects_unattested_start_time(
    registered_selector, started: str | None
) -> None:
    path, manifest, source = registered_selector
    before = path.read_bytes()
    selector = source["publisher"]["selector"]
    if started is None:
        selector.pop("started_at")
    else:
        selector["started_at"] = started
    observed = NOW + timedelta(seconds=10)
    current = g.audit_snapshots(source, git={}, generated=observed)
    with pytest.raises(EvidenceError, match="causal_runtime_drift|timestamp"):
        g.validate_manifest(manifest, current, observed)
    assert path.read_bytes() == before


@pytest.mark.parametrize("role", list(g.CONTAINERS))
def test_selector_restart_exception_does_not_cover_other_process_starts(
    registered_selector, role: str
) -> None:
    _, manifest, source = registered_selector
    source["publisher"]["selector"]["started_at"] = (NOW + timedelta(seconds=1)).isoformat()
    source[role]["started_at"] = (NOW + timedelta(seconds=1)).isoformat()
    observed = NOW + timedelta(seconds=10)
    with pytest.raises(EvidenceError, match="causal_runtime_drift"):
        g.validate_manifest(
            manifest, g.audit_snapshots(source, git={}, generated=observed), observed
        )


def test_resealed_manifest_cannot_replace_registered_fingerprints(registered_selector) -> None:
    _, manifest, source = registered_selector
    manifest["fingerprints"]["publisher"]["selector"]["source_sha256"] = {"changed.py": "1" * 64}
    manifest["manifest_sha256"] = digest(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    with pytest.raises(EvidenceError, match="registration_audit_identity_mismatch"):
        g.validate_manifest(manifest, g.audit_snapshots(source, git={}, generated=NOW), NOW)


def test_selector_restart_supports_real_docker_nanosecond_timestamps(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    source = snapshots()
    source["publisher"]["selector"] = selector_fingerprint(NOW - timedelta(hours=1))
    source["publisher"]["selector"]["started_at"] = "2026-09-30T23:00:00.694148427Z"
    path = tmp_path / "manifest.json"
    manifest = g.activate_once(path, g.audit_snapshots(source, git={}, generated=NOW))
    before = path.read_bytes()
    source = deepcopy(source)
    source["publisher"]["selector"]["started_at"] = "2026-10-01T00:00:01.1279858Z"
    observed = NOW + timedelta(seconds=10)
    g.validate_manifest(manifest, g.audit_snapshots(source, git={}, generated=observed), observed)
    assert path.read_bytes() == before


def test_only_exact_noneconomic_config_differences_allowed() -> None:
    source = snapshots()
    source["treatment"]["config"]["bot_name"] = "Paper-B"
    assert g.audit_snapshots(source, git={}, generated=NOW)["parity_status"] == "PASS"
    source["treatment"]["config"]["stake_amount"] = 51
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert "economic_config_difference:stake_amount" in report["issues"]
    assert "config" not in report["fingerprints"]["control"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "different"),
        ("strategy_sha256", "c" * 64),
        ("economic_environment_sha256", "different"),
        ("economic_command", ["trade", "--fee", "0"]),
    ],
)
def test_runtime_economic_drift_blocks(field: str, value: object) -> None:
    source = snapshots()
    source["treatment"][field] = value
    assert g.audit_snapshots(source, git={}, generated=NOW)["parity_status"] == "BLOCKED"


@pytest.mark.parametrize("mutation", ["unhealthy", "live", "credentials", "layered"])
def test_unattested_runtime_blocks(mutation: str) -> None:
    source = snapshots()
    if mutation == "unhealthy":
        source["publisher"]["health"] = "unhealthy"
    elif mutation == "live":
        source["control"]["safety_environment"]["LIVE_ENABLED"] = "true"
    elif mutation == "credentials":
        source["control"]["config"]["exchange"]["secret"] = "sensitive-value"
    else:
        source["control"]["config"]["add_config_files"] = ["extra.json"]
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["parity_status"] == "BLOCKED"
    assert "sensitive-value" not in str(report)


def test_activation_is_current_atomic_idempotent_and_never_overwritten(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    path = tmp_path / "manifest.json"
    manifest = g.activate_once(path, audit())
    before = path.read_bytes()
    assert manifest["formal_activation_utc"] == NOW.isoformat()
    assert manifest["pre_activation_rows_classification"] == "smoke_pre_registration"
    assert manifest["exact_join_required"] is True
    assert manifest["prospective_only"] is True
    assert manifest["backfill_allowed"] is False
    monkeypatch.setattr(g, "now_utc", lambda: NOW + timedelta(seconds=1))
    assert g.activate_once(path, audit()) == manifest
    assert path.read_bytes() == before
    bad = audit()
    bad["fingerprints"]["control"]["image"] = "changed"
    bad["fingerprints_sha256"] = digest(bad["fingerprints"])
    bad["audit_sha256"] = digest({k: v for k, v in bad.items() if k != "audit_sha256"})
    with pytest.raises(EvidenceError, match="causal_runtime_drift"):
        g.activate_once(path, bad)
    assert path.read_bytes() == before


def test_busy_lock_and_failed_parity_cannot_activate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    path = tmp_path / "manifest.json"
    path.with_suffix(".guard").touch()
    with pytest.raises(EvidenceError, match="store_busy"):
        g.activate_once(path, audit())
    path.with_suffix(".guard").unlink()
    bad = audit()
    bad["parity_status"] = "BLOCKED"
    bad["audit_sha256"] = digest({k: v for k, v in bad.items() if k != "audit_sha256"})
    with pytest.raises(EvidenceError, match="runtime_parity_not_pass"):
        g.activate_once(path, bad)
    assert not path.exists()


def test_incomplete_publication_does_not_replace_destination(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    path = tmp_path / "manifest.json"

    def fail_link(source, destination):
        raise OSError("simulated_link_failure")

    monkeypatch.setattr(g.os, "link", fail_link)
    with pytest.raises(OSError, match="simulated"):
        g.activate_once(path, audit())
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("age", [-1, 301])
def test_stale_or_future_audit_rejected(age: int) -> None:
    with pytest.raises(EvidenceError, match="stale_or_future"):
        g.validate_audit(audit(NOW - timedelta(seconds=age)), NOW)


def test_invalid_manifest_cannot_be_repaired_in_place(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"wrong"}')
    before = path.read_bytes()
    with pytest.raises(EvidenceError, match="manifest_hash_or_schema"):
        g.activate_once(path, audit())
    assert path.read_bytes() == before


def test_runtime_directories_cannot_be_output() -> None:
    with pytest.raises(EvidenceError, match="outside_protected_runtime"):
        g.run_governance(Path("/tmp/FUTUROS"), Path("/tmp/FUTUROS"))


def test_sqlite_reader_is_readonly_and_reads_real_trade_columns(tmp_path: Path) -> None:
    path = tmp_path / "trades.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE trades (id INTEGER, is_open INTEGER, open_date TEXT, close_date TEXT, enter_tag TEXT, pair TEXT, is_short INTEGER, close_profit_abs REAL, stake_amount REAL, leverage REAL)"
        )
        connection.execute(
            "INSERT INTO trades VALUES (1,0,'2026-10-01T00:00:01Z','2026-10-01T01:00:00Z','decision_event_id=exact','BTC/USDT:USDT',0,-1.25,50,2)"
        )
    before = path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            g._READ_PROGRAM,
            json.dumps({"mode": "sqlite", "path": str(path)}),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    row = json.loads(result.stdout)[0]
    assert row["close_profit_abs"] == -1.25
    assert row["enter_tag"] == "decision_event_id=exact"
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_registration_audit_prevents_backdating_or_retiming(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    manifest = g.activate_once(tmp_path / "manifest.json", audit())
    manifest["formal_activation_utc"] = (NOW - timedelta(seconds=1)).isoformat()
    manifest["manifest_sha256"] = digest(
        {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    )
    with pytest.raises(EvidenceError, match="stale_or_future"):
        g.validate_manifest(manifest, audit(), NOW)


def test_instrumentation_and_unrelated_strategy_files_are_not_economic_equality() -> None:
    source = snapshots()
    for role in ("publisher", "monitor"):
        source[role]["image"] = "sha256:" + role
        source[role]["economic_environment_sha256"] = role
        source[role]["service_environment_sha256"] = role
    source["treatment"]["strategy_hashes"] = {"unrelated.py": "different"}
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["parity_status"] == "PASS"
    assert report["economic_parity"] == report["safety_parity"] == "PASS"
    assert report["instrumentation_differences"]["publisher"]["compared_to_freqtrade"] is False
    source["publisher"]["health"] = "unhealthy"
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["economic_parity"] == "PASS"
    assert report["runtime_identity"]["status"] == "BLOCKED"
    assert "publisher:not_running_healthy" in report["blocking_differences"]


@pytest.mark.parametrize("enabled", [False, True])
def test_api_server_exemption_requires_disabled(enabled: bool) -> None:
    source = snapshots()
    source["control"]["config"]["api_server"] = {"enabled": enabled, "listen_port": 8080}
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["parity_status"] == ("BLOCKED" if enabled else "PASS")
    if not enabled:
        assert "api_server.enabled" in report["allowed_differences"]


def test_environment_projection_excludes_service_vars_preserves_overrides_and_safety() -> None:
    env = {"SMARTCRYPTO_RUNTIME_MODE": "paper", **dict.fromkeys(g._FALSE_ENV, "false")}
    assert g.economic_environment(env | {"PYTHONPATH": "/app", "HOST_ROOT": "x"}) == env
    assert g.economic_environment(env | {"FREQTRADE__STAKE_AMOUNT": "99"}) != env
    assert g.economic_environment(env | {"LIVE_ENABLED": "true"}) != env


def test_normalized_universe_and_effective_strategy_defaults() -> None:
    source = snapshots()
    for role in ("control", "treatment"):
        source[role]["config"]["exchange"]["pair_whitelist"] = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        source[role]["strategy_defaults"] = {"stoploss": -0.1, "minimal_roi": {"0": 0.02}}
    source["treatment"]["config"]["exchange"]["pair_whitelist"].reverse()
    source["treatment"]["config"]["stoploss"] = -0.1
    assert g.audit_snapshots(source, git={}, generated=NOW)["parity_status"] == "PASS"
    source["treatment"]["config"]["stoploss"] = -0.2
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert "economic_config_difference:stoploss" in report["blocking_differences"]
    source["treatment"]["config"]["exchange"]["pair_whitelist"] = ["BTC/USDT:USDT"]
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert "economic_config_difference:exchange.pair_whitelist" in report["blocking_differences"]


def test_strategy_contract_is_static_and_rejects_dynamic_paths() -> None:
    raw = b"""raise RuntimeError("must_not_execute")
class SmartCryptoSignalStrategy:
    stoploss = -0.1
    minimal_roi: dict = {"0": 0.02}
    _signal_paths = [Path("/freqtrade/user_data/data/runtime/active_freqtrade_signals.json")]
"""
    defaults, paths = g.strategy_contract(raw)
    assert defaults == {"stoploss": -0.1, "minimal_roi": {"0": 0.02}}
    assert paths == ["/freqtrade/user_data/data/runtime/active_freqtrade_signals.json"]
    with pytest.raises(EvidenceError, match="strategy_signal_paths_not_literal"):
        g.strategy_contract(
            raw.replace(
                b'[Path("/freqtrade/user_data/data/runtime/active_freqtrade_signals.json")]',
                b"get_paths()",
            )
        )


def test_compose_sources_resolve_labels_and_overrides_not_a_fixed_filename(tmp_path: Path) -> None:
    (tmp_path / "actual.yml").write_text("services: {}\n")
    (tmp_path / "override.yml").write_text("name: example\n")
    info = {
        "Config": {
            "Labels": {
                "com.docker.compose.project.config_files": "actual.yml,override.yml",
                "com.docker.compose.project.working_dir": str(tmp_path),
            }
        }
    }
    hashes, root, issues = g.compose_sources(info)
    assert root == tmp_path
    assert len(hashes) == 2 and issues == []
    (tmp_path / "override.yml").unlink()
    _, _, issues = g.compose_sources(info)
    assert issues == ["compose_label_source_missing:" + str(tmp_path / "override.yml")]


def test_signal_source_isolation_compares_host_source_despite_mount_mode() -> None:
    source = snapshots()
    source["control"]["signal_source_identity"] = {"source_host": "/same", "mount_readonly": True}
    source["treatment"]["signal_source_identity"] = {
        "source_host": "/same",
        "mount_readonly": False,
    }
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert "control_treatment_signal_source_not_isolated" in report["blocking_differences"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("economic_parity", "BLOCKED"),
        ("safety_parity", "BLOCKED"),
        ("strategy_hash_parity", "BLOCKED"),
        ("image_parity", "BLOCKED"),
        ("blocking_differences", ["blocked"]),
        ("runtime_identity", {"status": "BLOCKED"}),
        ("policy_version", "old"),
    ],
)
def test_each_hard_gate_prevents_activation(tmp_path: Path, monkeypatch, key, value) -> None:
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    report = audit()
    report[key] = value
    report["audit_sha256"] = digest({k: v for k, v in report.items() if k != "audit_sha256"})
    with pytest.raises(EvidenceError, match="runtime_parity_hard_gate_invalid"):
        g.activate_once(tmp_path / "manifest.json", report)
    assert not (tmp_path / "manifest.json").exists()


def test_failed_runtime_read_emits_separate_fail_closed_sections(
    tmp_path: Path, monkeypatch
) -> None:
    def unavailable(root):
        raise EvidenceError("required_mount_missing")

    monkeypatch.setattr(g, "collect_runtime", unavailable)
    report = g.run_governance(tmp_path / "checkout", tmp_path / "runtime")
    assert report["blocking_differences"] == ["required_mount_missing"]
    assert report["economic_parity"] == report["safety_parity"] == "BLOCKED"
    assert report["runtime_identity"]["status"] == "BLOCKED"
    assert not (tmp_path / "checkout" / g.MANIFEST_PATH).exists()


@pytest.mark.parametrize("unreadable", [False, True])
def test_optional_reader_distinguishes_absent_from_unreadable(
    tmp_path: Path, unreadable: bool
) -> None:
    program = g._READ_PROGRAM
    if unreadable:
        program = (
            "import pathlib\ndef deny_stat(*args, **kwargs):\n    raise PermissionError('denied')\n"
            "pathlib.Path.stat = deny_stat\n" + program
        )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            program,
            json.dumps({"mode": "optional_file", "path": str(tmp_path / "absent.json")}),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if unreadable:
        assert result.returncode != 0
        assert "PermissionError" in result.stderr
        assert result.stdout == ""
    else:
        assert result.returncode == 0
        assert json.loads(result.stdout) is None


def inspect_fixture(root: Path) -> dict:
    return {
        "Id": "control",
        "Image": "sha256:" + "a" * 64,
        "Name": "/control",
        "Config": {
            "Image": "image:stable",
            "User": "ftuser",
            "Cmd": ["trade"],
            "Env": ["PASSWORD=sensitive-value"],
            "Labels": {
                "com.docker.compose.project": "control",
                "com.docker.compose.service": "paper",
                "com.docker.compose.project.working_dir": str(root),
                "com.docker.compose.project.config_files": "absent.yml",
                "com.docker.compose.config-hash": "a" * 64,
            },
        },
        "HostConfig": {"NetworkMode": "paper", "LogConfig": {"private": "sensitive-value"}},
        "Mounts": [{"Type": "bind", "Source": str(root), "Destination": "/runtime", "RW": False}],
    }


def test_missing_compose_recovered_from_exact_git_blob_without_restoring(
    tmp_path: Path, monkeypatch
) -> None:
    info = inspect_fixture(tmp_path)
    raw = b"services:\r\n  paper: {}\r\n"
    monkeypatch.setattr(g, "_git", lambda root: {"commit": "a" * 40})
    calls = []

    def command(args):
        calls.append(args)
        assert args[-1] == "a" * 40 + ":absent.yml"
        assert args[-2] in {"show", "rev-parse"}
        return raw if args[-2] == "show" else ("b" * 40).encode()

    monkeypatch.setattr(g, "command", command)
    provenance, root = g.compose_provenance(info)
    assert root == tmp_path and len(calls) == 2
    assert provenance["compose_source_recovered"] is True
    assert (
        provenance["files_sha256"][str(tmp_path / "absent.yml")] == hashlib.sha256(raw).hexdigest()
    )
    assert provenance["git_recovery"][str(tmp_path / "absent.yml")]["git_blob"] == "b" * 40
    assert not (tmp_path / "absent.yml").exists()


def test_unrecoverable_compose_is_warning_only_with_complete_live_spec(
    tmp_path: Path, monkeypatch
) -> None:
    def unavailable(root):
        raise EvidenceError("git_unavailable")

    monkeypatch.setattr(g, "_git", unavailable)
    info = inspect_fixture(tmp_path)
    provenance, _ = g.compose_provenance(info)
    assert provenance["compose_source_recovered"] is False
    source = snapshots()
    source["control"]["compose_provenance"] = provenance
    blocked = g.audit_snapshots(source, git={}, generated=NOW)
    assert "control:compose_gap_without_complete_live_spec" in blocked["blocking_differences"]
    spec = g.live_container_spec_snapshot(info)
    source["control"].update(
        live_container_spec_snapshot=spec, live_container_spec_sha256=digest(spec)
    )
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["parity_status"] == "PASS"
    assert report["provenance_warnings"]
    assert "sensitive-value" not in json.dumps(report)
    monkeypatch.setattr(g, "now_utc", lambda: NOW)
    manifest = g.activate_once(tmp_path / "manifest.json", report)
    assert manifest["provenance_warnings"] == report["provenance_warnings"]
    assert (
        manifest["fingerprints"]["control"]["compose_provenance"]["compose_source_recovered"]
        is False
    )
    source["treatment"]["config"]["stake_amount"] = 999
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert "economic_config_difference:stake_amount" in report["blocking_differences"]


@pytest.mark.parametrize("field", ["Id", "Image", "Mounts", "HostConfig"])
def test_incomplete_live_spec_never_substitutes_compose(tmp_path: Path, field: str) -> None:
    info = inspect_fixture(tmp_path)
    info.pop(field)
    with pytest.raises(EvidenceError, match="live_container_spec_incomplete"):
        g.live_container_spec_snapshot(info)


@pytest.mark.parametrize(
    "mutation", ["same_digest", "different_digest", "tag_only", "different_platform", "stale_id"]
)
def test_active_image_equivalence_requires_immutable_digest_and_platform(mutation: str) -> None:
    source = snapshots()
    source["treatment"]["image"] = "sha256:" + "b" * 64
    for role in ("control", "treatment"):
        source[role]["image_details"] = {
            "id": source[role]["image"],
            "platform": "linux/amd64/",
            "repo_digests": ["freqtradeorg/freqtrade@sha256:" + "c" * 64],
        }
    item = source["treatment"]["image_details"]
    if mutation == "different_digest":
        item["repo_digests"] = ["freqtradeorg/freqtrade@sha256:" + "d" * 64]
    elif mutation == "tag_only":
        for role in ("control", "treatment"):
            source[role]["image_details"]["repo_digests"] = ["freqtradeorg/freqtrade:stable"]
    elif mutation == "different_platform":
        item["platform"] = "linux/arm64/"
    elif mutation == "stale_id":
        item["id"] = source["control"]["image"]
    report = g.audit_snapshots(source, git={}, generated=NOW)
    assert report["image_parity"] == ("PASS" if mutation == "same_digest" else "BLOCKED")
    assert report["parity_status"] == report["image_parity"]
