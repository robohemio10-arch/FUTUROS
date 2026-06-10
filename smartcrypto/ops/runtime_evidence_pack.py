from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "runtime_evidence_pack_v2"
DEFAULT_OUTPUT_DIR = Path("data/reports")
DEFAULT_EVIDENCE_PACK_PATH = Path("runtime_evidence_pack_v2.json")
DEFAULT_READINESS_SNAPSHOT_PATH = Path("readiness_snapshot_v2.json")
DIAGNOSTIC_SOAK_DAYS = 7
REQUIRED_SOAK_DAYS = 30

REQUIRED_EVIDENCE = {
    "paper_soak_report",
    "freqtrade_paper_db_authority_report",
    "readiness_gate_report",
    "monte_carlo_report",
    "manifest_check",
    "secret_scan",
}

EVIDENCE_PATHS = {
    "paper_soak_report": "data/reports/paper_soak_report.json",
    "paper_shadow_soak_report": "data/reports/paper_shadow_soak_report.json",
    "paper_shadow_soak_continuity_audit": "data/reports/paper_shadow_soak_continuity_audit.json",
    "freqtrade_paper_db_authority_report": "data/reports/freqtrade_paper_db_authority_report.json",
    "freqtrade_paper_db_snapshot_export": "data/reports/freqtrade_paper_db_snapshot_export.json",
    "readiness_gate_report": "data/reports/readiness_gate_report.json",
    "runtime_safety_report": "data/reports/runtime_safety_config_validation_report.json",
    "critical_alerting_report": "data/reports/critical_alerting_report.json",
    "risk_recovery_report": "data/reports/risk_recovery_mode_audit_report.json",
    "risk_readiness_report": "data/reports/risk_readiness_soak_dashboard_sources_report.json",
    "risk_budget_report": "data/reports/monte_carlo_risk_budget_policy_report.json",
    "monte_carlo_report": "data/reports/monte_carlo_risk_simulation_report.json",
    "monte_carlo_no_trade_recovery_report": "data/reports/monte_carlo_no_trade_recovery_report.json",
    "monte_carlo_no_trade_recovery_diagnostics": "data/reports/monte_carlo_no_trade_recovery_diagnostics.json",
    "event_backtest_report": "data/reports/event_driven_backtest_report.json",
    "anti_leakage_report": "data/reports/phase23_anti_leakage_report.json",
    "market_data_health_report": "data/reports/market_data_health_audit_report.json",
    "market_data_runtime_sources_report": "data/reports/market_data_health_runtime_sources_report.json",
    "system_healthcheck_report": "data/reports/system_healthcheck_report.json",
    "backup_snapshot_report": "data/reports/backup_snapshot_report.json",
    "restore_dry_run_report": "data/reports/restore_dry_run_report.json",
    "runtime_evidence_refresh_report": "data/reports/runtime_evidence_refresh_report.json",
    "ai_shadow_daily_update_summary": "data/reports/daily_ai_shadow_update_summary.json",
    "ai_shadow_daily_scheduler_report": "data/reports/ai_shadow_daily_update_scheduler_audit_report.json",
    "ai_shadow_incremental_trainer_report": "data/reports/ai_shadow_incremental_trainer_report.json",
    "ai_shadow_financial_threshold_report": "data/reports/ai_shadow_financial_threshold_evaluation_report.json",
    "ai_shadow_drift_report": "data/reports/ai_shadow_drift_monitor_report.json",
    "ai_shadow_threshold_readiness_evidence": "data/reports/ai_shadow_threshold_readiness_evidence.json",
    "ai_shadow_filter_decision_db_audit_summary": "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
}

RUNTIME_OBSERVABILITY_PATHS = {
    "trade_event_notifications_report": "data/reports/trade_event_notifications_report.json",
    "phase14_runtime_feedback_sync_report": "data/reports/phase14_runtime_feedback_sync_report.json",
    "phase14_summary": "data/reports/phase14_summary.json",
    "phase14_output_summary": "data/reports/phase14_output_summary.json",
    "phase14_closed_feedback_report": "data/reports/phase14_closed_feedback_report.json",
    "phase14_open_positions_report": "data/reports/phase14_open_positions_report.json",
    "qlib_paper_refresh_supervisor_report": "data/reports/qlib_paper_refresh_supervisor_report.json",
    "qlib_market_features_refresh_report": "data/reports/qlib_market_features_refresh_report.json",
    "qlib_fresh_prediction_runner_report": "data/reports/qlib_fresh_prediction_runner_report.json",
    "phase13_signal_producer_report": "data/reports/phase13_signal_producer_report.json",
    "ntfy_telegram_manual_validation_summary": "data/reports/ntfy_telegram_manual_validation_summary.json",
    "manual_notification_test_dispatch_report": "data/reports/manual_notification_test_dispatch_report.json",
    "telegram_trade_event_notifications_binance_links_handover": "data/reports/telegram_trade_event_notifications_binance_links_handover.json",
}

RUNTIME_STALE_AFTER_SECONDS = {
    "trade_event_notifications_report": 180,
    "phase14_runtime_feedback_sync_report": 300,
    "phase14_summary": 300,
    "phase14_output_summary": 300,
    "phase14_closed_feedback_report": 300,
    "phase14_open_positions_report": 300,
    "qlib_paper_refresh_supervisor_report": 900,
    "qlib_market_features_refresh_report": 900,
    "qlib_fresh_prediction_runner_report": 900,
    "phase13_signal_producer_report": 900,
}

OPTIONAL_MISSING_NEUTRAL_RUNTIME_REPORTS = {
    "manual_notification_test_dispatch_report",
}

EXPECTED_CONTAINER_SERVICES = (
    "freqtrade-paper",
    "phase14-feedback-sync-paper",
    "qlib-refresh-supervisor-paper",
    "smartcrypto-bot-paper",
    "smartcrypto-dashboard-paper",
    "trade-event-notifications-paper",
)

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
    "writes_official_trades_master",
    "changes_model",
    "changes_config",
    "changes_risk_config",
)

SAFE_TRUE_FLAGS = (
    "paper_only",
    "shadow_only",
)


@dataclass(frozen=True)
class BuildResult:
    evidence_pack: dict[str, Any]
    readiness_snapshot: dict[str, Any]
    evidence_pack_path: Path
    readiness_snapshot_path: Path
    write_performed: bool


def build_runtime_evidence_pack_and_readiness_snapshot_v2(
    *,
    project_root: str | Path = ".",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    no_write: bool = False,
    now: datetime | None = None,
    include_containers: bool = False,
    container_timeout_seconds: float = 3.0,
) -> BuildResult:
    root = Path(project_root).resolve()
    current_time = now or datetime.now(timezone.utc)
    generated_at = iso(current_time)
    output_path = resolve_under_root(root, output_dir)
    evidence_pack_path = output_path / DEFAULT_EVIDENCE_PACK_PATH
    readiness_snapshot_path = output_path / DEFAULT_READINESS_SNAPSHOT_PATH

    sources = collect_evidence_sources(root)
    runtime_sources = collect_runtime_observability_sources(root, now=current_time)
    service_catalog = collect_compose_service_catalog(root)
    container_snapshot = collect_docker_container_snapshot(timeout_seconds=container_timeout_seconds) if include_containers else {
        "status": "disabled",
        "reason": "container_collection_not_requested",
        "containers": [],
        "expected_services": list(EXPECTED_CONTAINER_SERVICES),
        "missing_expected_services": [],
        "unhealthy_services": [],
    }

    missing_evidence = sorted(
        name for name, source in sources.items() if source["status"] == "evidence_missing" and source.get("required")
    )
    invalid_evidence = sorted(
        name for name, source in sources.items() if source["status"] in {"invalid_schema", "blocked"} and source.get("required")
    )

    safety = safety_payload()
    observed_soak_days = observed_soak_days_from_source(sources.get("paper_soak_report", {}))
    blocking_reasons, warning_reasons = classify_readiness(
        sources=sources,
        runtime_sources=runtime_sources,
        observed_soak_days=observed_soak_days,
        safety=safety,
    )

    diagnostic_soak_reached = observed_soak_days >= DIAGNOSTIC_SOAK_DAYS
    readiness_soak_reached = observed_soak_days >= REQUIRED_SOAK_DAYS
    evidence_sources = {name: public_source_summary(source) for name, source in sorted(sources.items())}
    runtime_observability = runtime_observability_rollup(runtime_sources)
    runtime_source_summaries = {name: public_runtime_source_summary(source) for name, source in sorted(runtime_sources.items())}

    evidence_status = evidence_pack_status(missing_evidence, invalid_evidence, blocking_reasons)
    snapshot_status = readiness_status(blocking_reasons, missing_evidence, invalid_evidence, warning_reasons)

    evidence_pack = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(root),
        "status": evidence_status,
        "reason": evidence_reason(evidence_status, missing_evidence, invalid_evidence, blocking_reasons),
        "missing_evidence": missing_evidence,
        "invalid_evidence": invalid_evidence,
        "required_evidence": sorted(REQUIRED_EVIDENCE),
        "evidence_sources": evidence_sources,
        "runtime_observability": runtime_observability,
        "runtime_sources": runtime_source_summaries,
        "container_snapshot": container_snapshot,
        "compose_service_catalog": service_catalog,
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "deterministic_json": True,
        "output_paths": {
            "runtime_evidence_pack_v2": str(evidence_pack_path),
            "readiness_snapshot_v2": str(readiness_snapshot_path),
        },
        **safety,
    }

    readiness_snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(root),
        "status": snapshot_status,
        "reason": snapshot_reason(snapshot_status, blocking_reasons, missing_evidence, invalid_evidence, warning_reasons),
        "readiness_approved": False,
        "live_release_allowed": False,
        "live_canary_eligible": False,
        "diagnostic_soak_days": DIAGNOSTIC_SOAK_DAYS,
        "required_soak_days": REQUIRED_SOAK_DAYS,
        "observed_soak_days": observed_soak_days,
        "diagnostic_soak_reached": diagnostic_soak_reached,
        "readiness_soak_reached": readiness_soak_reached,
        "missing_evidence": missing_evidence,
        "invalid_evidence": invalid_evidence,
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "next_required_actions": next_required_actions(blocking_reasons, missing_evidence, invalid_evidence, warning_reasons),
        "evidence_sources": evidence_sources,
        "runtime_observability": runtime_observability,
        "runtime_sources": runtime_source_summaries,
        "container_snapshot": container_snapshot,
        "safety_flags": safety,
        **safety,
    }

    if not no_write:
        write_json(evidence_pack_path, evidence_pack)
        write_json(readiness_snapshot_path, readiness_snapshot)

    return BuildResult(
        evidence_pack=evidence_pack,
        readiness_snapshot=readiness_snapshot,
        evidence_pack_path=evidence_pack_path,
        readiness_snapshot_path=readiness_snapshot_path,
        write_performed=not no_write,
    )


def collect_evidence_sources(root: Path) -> dict[str, dict[str, Any]]:
    sources = {
        name: load_json_evidence(root / relative_path, required=name in REQUIRED_EVIDENCE)
        for name, relative_path in EVIDENCE_PATHS.items()
    }
    sources["manifest_check"] = collect_manifest_check(root)
    sources["secret_scan"] = collect_secret_scan(root)
    return sources


def collect_runtime_observability_sources(root: Path, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    current_time = now or datetime.now(timezone.utc)
    sources: dict[str, dict[str, Any]] = {}

    for name, relative_path in RUNTIME_OBSERVABILITY_PATHS.items():
        source = load_json_evidence(root / relative_path, required=False)
        payload = source.get("payload") if isinstance(source.get("payload"), Mapping) else {}
        if source.get("exists") and isinstance(payload, Mapping):
            component_summary = summarize_runtime_component(name, payload, now=current_time)
            source["component_summary"] = component_summary
            if component_summary["status"] == "blocked":
                source["runtime_status"] = "blocked"
            elif component_summary["status"] == "degraded":
                source["runtime_status"] = "degraded"
            else:
                source["runtime_status"] = "ok"
        elif source.get("status") == "evidence_missing":
            if name in OPTIONAL_MISSING_NEUTRAL_RUNTIME_REPORTS:
                source["component_summary"] = {
                    "status": "neutral",
                    "reason": "optional_runtime_report_missing_neutral",
                    "alerts": [],
                    "metrics": {},
                }
                source["runtime_status"] = "neutral"
            else:
                source["component_summary"] = {
                    "status": "missing",
                    "reason": "optional_runtime_report_missing",
                    "alerts": ["optional_runtime_report_missing"],
                    "metrics": {},
                }
                source["runtime_status"] = "missing"
        else:
            source["component_summary"] = {
                "status": "blocked",
                "reason": str(source.get("reason") or "invalid_runtime_report"),
                "alerts": [str(source.get("reason") or "invalid_runtime_report")],
                "metrics": {},
            }
            source["runtime_status"] = "blocked"
        sources[name] = source

    return sources

def summarize_runtime_component(name: str, payload: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    alerts: list[str] = []
    metrics: dict[str, Any] = {
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "created_at": payload.get("created_at"),
    }

    source_status = str(payload.get("status", "ok")).lower()
    if source_status in {"blocked", "critical", "failed"}:
        alerts.append("component_status_blocked")
    elif source_status not in {"ok", "ready", "degraded", "warning"}:
        alerts.append("component_status_unknown")

    safety_alerts = [f"unsafe_flag:{flag}" for flag in unsafe_safety_flags(payload)]
    alerts.extend(safety_alerts)

    age_seconds = runtime_report_age_seconds(payload, now=now)
    metrics["age_seconds"] = age_seconds
    stale_after = RUNTIME_STALE_AFTER_SECONDS.get(name)
    if stale_after is not None and age_seconds is not None and age_seconds > stale_after:
        alerts.append("report_stale")

    if name == "trade_event_notifications_report":
        metrics.update(
            {
                "daemon": payload.get("daemon"),
                "daemon_iteration": int_value(payload.get("daemon_iteration")),
                "dry_run": payload.get("dry_run"),
                "channels": payload.get("channels"),
                "events_detected": int_value(payload.get("events_detected")),
                "events_pending": int_value(payload.get("events_pending")),
                "events_dispatched": int_value(payload.get("events_dispatched")),
                "events_marked_sent": int_value(payload.get("events_marked_sent")),
            }
        )
        if payload.get("daemon") is not True:
            alerts.append("daemon_not_true")
        if payload.get("dry_run") is not False:
            alerts.append("dry_run_not_false")
        if str(payload.get("channels") or "").lower() != "all":
            alerts.append("channels_not_all")
        if int_value(payload.get("events_pending")) > 0:
            alerts.append("events_pending_positive")

    if name.startswith("qlib_") or name.startswith("phase13_"):
        metrics.update(
            {
                "input_data_status": payload.get("input_data_status"),
                "signals_after": payload.get("signals_after"),
                "written_pinned": payload.get("written_pinned"),
                "market_features_age_minutes": payload.get("market_features_age_minutes"),
            }
        )

    if name.startswith("phase14_"):
        metrics.update(
            {
                "processed": payload.get("processed"),
                "open_positions": payload.get("open_positions"),
                "closed_feedback": payload.get("closed_feedback"),
                "rows": payload.get("rows"),
            }
        )

    if safety_alerts:
        status = "blocked"
    elif any(alert in alerts for alert in ("component_status_blocked",)):
        status = "blocked"
    elif alerts:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "reason": ";".join(sorted(set(alerts)) or ["ok"]),
        "alerts": sorted(set(alerts)),
        "metrics": metrics,
    }


def runtime_report_age_seconds(payload: Mapping[str, Any], *, now: datetime) -> float | None:
    raw = first_present(payload, "created_at", "generated_at", "timestamp", "updated_at")
    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    return round(max(0.0, (now - parsed).total_seconds()), 3)


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def runtime_observability_rollup(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [str(source.get("runtime_status") or source.get("status") or "unknown") for source in sources.values()]
    blocked = sorted(name for name, source in sources.items() if source.get("runtime_status") == "blocked")
    degraded = sorted(name for name, source in sources.items() if source.get("runtime_status") == "degraded")
    missing = sorted(name for name, source in sources.items() if source.get("runtime_status") == "missing")
    neutral = sorted(name for name, source in sources.items() if source.get("runtime_status") == "neutral")
    ok = sorted(name for name, source in sources.items() if source.get("runtime_status") == "ok")

    if blocked:
        status = "blocked"
    elif degraded:
        status = "degraded"
    elif missing:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "reason": ";".join(blocked or degraded or missing or ["ok"]),
        "total_sources": len(statuses),
        "ok_sources": ok,
        "blocked_sources": blocked,
        "degraded_sources": degraded,
        "missing_optional_sources": missing,
        "neutral_optional_sources": neutral,
    }

def collect_compose_service_catalog(root: Path) -> dict[str, Any]:
    compose_path = root / "docker-compose.paper.yml"
    if not compose_path.exists():
        return {
            "status": "missing",
            "reason": "docker_compose_paper_missing",
            "path": str(compose_path),
            "services": [],
            "expected_services": list(EXPECTED_CONTAINER_SERVICES),
            "missing_expected_services": list(EXPECTED_CONTAINER_SERVICES),
        }

    payload = compose_path.read_text(encoding="utf-8")
    service_block = payload
    if "\nservices:" in payload:
        service_block = payload.split("\nservices:", 1)[1]
    if "\nvolumes:" in service_block:
        service_block = service_block.split("\nvolumes:", 1)[0]

    services = sorted(set(re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", service_block)))
    missing = sorted(set(EXPECTED_CONTAINER_SERVICES) - set(services))

    return {
        "status": "ok" if not missing else "degraded",
        "reason": "ok" if not missing else "missing_expected_services:" + ",".join(missing),
        "path": str(compose_path),
        "services": services,
        "expected_services": list(EXPECTED_CONTAINER_SERVICES),
        "missing_expected_services": missing,
    }


def collect_docker_container_snapshot(*, timeout_seconds: float = 3.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": f"docker_ps_unavailable:{type(exc).__name__}",
            "error": str(exc),
            "containers": [],
            "expected_services": list(EXPECTED_CONTAINER_SERVICES),
            "missing_expected_services": [],
            "unhealthy_services": [],
        }

    if result.returncode != 0:
        return {
            "status": "unavailable",
            "reason": "docker_ps_failed",
            "stderr": result.stderr[-500:],
            "containers": [],
            "expected_services": list(EXPECTED_CONTAINER_SERVICES),
            "missing_expected_services": [],
            "unhealthy_services": [],
        }

    containers: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            containers.append(
                {
                    "name": row.get("Names"),
                    "image": row.get("Image"),
                    "status": row.get("Status"),
                    "command": row.get("Command"),
                }
            )

    names = {str(item.get("name") or "") for item in containers}
    missing = sorted(
        service for service in EXPECTED_CONTAINER_SERVICES
        if not any(service in name for name in names)
    )
    unhealthy = sorted(
        str(item.get("name"))
        for item in containers
        if "unhealthy" in str(item.get("status") or "").lower()
    )

    status = "ok"
    reason = "ok"
    if unhealthy:
        status = "blocked"
        reason = "unhealthy_containers:" + ",".join(unhealthy)
    elif missing:
        status = "degraded"
        reason = "missing_expected_containers:" + ",".join(missing)

    return {
        "status": status,
        "reason": reason,
        "containers": containers,
        "expected_services": list(EXPECTED_CONTAINER_SERVICES),
        "missing_expected_services": missing,
        "unhealthy_services": unhealthy,
    }


def load_json_evidence(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "evidence_missing",
            "reason": "evidence_missing",
            "path": str(path),
            "exists": False,
            "required": required,
            "payload": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "invalid_schema",
            "reason": "invalid_json",
            "error": str(exc),
            "path": str(path),
            "exists": True,
            "required": required,
            "payload": {},
            "last_modified_utc": file_mtime_iso(path),
        }
    if not isinstance(payload, dict):
        return {
            "status": "invalid_schema",
            "reason": "json_payload_not_object",
            "path": str(path),
            "exists": True,
            "required": required,
            "payload": {},
            "last_modified_utc": file_mtime_iso(path),
        }
    source_status = str(payload.get("status", "ok")).lower()
    status = "blocked" if source_status in {"blocked", "failed", "critical"} else "ok"
    return {
        "status": status,
        "reason": f"source_status_{source_status}" if status == "blocked" else "evidence_loaded",
        "path": str(path),
        "exists": True,
        "required": required,
        "source_status": source_status,
        "payload": payload,
        "last_modified_utc": file_mtime_iso(path),
    }


def collect_manifest_check(root: Path) -> dict[str, Any]:
    manifest_path = root / "PROJECT_MANIFEST_CLEAN.json"
    if not manifest_path.exists():
        return generated_source("manifest_check", "evidence_missing", "manifest_missing", manifest_path, required=True)
    try:
        module = load_local_script_module("generate_project_manifest.py")
        expected = module.build_manifest(root)
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return generated_source("manifest_check", "blocked", "manifest_check_failed", manifest_path, required=True, error=str(exc))
    if current != expected:
        return generated_source("manifest_check", "blocked", "manifest_outdated", manifest_path, required=True)
    return generated_source(
        "manifest_check",
        "ok",
        "manifest_current",
        manifest_path,
        required=True,
        payload={"status": "ok", "reason": "manifest_current", "aggregate_sha256": current.get("aggregate_sha256")},
    )


def collect_secret_scan(root: Path) -> dict[str, Any]:
    try:
        module = load_local_script_module("scan_versioned_secrets.py")
        report = module.run_secret_scan(root)
    except Exception as exc:
        return generated_source("secret_scan", "blocked", "secret_scan_failed", root, required=True, error=str(exc))
    status = str(report.get("status", "blocked")).lower()
    if status != "ok":
        return generated_source("secret_scan", "blocked", str(report.get("reason", "secret_scan_failed")), root, required=True, payload=report)
    return generated_source("secret_scan", "ok", str(report.get("reason", "no_versioned_secrets_detected")), root, required=True, payload=report)


def generated_source(
    name: str,
    status: str,
    reason: str,
    path: Path,
    *,
    required: bool,
    payload: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    source = {
        "status": status,
        "reason": reason,
        "path": str(path),
        "exists": status != "evidence_missing",
        "required": required,
        "generated": True,
        "payload": dict(payload or {}),
    }
    if error:
        source["error"] = error
    if name:
        source["name"] = name
    return source


def load_local_script_module(script_name: str) -> Any:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"runtime_evidence_{script_name.replace('.', '_')}", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_script:{script_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify_readiness(
    *,
    sources: Mapping[str, Mapping[str, Any]],
    runtime_sources: Mapping[str, Mapping[str, Any]] | None = None,
    observed_soak_days: float,
    safety: Mapping[str, bool],
) -> tuple[list[str], list[str]]:
    runtime_sources = runtime_sources or {}
    blockers: list[str] = []
    warnings: list[str] = []

    for name in sorted(REQUIRED_EVIDENCE):
        source = sources.get(name, {})
        if source.get("status") == "evidence_missing":
            blockers.append(f"missing_required_evidence:{name}")
        elif source.get("status") in {"blocked", "invalid_schema"}:
            blockers.append(f"{name}_{source.get('reason', 'blocked')}")

    if any(source.get("status") == "evidence_missing" for name, source in sources.items() if name not in REQUIRED_EVIDENCE):
        warnings.append("optional_evidence_missing")

    runtime_rollup = runtime_observability_rollup(runtime_sources)
    if runtime_rollup.get("status") == "blocked":
        blockers.append("runtime_observability_blocked")
    elif runtime_rollup.get("status") == "degraded":
        warnings.append("runtime_observability_degraded")

    if observed_soak_days < REQUIRED_SOAK_DAYS:
        blockers.append("soak_days_below_required")

    monte_carlo = sources.get("monte_carlo_report", {}).get("payload", {})
    if sources.get("monte_carlo_report", {}).get("exists"):
        if isinstance(monte_carlo, Mapping) and status_is_blocked(monte_carlo):
            blockers.append("monte_carlo_blocked")
        if isinstance(monte_carlo, Mapping) and no_trade_policy_active(monte_carlo):
            blockers.append("monte_carlo_no_trade_policy_active")

    readiness = sources.get("readiness_gate_report", {}).get("payload", {})
    if sources.get("readiness_gate_report", {}).get("exists"):
        if isinstance(readiness, Mapping) and (status_is_blocked(readiness) or readiness.get("readiness_approved") is False):
            blockers.append("readiness_gate_blocked")

    unsafe = unsafe_safety_flags(safety)
    unsafe.extend(source_unsafe_flags(sources))
    unsafe.extend(source_unsafe_flags(runtime_sources))
    blockers.extend(f"unsafe_safety_flag:{flag}" for flag in sorted(set(unsafe)))

    if p0_p1_live_blocking_declared(sources) or p0_p1_live_blocking_declared(runtime_sources):
        blockers.append("p0_p1_live_blocking_declared")

    if any(
        sources.get(name, {}).get("status") in {"evidence_missing", "blocked", "invalid_schema"}
        for name in REQUIRED_EVIDENCE
    ):
        blockers.append("evidence_pack_incomplete_required_gates")

    return sorted(set(blockers)), sorted(set(warnings))


def public_source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = source.get("payload", {})
    summary = {
        "status": source.get("status"),
        "reason": source.get("reason"),
        "path": source.get("path"),
        "exists": bool(source.get("exists")),
        "required": bool(source.get("required")),
        "source_status": source.get("source_status") or (payload.get("status") if isinstance(payload, Mapping) else None),
        "last_modified_utc": source.get("last_modified_utc"),
    }
    if source.get("error"):
        summary["error"] = source.get("error")
    return summary


def public_runtime_source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    base = public_source_summary(source)
    component = source.get("component_summary") if isinstance(source.get("component_summary"), Mapping) else {}
    base.update(
        {
            "runtime_status": source.get("runtime_status"),
            "component_status": component.get("status"),
            "component_reason": component.get("reason"),
            "alerts": component.get("alerts", []),
            "metrics": component.get("metrics", {}),
        }
    )
    return base


def evidence_pack_status(missing: list[str], invalid: list[str], blockers: list[str]) -> str:
    if any(item.startswith("unsafe_safety_flag") for item in blockers):
        return "blocked"
    if invalid:
        return "blocked"
    if missing:
        return "evidence_missing"
    return "ready" if not blockers else "blocked"


def readiness_status(blockers: list[str], missing: list[str], invalid: list[str], warnings: list[str]) -> str:
    if blockers:
        return "blocked"
    if missing or invalid:
        return "evidence_missing"
    if warnings:
        return "degraded"
    return "ready"


def evidence_reason(status: str, missing: list[str], invalid: list[str], blockers: list[str]) -> str:
    if status == "ready":
        return "runtime_evidence_pack_complete"
    if invalid:
        return "invalid_evidence:" + ",".join(invalid)
    if missing:
        return "missing_evidence:" + ",".join(missing)
    return ";".join(blockers or [status])


def snapshot_reason(
    status: str,
    blockers: list[str],
    missing: list[str],
    invalid: list[str],
    warnings: list[str],
) -> str:
    if status == "ready":
        return "readiness_snapshot_ready"
    if blockers:
        return ";".join(blockers)
    if invalid:
        return "invalid_evidence:" + ",".join(invalid)
    if missing:
        return "missing_evidence:" + ",".join(missing)
    return ";".join(warnings or [status])


def next_required_actions(
    blockers: list[str],
    missing: list[str],
    invalid: list[str],
    warnings: list[str],
) -> list[str]:
    actions = ["keep_live_disabled", "continue_paper_shadow_only"]
    if missing or invalid or any("evidence" in reason for reason in blockers):
        actions.append("refresh_missing_or_invalid_runtime_evidence")
    if any("soak_days_below_required" in reason for reason in blockers):
        actions.append("continue_paper_shadow_soak_until_30_days")
    if any("monte_carlo" in reason for reason in blockers):
        actions.append("resolve_monte_carlo_no_trade_or_blocking_policy")
    if any("readiness_gate" in reason for reason in blockers):
        actions.append("rerun_readiness_gate_after_evidence_refresh")
    if any("runtime_observability" in reason for reason in blockers + warnings):
        actions.append("inspect_runtime_services_and_reports")
    if warnings and not blockers:
        actions.append("review_degraded_optional_evidence")
    return sorted(set(actions))


def observed_soak_days_from_source(source: Mapping[str, Any]) -> float:
    payload = source.get("payload")
    if not isinstance(payload, Mapping):
        return 0.0
    return float_value(first_present(payload, "observed_soak_days", "soak_days", "paper_days", default=0.0))


def safety_payload() -> dict[str, bool]:
    return {
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
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "writes_official_trades_master": False,
        "changes_model": False,
        "changes_config": False,
        "changes_risk_config": False,
    }


def unsafe_safety_flags(payload: Mapping[str, Any]) -> list[str]:
    unsafe: list[str] = []

    for flag in SAFE_TRUE_FLAGS:
        if flag in payload and payload.get(flag) is not True:
            unsafe.append(flag)

    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)

    return unsafe


def source_unsafe_flags(sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    unsafe: list[str] = []
    for name, source in sources.items():
        payload = source.get("payload", {})
        if isinstance(payload, Mapping):
            unsafe.extend(f"{name}:{flag}" for flag in unsafe_safety_flags(payload))
    return unsafe


def p0_p1_live_blocking_declared(sources: Mapping[str, Mapping[str, Any]]) -> bool:
    for source in sources.values():
        payload = source.get("payload", {})
        if not isinstance(payload, Mapping):
            continue
        if int_value(payload.get("p0_incidents")) > 0 or int_value(payload.get("p1_incidents")) > 0:
            return True
        findings = payload.get("findings") or payload.get("critical_findings") or payload.get("incidents") or []
        if isinstance(findings, list) and any(is_p0_p1_live_blocking(item) for item in findings):
            return True
    return False


def is_p0_p1_live_blocking(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    severity = str(item.get("severity") or item.get("priority") or "").upper()
    live_blocking = item.get("live_blocking") is True or item.get("blocks_live") is True
    return severity in {"P0", "P1"} and live_blocking


def no_trade_policy_active(payload: Mapping[str, Any]) -> bool:
    policy_action = str(payload.get("policy_action", "")).lower()
    blockers = payload.get("readiness_blockers", [])
    if not isinstance(blockers, list):
        blockers = [blockers]
    return (
        payload.get("no_trade_policy_present") is True
        or policy_action == "no_trade"
        or "monte_carlo_no_trade_policy_active" in blockers
        or "no_trade_policy_active" in blockers
    )


def status_is_blocked(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status", "")).lower() in {"blocked", "critical", "failed"}


def resolve_under_root(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iso(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def first_present(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return default


def int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def float_value(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0
