from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import docker_runtime_permissions_bootstrap as bootstrap

BOOTSTRAP = Path(bootstrap.__file__).resolve()


def command_with_path(path: str) -> list[str]:
    return ["--path", path, "--", "python", "worker.py"]


def test_allowlist_is_exact() -> None:
    assert bootstrap.ALLOWED_RUNTIME_PATHS == {
        "/app/data/reports",
        "/app/data/runtime",
        "/app/data/trades",
        "/app/data/feedback",
        "/app/data/research",
        "/app/data/models",
        "/app/data/registries",
        "/app/data/features",
        "/app/data/predictions",
        "/app/data/snapshots/freqtrade-paper",
    }


@pytest.mark.parametrize("path", sorted(bootstrap.ALLOWED_RUNTIME_PATHS))
def test_authorized_paths_are_accepted(path: str) -> None:
    assert bootstrap.parse_args(command_with_path(path)).path == [path]


@pytest.mark.parametrize(
    "path",
    (
        "/app/data",
        "/app/data/reports/../runtime",
        "app/data/reports",
        r"\app\data\reports",
        r"/app/data\reports",
        "/app/data//reports",
        "/app/data/reports-unscoped",
    ),
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(SystemExit):
        bootstrap.parse_args(command_with_path(path))


@pytest.mark.parametrize("option", ("--uid", "--gid"))
def test_root_uid_and_gid_are_rejected(option: str) -> None:
    with pytest.raises(SystemExit):
        bootstrap.parse_args([option, "0", *command_with_path("/app/data/reports")])


def test_target_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "reports"
    target.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == target or original(self))

    with pytest.raises(bootstrap.RuntimeBootstrapError, match="symlink_path_forbidden"):
        bootstrap.ensure_runtime_path(
            target,
            uid=10001,
            gid=10001,
            chown=lambda *_: None,
            chmod=lambda *_: None,
        )


def test_intermediate_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    intermediate = tmp_path / "data"
    target = intermediate / "reports"
    intermediate.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == intermediate or original(self),
    )

    with pytest.raises(bootstrap.RuntimeBootstrapError, match="symlink_path_forbidden"):
        bootstrap.ensure_runtime_path(
            target,
            uid=10001,
            gid=10001,
            chown=lambda *_: None,
            chmod=lambda *_: None,
        )


def test_regular_file_as_target_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "reports"
    target.write_text("not-a-directory", encoding="utf-8")

    with pytest.raises(bootstrap.RuntimeBootstrapError):
        bootstrap.ensure_runtime_path(
            target,
            uid=10001,
            gid=10001,
            chown=lambda *_: None,
            chmod=lambda *_: None,
        )


def test_directories_and_existing_files_receive_minimum_permissions(tmp_path: Path) -> None:
    target = tmp_path / "reports"
    nested = target / "nested"
    target.mkdir()
    nested.mkdir()
    report = target / "report.json"
    nested_report = nested / "nested.json"
    report.write_text("{}", encoding="utf-8")
    nested_report.write_text("{}", encoding="utf-8")
    ownership: list[tuple[Path, int, int]] = []
    modes: list[tuple[Path, int]] = []

    summary = bootstrap.ensure_runtime_path(
        target,
        uid=10001,
        gid=10001,
        chown=lambda path, uid, gid: ownership.append((path, uid, gid)),
        chmod=lambda path, mode: modes.append((path, mode)),
    )

    assert summary == {"directory_count": 2, "file_count": 2}
    assert set(ownership) == {
        (target, 10001, 10001),
        (nested, 10001, 10001),
        (report, 10001, 10001),
        (nested_report, 10001, 10001),
    }
    assert set(modes) == {
        (target, 0o700),
        (nested, 0o700),
        (report, 0o600),
        (nested_report, 0o600),
    }


@pytest.mark.parametrize("operation", ("chown", "chmod"))
def test_permission_failures_are_fail_closed(tmp_path: Path, operation: str) -> None:
    target = tmp_path / "reports"

    def fail(*_args: object) -> None:
        raise PermissionError("synthetic-permission-error")

    chown = fail if operation == "chown" else lambda *_: None
    chmod = fail if operation == "chmod" else lambda *_: None
    with pytest.raises(bootstrap.RuntimeBootstrapError, match="runtime_path_permission_failed"):
        bootstrap.ensure_runtime_path(
            target,
            uid=10001,
            gid=10001,
            chown=chown,
            chmod=chmod,
        )


def test_setgid_precedes_setuid_and_final_identity_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {"uid": 0, "gid": 0}
    calls: list[object] = []
    monkeypatch.setattr(os, "geteuid", lambda: identity["uid"], raising=False)
    monkeypatch.setattr(os, "getegid", lambda: identity["gid"], raising=False)
    monkeypatch.setattr(os, "setgroups", lambda groups: calls.append(("setgroups", groups)), raising=False)

    def setgid(gid: int) -> None:
        calls.append(("setgid", gid))
        identity["gid"] = gid

    def setuid(uid: int) -> None:
        calls.append(("setuid", uid))
        identity["uid"] = uid

    monkeypatch.setattr(os, "setgid", setgid, raising=False)
    monkeypatch.setattr(os, "setuid", setuid, raising=False)
    monkeypatch.setattr(os, "umask", lambda mask: calls.append(("umask", mask)))
    monkeypatch.setattr(os, "environ", {})

    bootstrap.drop_privileges(uid=10001, gid=10001)

    assert calls == [
        ("setgroups", []),
        ("setgid", 10001),
        ("setuid", 10001),
        ("umask", 0o077),
    ]
    assert identity == {"uid": 10001, "gid": 10001}


def test_invalid_final_identity_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "getegid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "setgroups", lambda _groups: None, raising=False)
    monkeypatch.setattr(os, "setgid", lambda _gid: None, raising=False)
    monkeypatch.setattr(os, "setuid", lambda _uid: None, raising=False)
    monkeypatch.setattr(os, "umask", lambda _mask: None)

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="privilege_drop_verification_failed",
    ):
        bootstrap.drop_privileges(uid=10001, gid=10001)


def test_execvp_receives_exact_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[object] = []

    def execvp(executable: str, argv: list[str]) -> None:
        received.extend((executable, argv))
        raise OSError("synthetic-exec-stop")

    monkeypatch.setattr(os, "execvp", execvp)
    command = ["python", "worker.py", "--once"]

    with pytest.raises(bootstrap.RuntimeBootstrapError, match="application_exec_failed"):
        bootstrap.exec_application(command)
    assert received == ["python", command]


def test_bootstrap_is_shell_free_and_does_not_authorize_data_root() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "shell=True" not in source
    assert "os.execvp" in source
    assert "0o777" not in source
    assert '"/app/data"' not in bootstrap.ALLOWED_RUNTIME_PATHS


def test_safety_flags_remain_paper_shadow_only() -> None:
    assert bootstrap.SAFE_FLAGS == {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def test_qlib_profile_has_exact_directories_and_nominal_file_coverage() -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]

    assert profile.directories == (
        "/app/data/runtime",
        "/app/data/reports",
        "/app/data/features",
        "/app/data/predictions",
    )
    assert profile.covered_files == (
        "/app/data/runtime/active_freqtrade_signals.json",
        "/app/data/reports/qlib_market_features_refresh_report.json",
        "/app/data/reports/qlib_market_features_refresh_report.json.tmp",
    )
    bootstrap.validate_profile_contract(profile)


def test_profile_file_outside_authorized_directories_is_blocked() -> None:
    profile = bootstrap.RuntimePermissionProfile(
        service="synthetic",
        directories=("/app/data/reports",),
        covered_files=("/app/data/runtime/active_freqtrade_signals.json",),
    )

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="profile_file_outside_authorized_directory",
    ):
        bootstrap.validate_profile_contract(profile)


@pytest.mark.parametrize("service", [bootstrap.QLIB_REFRESH_SERVICE, bootstrap.NOTIFICATION_SERVICE])
def test_shared_runtime_only_changes_owned_files_and_preserves_foreign_namespace(
    tmp_path: Path, service: str,
) -> None:
    profile = bootstrap.SERVICE_PROFILES[service]
    bootstrap.validate_profile_contract(profile)
    policy = next(p for p in profile.path_policies if p.path == "/app/data/runtime")
    assert policy.recursive is False and policy.directory_mode == 0o755
    runtime = tmp_path / "runtime"
    treatment = runtime / "canonical_treatment"
    nested = treatment / "nested"
    nested.mkdir(parents=True)
    treatment.chmod(0o755)
    payload = nested / "signal.json"
    payload.write_bytes(b'{"signals":[]}\n')
    before = (treatment.stat(), nested.stat(), payload.stat(), payload.read_bytes())
    own_files = tuple(
        runtime / Path(value).name for value in profile.covered_files
        if value.startswith("/app/data/runtime/")
    )
    for target in own_files:
        target.write_bytes(b"owned-test-content")
    ownership, modes = [], []
    for _ in range(2):
        summary = bootstrap.ensure_runtime_path(
            runtime, uid=10001, gid=10001, recursive=policy.recursive,
            covered_files=own_files, directory_mode=policy.directory_mode,
            file_mode=policy.file_mode,
            chown=lambda *args: ownership.append(args),
            chmod=lambda *args: modes.append(args),
        )
        assert summary == {"directory_count": 1, "file_count": len(own_files)}
        assert (treatment.stat(), nested.stat(), payload.stat(), payload.read_bytes()) == before
    assert {entry[0] for entry in ownership} == {runtime, *own_files}
    assert set(modes) == {(runtime, 0o755), *((path, policy.file_mode) for path in own_files)}
    assert all(path.read_bytes() == b"owned-test-content" for path in own_files)
    if service == bootstrap.QLIB_REFRESH_SERVICE:
        assert own_files == (runtime / "active_freqtrade_signals.json",)
        assert policy.file_mode == 0o644
    assert bootstrap.verify_runtime_directory_writability(runtime)["attempt_count"] == 1


@pytest.mark.parametrize("policy_path", ["relative", "/app/data/runtime/../reports", "/app/data/runtime/child"])
def test_path_policy_cannot_escape_or_expand_profile(policy_path: str) -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]
    invalid = replace(profile, path_policies=(bootstrap.RuntimePathPolicy(policy_path, recursive=False),))
    with pytest.raises(bootstrap.RuntimeBootstrapError, match="path_policy_outside_profile"):
        bootstrap.validate_profile_contract(invalid)


def test_shared_root_cannot_claim_a_foreign_nested_file() -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]
    invalid = replace(profile, covered_files=("/app/data/runtime/canonical_treatment/signal.json",))
    with pytest.raises(bootstrap.RuntimeBootstrapError, match="shared_root_file_must_be_direct_child"):
        bootstrap.validate_profile_contract(invalid)


@pytest.mark.parametrize("mode", [0o707, 0o775, 0o4775])
def test_shared_policy_rejects_unsafe_modes(mode: int) -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]
    invalid = replace(profile, path_policies=(bootstrap.RuntimePathPolicy("/app/data/runtime", False, mode),))
    with pytest.raises(bootstrap.RuntimeBootstrapError, match="unsafe_profile_path_policy"):
        bootstrap.validate_profile_contract(invalid)


def test_shared_owned_symlink_is_rejected_before_chown(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = runtime / "active_freqtrade_signals.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    changes = []
    with pytest.raises(bootstrap.RuntimeBootstrapError, match="symlink_path_forbidden"):
        bootstrap.ensure_runtime_path(
            runtime, uid=10001, gid=10001, recursive=False, covered_files=(target,),
            chown=lambda *args: changes.append(args), chmod=lambda *args: changes.append(args),
        )
    assert not changes


def test_prepare_dispatches_profile_policy_and_preserves_private_profiles(monkeypatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    calls = []
    def ensure(path, **kwargs):
        calls.append((path, kwargs))
        return {"directory_count": 1, "file_count": 0}
    monkeypatch.setattr(bootstrap, "ensure_runtime_path", ensure)
    for profile in bootstrap.SERVICE_PROFILES.values():
        calls.clear()
        bootstrap.prepare_runtime_permissions(profile, uid=10001, gid=10001)
        for path, options in calls:
            if path == Path("/app/data/runtime"):
                assert options["recursive"] is False
                assert options["directory_mode"] == 0o755
                assert options["covered_files"]
            else:
                assert options["recursive"] is True
                assert options["directory_mode"] == 0o700
                assert options["file_mode"] == 0o600
