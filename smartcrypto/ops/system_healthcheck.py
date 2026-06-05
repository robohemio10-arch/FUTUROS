from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.paper_shadow_soak_report import as_bool, int_value, safety_payload, unsafe_safety_flags, write_report

DEFAULT_READINESS_REPORT = Path("data/reports/readiness_gate_report.json")
DEFAULT_PAPER_SOAK_REPORT = Path("data/reports/paper_soak_report.json")
DEFAULT_CRITICAL_ALERTING_REPORT = Path("data/reports/critical_alerting_report.json")
DEFAULT_RISK_RECOVERY_REPORT = Path("data/reports/risk_recovery_mode_audit_report.json")
DEFAULT_MARKET_HEALTH_REPORT = Path("data/reports/market_data_health_audit_report.json")
DEFAULT_STATE_RECONCILIATION_REPORT = Path("data/reports/state_reconciliation_audit_report.json")
DEFAULT_LEDGER_REPORT = Path("data/reports/order_intent_capital_ledger_audit_report.json")
DEFAULT_BACKUP_REPORT = Path("data/reports/backup_snapshot_report.json")
DEFAULT_RESTORE_REPORT = Path("data/reports/restore_dry_run_report.json")
DEFAULT_DOCKERFILE = Path("docker/smartcrypto/Dockerfile")
DEFAULT_COMPOSE_FILE = Path("docker-compose.paper.yml")
DEFAULT_REPORT_PATH = Path("data/reports/system_healthcheck_report.json")
REPORT_TIMESTAMP_KEYS = (
    "generated_at_utc",
    "audited_at_utc",
    "evaluated_at_utc",
    "created_at_utc",
    "created_at",
    "updated_at_utc",
)

CRITICAL_REPORTS = (
    "readiness_report",
    "paper_soak_report",
    "critical_alerting_report",
    "risk_recovery_report",
    "market_health_report",
    "state_reconciliation_report",
    "ledger_report",
)


def run_system_healthcheck(
    *,
    readiness_report: str | Path | None = DEFAULT_READINESS_REPORT,
    paper_soak_report: str | Path | None = DEFAULT_PAPER_SOAK_REPORT,
    critical_alerting_report: str | Path | None = DEFAULT_CRITICAL_ALERTING_REPORT,
    risk_recovery_report: str | Path | None = DEFAULT_RISK_RECOVERY_REPORT,
    market_health_report: str | Path | None = DEFAULT_MARKET_HEALTH_REPORT,
    state_reconciliation_report: str | Path | None = DEFAULT_STATE_RECONCILIATION_REPORT,
    ledger_report: str | Path | None = DEFAULT_LEDGER_REPORT,
    backup_report: str | Path | None = DEFAULT_BACKUP_REPORT,
    restore_report: str | Path | None = DEFAULT_RESTORE_REPORT,
    dockerfile: str | Path | None = DEFAULT_DOCKERFILE,
    compose_file: str | Path | None = DEFAULT_COMPOSE_FILE,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    required_dirs: list[str | Path] | None = None,
    writable_dirs: list[str | Path] | None = None,
    state_repository_path: str | Path | None = None,
    max_report_age_seconds: int = 900,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = ensure_utc(now or datetime.now(timezone.utc))
    paths = {
        "readiness_report": readiness_report,
        "paper_soak_report": paper_soak_report,
        "critical_alerting_report": critical_alerting_report,
        "risk_recovery_report": risk_recovery_report,
        "market_health_report": market_health_report,
        "state_reconciliation_report": state_reconciliation_report,
        "ledger_report": ledger_report,
        "backup_report": backup_report,
        "restore_report": restore_report,
        "dockerfile": dockerfile,
        "compose_file": compose_file,
    }
    payloads = {name: load_json(path) for name, path in paths.items() if name.endswith("_report")}
    checks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    blockers: list[str] = []
    stale_reports: list[dict[str, Any]] = []
    safety = safety_payload(safety_overrides)
    blockers.extend(f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety))

    for name in CRITICAL_REPORTS:
        check = check_report(name, paths[name], payloads[name], generated_at, max_report_age_seconds)
        checks[name] = check
        if check["status"] == "missing":
            warnings.append(f"missing_report:{name}")
            if strict:
                blockers.append(f"missing_required_report:{name}")
        elif check["status"] == "stale":
            warnings.append(f"stale_report:{name}")
            stale_reports.append(stale_report_detail(name, check))
        elif check["status"] == "blocked":
            blockers.extend(report_blockers(name, payloads[name]))
        for flag in unsafe_safety_flags(payloads[name]):
            blockers.append(f"unsafe_source_safety_flag:{name}:{flag}")

    for name in ("backup_report", "restore_report"):
        check = check_report(name, paths[name], payloads[name], generated_at, max_report_age_seconds)
        checks[name] = check
        if check["status"] == "missing":
            warnings.append(f"missing_report:{name}")
            if strict:
                blockers.append(f"missing_required_report:{name}")
        elif check["status"] == "blocked":
            blockers.extend(report_blockers(name, payloads[name]))

    blockers.extend(domain_blockers(payloads))
    checks["dockerfile"] = check_file_exists("dockerfile", dockerfile)
    checks["compose_file"] = check_file_exists("compose_file", compose_file)
    checks["docker_healthcheck"] = check_docker_healthcheck(dockerfile, compose_file)
    if checks["docker_healthcheck"]["status"] != "ok":
        warnings.append("missing_docker_healthcheck")
        if strict:
            blockers.append("missing_docker_healthcheck")

    for directory in required_dirs or []:
        name = f"required_dir:{Path(directory).name}"
        checks[name] = check_directory(directory)
        if checks[name]["status"] != "ok":
            warnings.append(f"missing_required_dir:{directory}")
            if strict:
                blockers.append(f"missing_required_dir:{directory}")
    for directory in writable_dirs or []:
        name = f"writable_dir:{Path(directory).name}"
        checks[name] = check_writable_directory(directory)
        if checks[name]["status"] != "ok":
            warnings.append(f"unwritable_dir:{directory}")
            if strict:
                blockers.append(f"unwritable_dir:{directory}")
    if state_repository_path is not None:
        checks["state_repository"] = check_file_exists("state_repository", state_repository_path)
        if checks["state_repository"]["status"] != "ok":
            warnings.append("missing_state_repository")
            if strict:
                blockers.append("missing_state_repository")

    failed_checks = sorted(name for name, check in checks.items() if check["status"] in {"blocked", "missing", "stale", "warning"})
    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    status = "blocked" if blockers else "missing_data" if any(checks[name]["status"] == "missing" for name in CRITICAL_REPORTS) else "warning" if warnings else "ok"
    report = {
        "status": status,
        "reason": ";".join(blockers or warnings or ["system_healthcheck_ok"]),
        "generated_at_utc": iso(generated_at),
        "runtime_mode": "paper",
        "checks": checks,
        "failed_checks": failed_checks,
        "warnings": warnings,
        "stale_reports": stale_reports,
        "stale_reports_count": len(stale_reports),
        "blocking_findings": blockers,
        **safety,
    }
    write_report(report, report_path)
    return report


def domain_blockers(payloads: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    readiness = payloads["readiness_report"]
    soak = payloads["paper_soak_report"]
    critical = payloads["critical_alerting_report"]
    risk = payloads["risk_recovery_report"]
    state = payloads["state_reconciliation_report"]
    ledger = payloads["ledger_report"]
    if str(readiness.get("status", "")).lower() == "blocked" or readiness.get("readiness_approved") is False:
        blockers.append("readiness_gate_blocked")
    if as_bool(readiness.get("no_trade_policy_present")) or as_bool(soak.get("no_trade_policy_present")) or "no_trade_policy_active" in list_values(readiness.get("readiness_blockers")) or "monte_carlo_no_trade_policy_active" in list_values(soak.get("readiness_blockers")):
        blockers.append("no_trade_policy_active")
    if "soak_days_below_required" in list_values(readiness.get("readiness_blockers")) or "soak_days_below_required" in list_values(soak.get("readiness_blockers")):
        blockers.append("soak_days_below_required")
    if str(critical.get("status", "")).lower() == "blocked" or int_value(critical.get("critical_alerts")) > 0:
        blockers.append("critical_alerting_blocked")
    if str(risk.get("recommended_mode", "")).upper() in {"PANIC", "RECONCILING"}:
        blockers.append(f"risk_recovery_mode_{str(risk.get('recommended_mode')).lower()}")
    if str(state.get("status", "")).lower() == "blocked" or as_bool(state.get("reconciliation_required")):
        blockers.append("state_reconciliation_blocked")
    if str(ledger.get("status", "")).lower() == "blocked":
        blockers.append("ledger_audit_blocked")
    return blockers


def report_blockers(name: str, payload: Mapping[str, Any]) -> list[str]:
    if name == "readiness_report":
        blockers = ["readiness_gate_blocked"]
        if as_bool(payload.get("no_trade_policy_present")) or "no_trade_policy_active" in list_values(payload.get("readiness_blockers")):
            blockers.append("no_trade_policy_active")
        if "soak_days_below_required" in list_values(payload.get("readiness_blockers")):
            blockers.append("soak_days_below_required")
        return blockers
    if name == "paper_soak_report":
        blockers = []
        report_blockers_list = list_values(payload.get("readiness_blockers"))
        if as_bool(payload.get("no_trade_policy_present")) or "monte_carlo_no_trade_policy_active" in report_blockers_list:
            blockers.append("no_trade_policy_active")
        if "soak_days_below_required" in report_blockers_list:
            blockers.append("soak_days_below_required")
        return blockers or ["paper_soak_report_blocked"]
    return [f"{name}_blocked"]


def check_report(name: str, path: str | Path | None, payload: dict[str, Any], now: datetime, max_age_seconds: int) -> dict[str, Any]:
    target = Path(path) if path is not None else None
    if target is None or not target.exists():
        return {"status": "missing", "reason": "missing_report", "report_name": name, "exists": False, "path": str(target) if target else None}
    payload_status = str(payload.get("status", "ok")).lower()
    if payload_status in {"blocked", "critical", "failed"}:
        return {
            "status": "blocked",
            "reason": f"status_{payload_status}",
            "report_name": name,
            "exists": True,
            "path": str(target),
            "source_status": payload_status,
        }
    age = report_age_seconds(target, payload, now)
    status = "stale" if age is not None and age > max_age_seconds else "ok"
    reason = "report_stale" if status == "stale" else "ok"
    timestamp_key, timestamp_value = report_timestamp(payload)
    return {
        "status": status,
        "reason": reason,
        "report_name": name,
        "exists": True,
        "path": str(target),
        "age_seconds": age,
        "age_minutes": round(age / 60.0, 3) if age is not None else None,
        "stale_limit_seconds": int(max_age_seconds),
        "stale_limit_minutes": round(max_age_seconds / 60.0, 3),
        "timestamp_key": timestamp_key,
        "report_timestamp_utc": timestamp_value,
        "source_status": payload_status,
    }


def stale_report_detail(name: str, check: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "report_name": name,
        "path": check.get("path"),
        "reason": check.get("reason"),
        "source_status": check.get("source_status"),
        "age_seconds": check.get("age_seconds"),
        "age_minutes": check.get("age_minutes"),
        "stale_limit_seconds": check.get("stale_limit_seconds"),
        "stale_limit_minutes": check.get("stale_limit_minutes"),
        "timestamp_key": check.get("timestamp_key"),
        "report_timestamp_utc": check.get("report_timestamp_utc"),
    }


def report_age_seconds(path: Path, payload: Mapping[str, Any], now: datetime) -> int | None:
    _, timestamp_value = report_timestamp(payload)
    parsed = parse_utc(timestamp_value)
    if parsed is not None:
        return max(0, int((now - parsed).total_seconds()))
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return max(0, int((now - mtime).total_seconds()))


def report_timestamp(payload: Mapping[str, Any]) -> tuple[str | None, Any]:
    for key in REPORT_TIMESTAMP_KEYS:
        value = payload.get(key)
        if parse_utc(value) is not None:
            return key, value
    return None, None


def check_file_exists(name: str, path: str | Path | None) -> dict[str, Any]:
    target = Path(path) if path is not None else None
    if target is None or not target.exists():
        return {"status": "missing", "reason": f"{name}_missing", "path": str(target) if target else None}
    return {"status": "ok", "reason": "ok", "path": str(target)}


def check_directory(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists() or not target.is_dir():
        return {"status": "missing", "reason": "directory_missing", "path": str(target)}
    return {"status": "ok", "reason": "ok", "path": str(target)}


def check_writable_directory(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists() or not target.is_dir():
        return {"status": "missing", "reason": "directory_missing", "path": str(target)}
    try:
        with tempfile.NamedTemporaryFile(dir=target, delete=True) as handle:
            handle.write(b"healthcheck")
    except OSError as exc:
        return {"status": "blocked", "reason": f"directory_not_writable:{exc}", "path": str(target)}
    return {"status": "ok", "reason": "ok", "path": str(target)}


def check_docker_healthcheck(dockerfile: str | Path | None, compose_file: str | Path | None) -> dict[str, Any]:
    inspected = []
    for path in (dockerfile, compose_file):
        if path is None:
            continue
        target = Path(path)
        if not target.exists():
            continue
        inspected.append(str(target))
        text = target.read_text(encoding="utf-8", errors="ignore").lower()
        if "healthcheck" in text:
            return {"status": "ok", "reason": "healthcheck_documented", "paths": inspected}
    return {"status": "warning", "reason": "docker_healthcheck_missing", "paths": inspected}


def load_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        return {"status": "blocked", "error": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "blocked", "error": "invalid_json_payload"}


def list_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")
