from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import docker_runtime_permissions_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.paper.yml"
LEDGER_CONFIG = ROOT / "config/decision_ledger_paper_observability.yml"


def services() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def compose_payload() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_freqtrade_internal_data_is_isolated_and_signals_are_read_only() -> None:
    payload = compose_payload()
    service = payload["services"]["freqtrade-paper"]
    volumes = service["volumes"]

    assert "./data:/freqtrade/user_data/data" not in volumes
    assert "freqtrade_paper_data:/freqtrade/user_data/data" in volumes
    assert "./data/runtime:/freqtrade/user_data/data/runtime:ro" in volumes
    assert payload["volumes"]["freqtrade_paper_data"]["name"] == (
        "futuros_freqtrade_paper_data"
    )
    assert payload["volumes"]["freqtrade_paper_db"]["name"] == (
        "futuros_freqtrade_paper_db"
    )


def test_freqtrade_has_specific_healthcheck_and_preserves_paper_flags() -> None:
    service = services()["freqtrade-paper"]
    command = [str(item) for item in service["healthcheck"]["test"]]

    assert command[:3] == [
        "CMD",
        "python",
        "/freqtrade/user_data/freqtrade_paper_healthcheck.py",
    ]
    assert "--quiet" in command
    assert "--min-uptime-seconds" in command
    assert (
        "./scripts/freqtrade_paper_healthcheck.py:"
        "/freqtrade/user_data/freqtrade_paper_healthcheck.py:ro"
    ) in service["volumes"]
    environment = service["environment"]
    assert environment["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
    assert environment["LIVE_ENABLED"] == "false"
    assert environment["ORDER_SUBMISSION_ENABLED"] == "false"
    assert environment["REAL_ORDER_SUBMISSION_ENABLED"] == "false"
    assert environment["SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS"] == "false"


def test_core_service_ordering_is_health_gated() -> None:
    payload = services()

    assert payload["qlib-refresh-supervisor-paper"]["depends_on"] == {
        "freqtrade-paper": {"condition": "service_healthy"}
    }
    assert payload["phase14-feedback-sync-paper"]["depends_on"] == {
        "freqtrade-paper": {"condition": "service_healthy"},
        "qlib-refresh-supervisor-paper": {"condition": "service_healthy"},
    }
    assert payload["smartcrypto-bot-paper"]["depends_on"] == {
        "qlib-refresh-supervisor-paper": {"condition": "service_healthy"}
    }
    assert "user" not in payload["smartcrypto-bot-paper"]


def test_phase14_has_specific_healthcheck() -> None:
    health = services()["phase14-feedback-sync-paper"]["healthcheck"]
    command = [str(item) for item in health["test"]]

    assert command[:4] == [
        "CMD",
        "python",
        "-m",
        "smartcrypto.runtime.phase14_feedback_sync_healthcheck",
    ]
    assert "--quiet" in command
    assert "--report" in command
    assert "--snapshot" in command


def test_optional_profiles_and_decision_ledger_remain_disabled() -> None:
    payload = services()
    ledger = yaml.safe_load(LEDGER_CONFIG.read_text(encoding="utf-8"))

    assert payload["paper-autolearning-scheduler"]["profiles"] == ["autolearning"]
    assert payload["trade-event-notifications-paper"]["profiles"] == ["notifications"]
    assert ledger["enabled"] is False
    assert ledger["writer_enabled"] is False
    assert ledger["trade_link_enabled"] is False
    assert ledger["writer_profile"]["activation_state"] == "disabled"
    assert ledger["writer_profile"]["runtime_write_authorized"] is False


class FakeLock:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def acquire(self, timeout_seconds: float) -> None:
        assert timeout_seconds == bootstrap.BOOTSTRAP_LOCK_TIMEOUT_SECONDS
        self.events.append("lock_acquire")

    def release(self) -> None:
        self.events.append("lock_release")


class StopAtExec(RuntimeError):
    pass


def test_bootstrap_lock_wraps_prepare_drop_and_probe_but_not_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.QLIB_REFRESH_SERVICE]
    events: list[str] = []
    args = argparse.Namespace(
        service=profile.service,
        uid=10001,
        gid=10001,
        path=list(profile.directories),
        command=["python", "worker.py"],
        lock_timeout_seconds=bootstrap.BOOTSTRAP_LOCK_TIMEOUT_SECONDS,
    )

    monkeypatch.setattr(bootstrap, "parse_args", lambda _: args)
    monkeypatch.setattr(
        bootstrap,
        "prepare_runtime_permissions",
        lambda *_, **__: events.append("prepare")
        or {"directory_count": 4, "file_count": 0},
    )
    monkeypatch.setattr(
        bootstrap,
        "drop_privileges",
        lambda **_: events.append("drop"),
    )
    monkeypatch.setattr(
        bootstrap,
        "verify_runtime_writability",
        lambda _: events.append("probe")
        or {
            "directory_count": 4,
            "probe_attempt_count": 4,
            "probe_retry_count": 0,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "emit_event",
        lambda event, **_: events.append(event),
    )

    def stop(_command: list[str]) -> None:
        events.append("exec")
        raise StopAtExec

    monkeypatch.setattr(bootstrap, "exec_application", stop)

    with pytest.raises(StopAtExec):
        bootstrap.main([], lock_factory=lambda _path: FakeLock(events))

    assert events == [
        "lock_acquire",
        "runtime_bootstrap_lock_acquired",
        "prepare",
        "runtime_permissions_prepared",
        "drop",
        "runtime_privileges_dropped",
        "probe",
        "runtime_writability_verified",
        "lock_release",
        "runtime_bootstrap_lock_released",
        "exec",
    ]


def test_lock_timeout_fails_closed_before_permission_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = bootstrap.SERVICE_PROFILES[bootstrap.PHASE14_SERVICE]
    args = argparse.Namespace(
        service=profile.service,
        uid=10001,
        gid=10001,
        path=list(profile.directories),
        command=["python", "worker.py"],
        lock_timeout_seconds=1.0,
    )
    prepare_calls: list[bool] = []

    class TimeoutLock:
        def acquire(self, _timeout_seconds: float) -> None:
            raise bootstrap.RuntimeBootstrapError("runtime_bootstrap_lock_timeout")

        def release(self) -> None:
            raise AssertionError("release must not run when acquire failed")

    monkeypatch.setattr(bootstrap, "parse_args", lambda _: args)
    monkeypatch.setattr(
        bootstrap,
        "prepare_runtime_permissions",
        lambda *_, **__: prepare_calls.append(True),
    )
    monkeypatch.setattr(bootstrap, "emit_event", lambda *_args, **_kwargs: None)

    result = bootstrap.main([], lock_factory=lambda _path: TimeoutLock())

    assert result == 2
    assert prepare_calls == []


def test_lock_symlink_is_rejected_before_fcntl_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == lock_path or original(self),
    )

    lock = bootstrap.PosixAdvisoryBootstrapLock(lock_path)
    with pytest.raises(
        bootstrap.RuntimeBootstrapError,
        match="runtime_bootstrap_lock_symlink_forbidden",
    ):
        lock.acquire(1.0)


def test_lock_path_is_nominal_regular_file_contract() -> None:
    assert bootstrap.BOOTSTRAP_LOCK_PATH == (
        "/app/data/reports/.runtime-permissions-bootstrap.lock"
    )
    assert bootstrap.BOOTSTRAP_LOCK_TIMEOUT_SECONDS > 0
    assert "fcntl.flock" in (
        ROOT / "scripts/docker_runtime_permissions_bootstrap.py"
    ).read_text(encoding="utf-8")


def test_bootstrap_safety_flags_remain_fail_closed() -> None:
    assert bootstrap.SAFE_FLAGS["paper_only"] is True
    assert bootstrap.SAFE_FLAGS["shadow_only"] is True
    for field in (
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        assert bootstrap.SAFE_FLAGS[field] is False
