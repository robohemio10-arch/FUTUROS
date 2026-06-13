from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.daily_evidence_pack.contracts import LockState, PackStatus, StepDefinition


SCHEMA_VERSION = "daily_evidence_pack_v1"
PROJECT_NAME = "SMART FUTUROS"
DEFAULT_LOCK_PATH = Path("data/runtime/daily_evidence_pack.lock")
DEFAULT_STALE_LOCK_SECONDS = 3600.0
MAX_OUTPUT_EXCERPT = 1200
SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "sends_orders": False,
    "changes_risk": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}
SAFE_SUMMARY_KEYS = (
    "status",
    "reason",
    "finding_count",
    "critical_count",
    "high_count",
    "medium_count",
    "low_count",
    "scanned_files",
    "missing_evidence",
    "blocking_reasons",
    "read_only_count",
    "writable_required_count",
    "writable_unjustified_count",
    "unknown_requires_review_count",
    "unpinned_count",
    "stable_tag_count",
    "latest_tag_count",
    "digest_pinned_count",
    "invalid_digest_count",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(token|secret|api[_-]?key|password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(SMARTCRYPTO_(?:TELEGRAM_BOT_TOKEN|NTFY_TOKEN))\s*=\s*([^\s]+)"),
)


DEFAULT_STEPS: tuple[StepDefinition, ...] = (
    StepDefinition(
        "runtime_evidence_pack_v2",
        "scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py",
        ("--project-root", "{project_root}", "--no-write", "--json"),
        supports_container_snapshot=True,
    ),
    StepDefinition(
        "paper_runtime_health",
        "scripts/audit_paper_runtime_health_and_freshness.py",
        ("--project-root", "{project_root}", "--json"),
        supports_container_snapshot=True,
    ),
    StepDefinition(
        "notification_runtime_permissions",
        "scripts/audit_notification_runtime_permissions.py",
        ("--project-root", "{project_root}", "--json"),
    ),
    StepDefinition(
        "operational_exception_swallowing",
        "scripts/audit_operational_exception_swallowing.py",
        ("--project-root", "{project_root}", "--json", "--fail-on", "none"),
    ),
    StepDefinition(
        "freqtrade_image_pin_digest_policy",
        "scripts/audit_freqtrade_image_pin_digest_policy.py",
        ("--project-root", "{project_root}", "--json"),
    ),
    StepDefinition(
        "lockfile_hash_integrity",
        "scripts/audit_lockfile_hash_integrity.py",
        ("--project-root", "{project_root}", "--json"),
    ),
    StepDefinition(
        "docker_compose_readonly_volumes",
        "scripts/audit_docker_compose_readonly_volumes.py",
        ("--project-root", "{project_root}", "--json"),
    ),
    StepDefinition(
        "manifest_check",
        "scripts/generate_project_manifest.py",
        ("--project-root", "{project_root}", "--check"),
    ),
    StepDefinition(
        "versioned_secret_scan",
        "scripts/scan_versioned_secrets.py",
        ("--project-root", "{project_root}", "--json"),
    ),
)
ALLOWED_SCRIPTS = frozenset(step.script for step in DEFAULT_STEPS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex else "[REDACTED]", text)
    text = text.replace(".env", "[ENV_FILE]")
    return text[-MAX_OUTPUT_EXCERPT:]


def resolve_under_root(root: Path, relative_path: str | Path) -> Path:
    candidate = Path(relative_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path_outside_project_root:{resolved}") from exc
    return resolved


def build_command(
    step: StepDefinition,
    *,
    project_root: Path,
    include_container_snapshot: bool,
) -> list[str]:
    if step.script not in ALLOWED_SCRIPTS:
        raise ValueError(f"script_not_allowlisted:{step.script}")
    script_path = resolve_under_root(project_root, step.script)
    expected_path = (project_root / step.script).resolve()
    if script_path != expected_path or not script_path.is_file():
        raise ValueError(f"allowlisted_script_missing_or_invalid:{step.script}")
    arguments = [argument.format(project_root=str(project_root)) for argument in step.arguments]
    if include_container_snapshot and step.supports_container_snapshot:
        arguments.append("--collect-containers")
    return [sys.executable, str(script_path), *arguments]


def parse_json_output(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def normalize_status(value: Any) -> PackStatus:
    normalized = str(value or "").strip().lower()
    if normalized == "ok":
        return "ok"
    if normalized == "warning":
        return "warning"
    return "blocked"


def safe_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    return {key: payload[key] for key in SAFE_SUMMARY_KEYS if key in payload}


def execute_step(
    step: StepDefinition,
    *,
    project_root: Path,
    timeout_seconds: float,
    include_container_snapshot: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = monotonic()
    try:
        command = build_command(
            step,
            project_root=project_root,
            include_container_snapshot=include_container_snapshot,
        )
    except ValueError as exc:
        return {
            "name": step.name,
            "command": [],
            "status": "blocked",
            "reason": str(exc),
            "elapsed_seconds": round(monotonic() - started, 6),
            "returncode": None,
            "timed_out": False,
            "json_parsed": False,
            "summary": {},
            "stdout_excerpt": "",
            "stderr_excerpt": "",
        }
    try:
        completed = runner(
            command,
            cwd=project_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": step.name,
            "command": command,
            "status": "blocked",
            "reason": "step_timeout",
            "elapsed_seconds": round(monotonic() - started, 6),
            "returncode": None,
            "timed_out": True,
            "json_parsed": False,
            "summary": {},
            "stdout_excerpt": sanitize_output(exc.stdout),
            "stderr_excerpt": sanitize_output(exc.stderr),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": step.name,
            "command": command,
            "status": "blocked",
            "reason": f"step_execution_failed:{type(exc).__name__}",
            "elapsed_seconds": round(monotonic() - started, 6),
            "returncode": None,
            "timed_out": False,
            "json_parsed": False,
            "summary": {},
            "stdout_excerpt": "",
            "stderr_excerpt": "",
        }
    stdout = sanitize_output(completed.stdout)
    stderr = sanitize_output(completed.stderr)
    payload = parse_json_output(completed.stdout or "")
    parsed_status = normalize_status(payload.get("status") if payload else None)
    if payload is None:
        status: PackStatus = "blocked"
        reason = "step_json_missing_or_invalid"
    elif completed.returncode != 0 and parsed_status == "ok":
        status = "blocked"
        reason = "step_nonzero_exit_with_ok_payload"
    else:
        status = parsed_status
        reason = str(payload.get("reason") or f"step_{status}")
    return {
        "name": step.name,
        "command": command,
        "status": status,
        "reason": reason,
        "elapsed_seconds": round(monotonic() - started, 6),
        "returncode": int(completed.returncode),
        "timed_out": False,
        "json_parsed": payload is not None,
        "summary": safe_summary(payload),
        "stdout_excerpt": stdout,
        "stderr_excerpt": stderr,
    }


def consolidate_steps(steps: Sequence[Mapping[str, Any]]) -> tuple[PackStatus, str, dict[str, int]]:
    summary = {
        "ok_count": sum(step.get("status") == "ok" for step in steps),
        "warning_count": sum(step.get("status") == "warning" for step in steps),
        "blocked_count": sum(step.get("status") == "blocked" for step in steps),
        "timeout_count": sum(bool(step.get("timed_out")) for step in steps),
        "failed_count": sum(step.get("returncode") not in {0, None} for step in steps),
    }
    if summary["blocked_count"]:
        return "blocked", "one_or_more_evidence_steps_blocked", summary
    if summary["warning_count"]:
        return "warning", "one_or_more_evidence_steps_warning", summary
    return "ok", "all_evidence_steps_ok", summary


class ExecutionLock:
    def __init__(self, path: Path, *, stale_seconds: float = DEFAULT_STALE_LOCK_SECONDS) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def acquire(self, *, now_epoch: float | None = None) -> LockState:
        current = time.time() if now_epoch is None else now_epoch
        self.path.parent.mkdir(parents=True, exist_ok=True)
        recovered = False
        if self.path.exists():
            try:
                age = max(0.0, current - self.path.stat().st_mtime)
            except OSError:
                return LockState(False, False, "lock_stat_failed")
            if age <= self.stale_seconds:
                return LockState(False, False, "daily_evidence_pack_already_running")
            try:
                self.path.unlink()
                recovered = True
            except OSError:
                return LockState(False, False, "stale_lock_recovery_failed")
        payload = json.dumps({"pid": os.getpid(), "created_epoch": current}, sort_keys=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
        except FileExistsError:
            return LockState(False, False, "daily_evidence_pack_already_running")
        except OSError:
            return LockState(False, recovered, "lock_acquisition_failed")
        self.acquired = True
        return LockState(True, recovered, "stale_lock_recovered" if recovered else "lock_acquired")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def blocked_lock_report(pack_date: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "runtime_mode": "paper",
        "pack_date": pack_date,
        "started_utc": None,
        "finished_utc": None,
        "elapsed_seconds": 0.0,
        "status": "blocked",
        "reason": reason,
        "write_performed": False,
        "output_files": [],
        "lock_recovered": False,
        "steps": [],
        "summary": {"ok_count": 0, "warning_count": 0, "blocked_count": 1, "timeout_count": 0, "failed_count": 0},
        **SAFETY_FLAGS,
    }


def run_daily_evidence_pack(
    *,
    project_root: str | Path = ".",
    output_dir: str | Path = "data/reports",
    no_write: bool = False,
    timeout_seconds: float = 120.0,
    include_container_snapshot: bool = False,
    pack_date: date | None = None,
    steps: Sequence[StepDefinition] = DEFAULT_STEPS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    lock_path: str | Path = DEFAULT_LOCK_PATH,
    lock_stale_seconds: float = DEFAULT_STALE_LOCK_SECONDS,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds_must_be_positive")
    selected_date = pack_date or now().date()
    date_text = selected_date.isoformat()
    resolved_lock = resolve_under_root(root, lock_path)
    lock = ExecutionLock(resolved_lock, stale_seconds=lock_stale_seconds)
    lock_state = lock.acquire()
    if not lock_state.acquired:
        return blocked_lock_report(date_text, lock_state.reason)

    started_at = now()
    started_monotonic = monotonic()
    try:
        step_reports = [
            execute_step(
                step,
                project_root=root,
                timeout_seconds=timeout_seconds,
                include_container_snapshot=include_container_snapshot,
                runner=runner,
                monotonic=monotonic,
            )
            for step in steps
        ]
        status, reason, summary = consolidate_steps(step_reports)
        finished_at = now()
        output_root = resolve_under_root(root, output_dir)
        daily_path = output_root / f"daily_evidence_pack_{selected_date.strftime('%Y%m%d')}.json"
        latest_path = output_root / "daily_evidence_pack_latest.json"
        output_files = [] if no_write else [str(daily_path), str(latest_path)]
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "project_name": PROJECT_NAME,
            "runtime_mode": "paper",
            "pack_date": date_text,
            "started_utc": started_at.isoformat().replace("+00:00", "Z"),
            "finished_utc": finished_at.isoformat().replace("+00:00", "Z"),
            "elapsed_seconds": round(monotonic() - started_monotonic, 6),
            "status": status,
            "reason": reason,
            "write_performed": not no_write,
            "output_files": output_files,
            "lock_recovered": lock_state.recovered_stale_lock,
            "include_container_snapshot": include_container_snapshot,
            "steps": step_reports,
            "summary": summary,
            **SAFETY_FLAGS,
        }
        if not no_write:
            try:
                atomic_write_json(daily_path, report)
                atomic_write_json(latest_path, report)
            except OSError as exc:
                report["status"] = "blocked"
                report["reason"] = f"daily_evidence_pack_write_failed:{type(exc).__name__}"
                report["write_performed"] = False
                report["output_files"] = []
        return report
    finally:
        lock.release()
