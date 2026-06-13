from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.ops.daily_evidence_pack.contracts import StepDefinition
from smartcrypto.ops.daily_evidence_pack.orchestrator import (
    ALLOWED_SCRIPTS,
    DEFAULT_STEPS,
    ExecutionLock,
    build_command,
    consolidate_steps,
    execute_step,
    run_daily_evidence_pack,
    sanitize_output,
)


ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.25
        return self.value


def fixed_now() -> datetime:
    return datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


def install_allowed_script(root: Path, relative_path: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('fixture')\n", encoding="utf-8")


def fake_runner_for(statuses: list[str], calls: list[dict[str, Any]] | None = None) -> Any:
    remaining = iter(statuses)

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append({"command": command, **kwargs})
        status = next(remaining)
        payload = {"status": status, "reason": f"fixture_{status}"}
        return subprocess.CompletedProcess(command, 1 if status == "blocked" else 0, json.dumps(payload), "")

    return runner


def test_consolidates_ok_warning_and_blocked_conservatively() -> None:
    ok_status, ok_reason, ok_summary = consolidate_steps([{"status": "ok", "returncode": 0, "timed_out": False}])
    warning_status, warning_reason, _ = consolidate_steps(
        [{"status": "ok", "returncode": 0, "timed_out": False}, {"status": "warning", "returncode": 0, "timed_out": False}]
    )
    blocked_status, blocked_reason, blocked_summary = consolidate_steps(
        [{"status": "warning", "returncode": 0, "timed_out": False}, {"status": "blocked", "returncode": 1, "timed_out": False}]
    )

    assert (ok_status, ok_reason) == ("ok", "all_evidence_steps_ok")
    assert ok_summary["ok_count"] == 1
    assert (warning_status, warning_reason) == ("warning", "one_or_more_evidence_steps_warning")
    assert (blocked_status, blocked_reason) == ("blocked", "one_or_more_evidence_steps_blocked")
    assert blocked_summary["blocked_count"] == 1


def test_blocked_step_makes_pack_blocked(tmp_path: Path) -> None:
    steps = DEFAULT_STEPS[:2]
    for step in steps:
        install_allowed_script(tmp_path, step.script)

    report = run_daily_evidence_pack(
        project_root=tmp_path,
        no_write=True,
        steps=steps,
        runner=fake_runner_for(["ok", "blocked"]),
        now=fixed_now,
        monotonic=Clock(),
    )

    assert report["status"] == "blocked"
    assert report["summary"]["blocked_count"] == 1


def test_warning_without_blocked_makes_pack_warning(tmp_path: Path) -> None:
    steps = DEFAULT_STEPS[:2]
    for step in steps:
        install_allowed_script(tmp_path, step.script)

    report = run_daily_evidence_pack(
        project_root=tmp_path,
        no_write=True,
        steps=steps,
        runner=fake_runner_for(["ok", "warning"]),
        now=fixed_now,
        monotonic=Clock(),
    )

    assert report["status"] == "warning"
    assert report["summary"]["warning_count"] == 1


def test_step_timeout_is_blocked(tmp_path: Path) -> None:
    step = DEFAULT_STEPS[0]
    install_allowed_script(tmp_path, step.script)

    def timeout_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="token=secret-value")

    result = execute_step(
        step,
        project_root=tmp_path,
        timeout_seconds=1.0,
        include_container_snapshot=False,
        runner=timeout_runner,
        monotonic=Clock(),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "step_timeout"
    assert result["timed_out"] is True
    assert "secret-value" not in result["stdout_excerpt"]


def test_subprocess_uses_argument_list_shell_false_and_fixed_allowlist(tmp_path: Path) -> None:
    step = DEFAULT_STEPS[0]
    install_allowed_script(tmp_path, step.script)
    calls: list[dict[str, Any]] = []

    result = execute_step(
        step,
        project_root=tmp_path,
        timeout_seconds=4.0,
        include_container_snapshot=False,
        runner=fake_runner_for(["ok"], calls),
        monotonic=Clock(),
    )

    assert result["status"] == "ok"
    assert isinstance(calls[0]["command"], list)
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == 4.0
    assert Path(calls[0]["command"][1]).resolve() == (tmp_path / step.script).resolve()


def test_allowlist_blocks_arbitrary_or_outside_script(tmp_path: Path) -> None:
    malicious = StepDefinition("malicious", "../outside.py", ("--json",))
    (tmp_path.parent / "outside.py").write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="script_not_allowlisted"):
        build_command(malicious, project_root=tmp_path, include_container_snapshot=False)
    assert "../outside.py" not in ALLOWED_SCRIPTS


def test_container_snapshot_flag_is_opt_in(tmp_path: Path) -> None:
    step = DEFAULT_STEPS[0]
    install_allowed_script(tmp_path, step.script)

    default_command = build_command(step, project_root=tmp_path, include_container_snapshot=False)
    opt_in_command = build_command(step, project_root=tmp_path, include_container_snapshot=True)

    assert "--collect-containers" not in default_command
    assert opt_in_command[-1] == "--collect-containers"


def test_no_write_does_not_create_reports(tmp_path: Path) -> None:
    step = DEFAULT_STEPS[0]
    install_allowed_script(tmp_path, step.script)
    output_dir = tmp_path / "data" / "reports"

    report = run_daily_evidence_pack(
        project_root=tmp_path,
        output_dir=output_dir,
        no_write=True,
        steps=(step,),
        runner=fake_runner_for(["ok"]),
        now=fixed_now,
        monotonic=Clock(),
    )

    assert report["write_performed"] is False
    assert report["output_files"] == []
    assert not output_dir.exists()


def test_normal_write_creates_daily_and_latest_atomically(tmp_path: Path) -> None:
    step = DEFAULT_STEPS[0]
    install_allowed_script(tmp_path, step.script)

    report = run_daily_evidence_pack(
        project_root=tmp_path,
        output_dir="data/reports",
        no_write=False,
        pack_date=date(2026, 6, 13),
        steps=(step,),
        runner=fake_runner_for(["ok"]),
        now=fixed_now,
        monotonic=Clock(),
    )
    daily = tmp_path / "data/reports/daily_evidence_pack_20260613.json"
    latest = tmp_path / "data/reports/daily_evidence_pack_latest.json"

    assert report["write_performed"] is True
    assert daily.is_file() and latest.is_file()
    assert json.loads(daily.read_text(encoding="utf-8"))["status"] == "ok"
    assert daily.read_text(encoding="utf-8") == latest.read_text(encoding="utf-8")
    assert not list(daily.parent.glob("*.tmp"))


def test_active_lock_blocks_second_execution(tmp_path: Path) -> None:
    lock_path = tmp_path / "data/runtime/daily_evidence_pack.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{}\n", encoding="utf-8")

    report = run_daily_evidence_pack(project_root=tmp_path, no_write=True, steps=(), lock_path=lock_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "daily_evidence_pack_already_running"
    assert report["write_performed"] is False


def test_stale_lock_is_recovered_and_cleaned(tmp_path: Path) -> None:
    lock_path = tmp_path / "data/runtime/daily_evidence_pack.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{}\n", encoding="utf-8")
    lock = ExecutionLock(lock_path, stale_seconds=10.0)

    state = lock.acquire(now_epoch=lock_path.stat().st_mtime + 20.0)
    lock.release()

    assert state.acquired is True
    assert state.recovered_stale_lock is True
    assert state.reason == "stale_lock_recovered"
    assert not lock_path.exists()


def test_sanitization_removes_tokens_secrets_and_env_filename() -> None:
    text = "token=abc123 password:xyz SMARTCRYPTO_TELEGRAM_BOT_TOKEN=telegram-secret Bearer bearer-secret .env"
    sanitized = sanitize_output(text)

    assert "abc123" not in sanitized
    assert "xyz" not in sanitized
    assert "telegram-secret" not in sanitized
    assert "bearer-secret" not in sanitized
    assert ".env" not in sanitized


def test_safety_flags_are_always_preserved(tmp_path: Path) -> None:
    report = run_daily_evidence_pack(
        project_root=tmp_path,
        no_write=True,
        steps=(),
        now=fixed_now,
        monotonic=Clock(),
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_default_steps_exclude_writers_schedulers_and_notification_dispatch() -> None:
    scripts = {step.script for step in DEFAULT_STEPS}

    assert "scripts/build_dashboard_snapshots.py" not in scripts
    assert all("scheduler" not in script and "telegram" not in script and "ntfy" not in script for script in scripts)
    assert all("docker" not in step.arguments for step in DEFAULT_STEPS)


def test_orchestrator_source_has_no_shell_or_arbitrary_script_cli() -> None:
    source = (ROOT / "smartcrypto/ops/daily_evidence_pack/orchestrator.py").read_text(encoding="utf-8").lower()
    cli_source = (ROOT / "scripts/run_daily_evidence_pack_orchestrator.py").read_text(encoding="utf-8").lower()

    assert "shell=true" not in source
    assert "notificationdispatcher" not in source
    assert "import ccxt" not in source
    assert "--script" not in cli_source
    assert "cron" not in cli_source
    assert "systemd" not in cli_source


def test_repository_allowlist_paths_are_inside_project_and_exist() -> None:
    for step in DEFAULT_STEPS:
        command = build_command(step, project_root=ROOT, include_container_snapshot=False)
        script_path = Path(command[1]).resolve()
        script_path.relative_to(ROOT)
        assert script_path.is_file()
