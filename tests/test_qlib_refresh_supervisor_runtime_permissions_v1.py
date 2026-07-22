from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import docker_runtime_permissions_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.paper.yml"
DECISION_LEDGER_CONFIG = ROOT / "config/decision_ledger_paper_observability.yml"
BOOTSTRAP_PATH = ROOT / "scripts/docker_runtime_permissions_bootstrap.py"


def services() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return payload["services"]


def command(service_name: str) -> list[str]:
    return [str(item) for item in services()[service_name]["command"]]


def option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == option]


def test_qlib_supervisor_uses_institutional_bootstrap_with_exact_paths() -> None:
    service = services()[bootstrap.QLIB_REFRESH_SERVICE]
    argv = command(bootstrap.QLIB_REFRESH_SERVICE)

    assert service["user"] == "0:0"
    assert argv[:4] == [
        "python",
        "scripts/docker_runtime_permissions_bootstrap.py",
        "--service",
        bootstrap.QLIB_REFRESH_SERVICE,
    ]
    assert option_values(argv, "--path") == [
        "/app/data/runtime",
        "/app/data/reports",
        "/app/data/features",
        "/app/data/predictions",
    ]


def test_qlib_supervisor_preserves_original_application_argv() -> None:
    argv = command(bootstrap.QLIB_REFRESH_SERVICE)
    separator = argv.index("--")

    assert argv[separator + 1 :] == [
        "python",
        "scripts/run_qlib_paper_refresh_supervisor.py",
        "--interval-seconds",
        "300",
    ]


def test_qlib_profile_covers_nominal_files_without_authorizing_data_root() -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]

    assert set(profile.directories) == {
        "/app/data/runtime",
        "/app/data/reports",
        "/app/data/features",
        "/app/data/predictions",
    }
    assert set(profile.covered_files) == {
        "/app/data/runtime/active_freqtrade_signals.json",
        "/app/data/reports/qlib_market_features_refresh_report.json",
        "/app/data/reports/qlib_market_features_refresh_report.json.tmp",
    }
    assert "/app/data" not in bootstrap.ALLOWED_RUNTIME_PATHS


def test_bootstrap_preserves_existing_signal_payload_and_applies_minimum_mode(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    signals = runtime / "active_freqtrade_signals.json"
    original = b'{"signals":[{"symbol":"BTCUSDT"}]}\n'
    signals.write_bytes(original)
    modes: list[tuple[Path, int]] = []

    summary = bootstrap.ensure_runtime_path(
        runtime,
        uid=10001,
        gid=10001,
        chown=lambda *_: None,
        chmod=lambda path, mode: modes.append((path, mode)),
    )

    assert summary == {"directory_count": 1, "file_count": 1}
    assert signals.read_bytes() == original
    assert (runtime, 0o700) in modes
    assert (signals, 0o600) in modes


def test_privilege_drop_is_setgid_then_setuid_and_exec_is_shell_free() -> None:
    source = BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert source.index("os.setgid(gid)") < source.index("os.setuid(uid)")
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
def test_qlib_profile_rejects_relative_traversal_and_unscoped_paths(path: str) -> None:
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
        lambda self: self == runtime or original(self),
    )

    with pytest.raises(bootstrap.RuntimeBootstrapError, match="symlink_path_forbidden"):
        bootstrap.ensure_runtime_path(
            runtime,
            uid=10001,
            gid=10001,
            chown=lambda *_: None,
            chmod=lambda *_: None,
        )


def test_existing_service_profiles_remain_nominal_and_restricted() -> None:
    assert bootstrap.SERVICE_PROFILES[bootstrap.PHASE14_SERVICE].directories == (
        "/app/data/reports",
        "/app/data/trades",
        "/app/data/snapshots/freqtrade-paper",
    )
    assert bootstrap.SERVICE_PROFILES[bootstrap.AUTOLEARNING_SERVICE].directories == (
        "/app/data/reports",
        "/app/data/feedback",
    )
    assert bootstrap.SERVICE_PROFILES[bootstrap.NOTIFICATION_SERVICE].directories == (
        "/app/data/reports",
        "/app/data/runtime",
    )


def test_qlib_service_preserves_paper_only_flags_and_decision_ledger_disabled() -> None:
    environment = services()[bootstrap.QLIB_REFRESH_SERVICE]["environment"]
    ledger = yaml.safe_load(DECISION_LEDGER_CONFIG.read_text(encoding="utf-8"))

    assert environment["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
    assert environment["LIVE_ENABLED"] == "false"
    assert environment["ORDER_SUBMISSION_ENABLED"] == "false"
    assert environment["REAL_ORDER_SUBMISSION_ENABLED"] == "false"
    assert environment["SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS"] == "false"
    assert ledger["enabled"] is False
    assert ledger["writer_enabled"] is False
    assert ledger["trade_link_enabled"] is False
    assert bootstrap.SAFE_FLAGS["sends_orders"] is False
    assert bootstrap.SAFE_FLAGS["changes_risk"] is False


def test_qlib_profile_validation_has_no_filesystem_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]

    bootstrap.validate_profile_contract(profile)

    assert list(tmp_path.iterdir()) == []
    assert os.getcwd() == str(tmp_path)
