from __future__ import annotations

import argparse
import errno
import os
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

import pytest
import yaml

from scripts import docker_runtime_permissions_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.paper.yml"
DECISION_LEDGER_CONFIG = (
    ROOT
    / "config/decision_ledger_paper_observability.yml"
)
BOOTSTRAP_PATH = (
    ROOT
    / "scripts/docker_runtime_permissions_bootstrap.py"
)


def services() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(
        COMPOSE_PATH.read_text(
            encoding="utf-8"
        )
    )
    return payload["services"]


def command(
    service_name: str,
) -> list[str]:
    return [
        str(item)
        for item in services()[service_name]["command"]
    ]


def option_values(
    argv: list[str],
    option: str,
) -> list[str]:
    return [
        argv[index + 1]
        for index, value in enumerate(
            argv[:-1]
        )
        if value == option
    ]


def test_qlib_supervisor_uses_institutional_bootstrap_with_exact_paths() -> None:
    service = services()[
        bootstrap.QLIB_REFRESH_SERVICE
    ]
    argv = command(
        bootstrap.QLIB_REFRESH_SERVICE
    )

    assert service["user"] == "0:0"
    assert argv[:4] == [
        "python",
        "scripts/docker_runtime_permissions_bootstrap.py",
        "--service",
        bootstrap.QLIB_REFRESH_SERVICE,
    ]
    assert option_values(
        argv,
        "--path",
    ) == [
        "/app/data/runtime",
        "/app/data/reports",
        "/app/data/features",
        "/app/data/predictions",
    ]


def test_qlib_supervisor_preserves_original_application_argv() -> None:
    argv = command(
        bootstrap.QLIB_REFRESH_SERVICE
    )
    separator = argv.index("--")

    assert argv[separator + 1 :] == [
        "python",
        "scripts/run_qlib_paper_refresh_supervisor.py",
        "--interval-seconds",
        "300",
    ]


def test_qlib_profile_covers_nominal_files_without_authorizing_data_root() -> None:
    profile = bootstrap.SERVICE_PROFILES[
        bootstrap.QLIB_REFRESH_SERVICE
    ]

    assert set(profile.directories) == {
        "/app/data/runtime",
        "/app/data/reports",
        "/app/data/features",
        "/app/data/predictions",
    }
    assert set(profile.covered_files) == {
        "/app/data/runtime/active_freqtrade_signals.json",
        (
            "/app/data/reports/"
            "qlib_market_features_refresh_report.json"
        ),
        (
            "/app/data/reports/"
            "qlib_market_features_refresh_report.json.tmp"
        ),
    }
    assert (
        "/app/data"
        not in bootstrap.ALLOWED_RUNTIME_PATHS
    )


def test_bootstrap_preserves_existing_signal_payload_and_applies_minimum_mode(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    signals = (
        runtime
        / "active_freqtrade_signals.json"
    )
    original = (
        b'{"signals":[{"symbol":"BTCUSDT"}]}\n'
    )
    signals.write_bytes(original)

    modes: list[tuple[Path, int]] = []

    summary = bootstrap.ensure_runtime_path(
        runtime,
        uid=10001,
        gid=10001,
        chown=lambda *_: None,
        chmod=lambda path, mode: modes.append(
            (path, mode)
        ),
    )

    assert summary == {
        "directory_count": 1,
        "file_count": 1,
    }
    assert signals.read_bytes() == original
    assert (runtime, 0o700) in modes
    assert (signals, 0o600) in modes


def test_privilege_drop_is_setgid_then_setuid_probe_then_exec() -> None:
    source = BOOTSTRAP_PATH.read_text(
        encoding="utf-8"
    )

    assert source.index(
        "os.setgid(gid)"
    ) < source.index(
        "os.setuid(uid)"
    )
    main_source = source[
        source.index("def main(") :
    ]

    assert main_source.index(
        "drop_privileges("
    ) < main_source.index(
        "verify_runtime_writability("
    )
    assert main_source.index(
        "verify_runtime_writability("
    ) < main_source.index(
        "exec_application(args.command)"
    )
    assert "os.execvp" in source
    assert "shell=True" not in source
    assert "0o777" not in source
    assert bootstrap.DEFAULT_UID == 10001
    assert bootstrap.DEFAULT_GID == 10001


@pytest.mark.parametrize(
    "path",
    (
        "app/data/runtime",
        "/app/data/runtime/../reports",
        "/app/data/runtime-link",
        "/app/data",
    ),
)
def test_qlib_profile_rejects_relative_traversal_and_unscoped_paths(
    path: str,
) -> None:
    with pytest.raises(SystemExit):
        bootstrap.parse_args(
            [
                "--service",
                bootstrap.QLIB_REFRESH_SERVICE,
                "--path",
                path,
                "--",
                "python",
                "worker.py",
            ]
        )


def test_qlib_profile_rejects_symlinked_runtime_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    original = Path.is_symlink

    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: (
            self == runtime
            or original(self)
        ),
    )

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="symlink_path_forbidden",
    ):
        bootstrap.ensure_runtime_path(
            runtime,
            uid=10001,
            gid=10001,
            chown=lambda *_: None,
            chmod=lambda *_: None,
        )


def test_existing_service_profiles_remain_nominal_and_restricted() -> None:
    assert bootstrap.SERVICE_PROFILES[
        bootstrap.PHASE14_SERVICE
    ].directories == (
        "/app/data/reports",
        "/app/data/trades",
        "/app/data/snapshots/freqtrade-paper",
    )
    assert bootstrap.SERVICE_PROFILES[
        bootstrap.AUTOLEARNING_SERVICE
    ].directories == (
        "/app/data/reports",
        "/app/data/feedback",
    )
    assert bootstrap.SERVICE_PROFILES[
        bootstrap.NOTIFICATION_SERVICE
    ].directories == (
        "/app/data/reports",
        "/app/data/runtime",
    )


def test_qlib_service_preserves_paper_flags_with_ledger_preflight_only() -> None:
    environment = services()[
        bootstrap.QLIB_REFRESH_SERVICE
    ]["environment"]
    ledger = yaml.safe_load(
        DECISION_LEDGER_CONFIG.read_text(
            encoding="utf-8"
        )
    )

    assert (
        environment["SMARTCRYPTO_RUNTIME_MODE"]
        == "paper"
    )
    assert environment["LIVE_ENABLED"] == "false"
    assert (
        environment["ORDER_SUBMISSION_ENABLED"]
        == "false"
    )
    assert (
        environment[
            "REAL_ORDER_SUBMISSION_ENABLED"
        ]
        == "false"
    )
    assert (
        environment[
            "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS"
        ]
        == "false"
    )
    assert ledger["enabled"] is True
    assert ledger["writer_enabled"] is True
    assert ledger["trade_link_enabled"] is False
    assert (
        bootstrap.SAFE_FLAGS["sends_orders"]
        is False
    )
    assert (
        bootstrap.SAFE_FLAGS["changes_risk"]
        is False
    )


def test_qlib_profile_validation_has_no_filesystem_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile = bootstrap.SERVICE_PROFILES[
        bootstrap.QLIB_REFRESH_SERVICE
    ]

    bootstrap.validate_profile_contract(
        profile
    )

    assert list(tmp_path.iterdir()) == []
    assert os.getcwd() == str(tmp_path)


def test_post_drop_writability_probe_creates_fsyncs_and_removes_own_file(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sentinel = runtime / "sentinel.json"
    sentinel.write_text(
        '{"preserve":true}\n',
        encoding="utf-8",
    )

    fsync_descriptors: list[int] = []

    result = (
        bootstrap.verify_runtime_directory_writability(
            runtime,
            fsync=fsync_descriptors.append,
            sleep=lambda _: None,
        )
    )

    assert result == {
        "attempt_count": 1,
        "retry_count": 0,
    }
    assert len(fsync_descriptors) == 1
    assert sentinel.read_text(
        encoding="utf-8"
    ) == '{"preserve":true}\n'
    assert list(
        runtime.glob(
            (
                f"{bootstrap.WRITABILITY_PROBE_PREFIX}"
                f"*{bootstrap.WRITABILITY_PROBE_SUFFIX}"
            )
        )
    ) == []


def test_post_drop_writability_probe_retries_transient_permission_error(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    open_count = 0
    observed_delays: list[float] = []

    def flaky_open(
        directory: Path,
    ) -> BinaryIO:
        nonlocal open_count

        open_count += 1

        if open_count == 1:
            raise PermissionError(
                errno.EACCES,
                "controlled transient probe denial",
            )

        return bootstrap.open_runtime_probe(
            directory
        )

    result = (
        bootstrap.verify_runtime_directory_writability(
            runtime,
            open_probe=flaky_open,
            fsync=lambda _: None,
            sleep=observed_delays.append,
        )
    )

    assert result == {
        "attempt_count": 2,
        "retry_count": 1,
    }
    assert open_count == 2
    assert observed_delays == [
        bootstrap.WRITABILITY_PROBE_BASE_DELAY_SECONDS
    ]


def test_post_drop_writability_probe_retries_busy_error_with_backoff(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    open_count = 0
    observed_delays: list[float] = []

    def flaky_open(
        directory: Path,
    ) -> BinaryIO:
        nonlocal open_count

        open_count += 1

        if open_count <= 2:
            raise OSError(
                errno.EBUSY,
                "controlled transient busy mount",
            )

        return bootstrap.open_runtime_probe(
            directory
        )

    result = (
        bootstrap.verify_runtime_directory_writability(
            runtime,
            open_probe=flaky_open,
            fsync=lambda _: None,
            sleep=observed_delays.append,
        )
    )

    assert result == {
        "attempt_count": 3,
        "retry_count": 2,
    }
    assert observed_delays == [
        bootstrap.WRITABILITY_PROBE_BASE_DELAY_SECONDS,
        (
            bootstrap.WRITABILITY_PROBE_BASE_DELAY_SECONDS
            * 2
        ),
    ]


def test_post_drop_writability_probe_does_not_retry_permanent_error(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    open_count = 0
    observed_delays: list[float] = []

    def permanent_failure(
        _directory: Path,
    ) -> BinaryIO:
        nonlocal open_count
        open_count += 1

        raise OSError(
            errno.ENOSPC,
            "controlled permanent probe failure",
        )

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="runtime_writability_probe_failed",
    ) as exc_info:
        bootstrap.verify_runtime_directory_writability(
            runtime,
            open_probe=permanent_failure,
            fsync=lambda _: None,
            sleep=observed_delays.append,
        )

    assert isinstance(
        exc_info.value.__cause__,
        OSError,
    )
    assert (
        exc_info.value.__cause__.errno
        == errno.ENOSPC
    )
    assert open_count == 1
    assert observed_delays == []


def test_post_drop_writability_probe_exhaustion_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    open_count = 0
    observed_delays: list[float] = []

    def always_denied(
        _directory: Path,
    ) -> BinaryIO:
        nonlocal open_count
        open_count += 1

        raise PermissionError(
            errno.EACCES,
            "controlled persistent probe denial",
        )

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="runtime_writability_probe_failed",
    ):
        bootstrap.verify_runtime_directory_writability(
            runtime,
            open_probe=always_denied,
            fsync=lambda _: None,
            sleep=observed_delays.append,
        )

    assert (
        open_count
        == bootstrap.WRITABILITY_PROBE_ATTEMPTS
    )
    assert len(observed_delays) == (
        bootstrap.WRITABILITY_PROBE_ATTEMPTS - 1
    )
    assert list(runtime.iterdir()) == []


class FlushFailureProbe:
    def __init__(
        self,
        directory: Path,
    ) -> None:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=(
                bootstrap.WRITABILITY_PROBE_PREFIX
            ),
            suffix=(
                bootstrap.WRITABILITY_PROBE_SUFFIX
            ),
            dir=str(directory),
        )
        os.close(descriptor)
        self._handle = open(
            raw_path,
            "r+b",
        )
        self.name = raw_path

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def write(
        self,
        payload: bytes,
    ) -> int:
        return self._handle.write(payload)

    def flush(self) -> None:
        raise OSError(
            errno.ENOSPC,
            "controlled flush failure",
        )

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()


def test_post_drop_probe_cleans_only_own_file_after_write_path_failure(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sentinel = runtime / "unrelated.tmp"
    sentinel.write_text(
        "preserve",
        encoding="utf-8",
    )

    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="runtime_writability_probe_failed",
    ):
        bootstrap.verify_runtime_directory_writability(
            runtime,
            open_probe=FlushFailureProbe,
            fsync=lambda _: None,
            sleep=lambda _: None,
        )

    assert sentinel.read_text(
        encoding="utf-8"
    ) == "preserve"
    assert list(
        runtime.glob(
            (
                f"{bootstrap.WRITABILITY_PROBE_PREFIX}"
                f"*{bootstrap.WRITABILITY_PROBE_SUFFIX}"
            )
        )
    ) == []


def test_main_blocks_application_exec_when_writability_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = bootstrap.SERVICE_PROFILES[
        bootstrap.QLIB_REFRESH_SERVICE
    ]
    args = argparse.Namespace(
        service=bootstrap.QLIB_REFRESH_SERVICE,
        uid=10001,
        gid=10001,
        path=list(profile.directories),
        command=["python", "worker.py"],
    )

    events: list[str] = []
    exec_calls: list[list[str]] = []

    monkeypatch.setattr(
        bootstrap,
        "parse_args",
        lambda _: args,
    )
    monkeypatch.setattr(
        bootstrap,
        "prepare_runtime_permissions",
        lambda *_, **__: {
            "directory_count": 4,
            "file_count": 0,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "drop_privileges",
        lambda **_: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "verify_runtime_writability",
        lambda _: (_ for _ in ()).throw(
            bootstrap.RuntimeBootstrapError(
                "runtime_writability_probe_failed"
            )
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "emit_event",
        lambda event, **_: events.append(
            event
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "exec_application",
        lambda command: exec_calls.append(
            list(command)
        ),
    )

    result = bootstrap.main(
        [],
        lock_factory=lambda _path: NoopLock(),
    )

    assert result == 2
    assert exec_calls == []
    assert events == [
        "runtime_bootstrap_lock_acquired",
        "runtime_permissions_prepared",
        "runtime_privileges_dropped",
        "runtime_bootstrap_lock_released",
        "runtime_permissions_bootstrap_blocked",
    ]


class ExecSentinel(RuntimeError):
    pass


class NoopLock:
    def acquire(self, _timeout_seconds: float) -> None:
        return None

    def release(self) -> None:
        return None


def test_main_emits_writability_verified_before_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = bootstrap.SERVICE_PROFILES[
        bootstrap.QLIB_REFRESH_SERVICE
    ]
    args = argparse.Namespace(
        service=bootstrap.QLIB_REFRESH_SERVICE,
        uid=10001,
        gid=10001,
        path=list(profile.directories),
        command=["python", "worker.py"],
    )

    events: list[str] = []

    monkeypatch.setattr(
        bootstrap,
        "parse_args",
        lambda _: args,
    )
    monkeypatch.setattr(
        bootstrap,
        "prepare_runtime_permissions",
        lambda *_, **__: {
            "directory_count": 4,
            "file_count": 0,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "drop_privileges",
        lambda **_: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "verify_runtime_writability",
        lambda _: {
            "directory_count": 4,
            "probe_attempt_count": 4,
            "probe_retry_count": 0,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "emit_event",
        lambda event, **_: events.append(
            event
        ),
    )

    def stop_at_exec(
        _command: list[str],
    ) -> None:
        raise ExecSentinel

    monkeypatch.setattr(
        bootstrap,
        "exec_application",
        stop_at_exec,
    )

    with pytest.raises(ExecSentinel):
        bootstrap.main(
            [],
            lock_factory=lambda _path: NoopLock(),
        )

    assert events == [
        "runtime_bootstrap_lock_acquired",
        "runtime_permissions_prepared",
        "runtime_privileges_dropped",
        "runtime_writability_verified",
        "runtime_bootstrap_lock_released",
    ]
