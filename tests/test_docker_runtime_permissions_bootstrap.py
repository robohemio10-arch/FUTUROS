from __future__ import annotations

import os
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
