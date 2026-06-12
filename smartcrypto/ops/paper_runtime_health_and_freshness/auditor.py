from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    CRITICAL_PAPER_SERVICES,
    DASHBOARD_NAME,
    DEFAULT_OUTPUT_PATH,
    EXPECTED_PAPER_SERVICES,
    OPTIONAL_PAPER_SERVICES,
    PROJECT_NAME,
    RUNTIME_REPORTS,
    SAFE_FALSE_FLAGS,
    SAFE_TRUE_FLAGS,
    SCHEMA_VERSION,
    SERVICE_COMPONENTS,
    RuntimeReportContract,
)


def audit_paper_runtime_health_and_freshness(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    write: bool = False,
    collect_containers: bool = False,
    include_containers: bool | None = None,
    container_timeout_seconds: float = 3.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    current_time = ensure_aware_utc(now or datetime.now(timezone.utc))
    output_path = resolve_under_root(root, output)
    collection_requested = bool(collect_containers or include_containers is True)

    report_sources = [inspect_runtime_report(root, contract, now=current_time) for contract in RUNTIME_REPORTS]
    compose_catalog = collect_compose_service_catalog(root)
    container_snapshot = (
        collect_docker_container_snapshot(
            project_root=root,
            timeout_seconds=container_timeout_seconds,
        )
        if collection_requested
        else disabled_container_snapshot()
    )

    missing_required = sorted(source["name"] for source in report_sources if source["required"] and not source["exists"])
    stale_required = sorted(source["name"] for source in report_sources if source["required"] and source["freshness_status"] == "stale")
    stale_optional = sorted(source["name"] for source in report_sources if not source["required"] and source["freshness_status"] == "stale")
    invalid_sources = sorted(source["name"] for source in report_sources if source["payload_status"] == "invalid")
    unsafe_sources = sorted(
        f"{source['name']}:{flag}"
        for source in report_sources
        for flag in source.get("unsafe_safety_flags", [])
    )

    component_rollup = build_component_rollup(
        report_sources,
        container_snapshot=container_snapshot,
        compose_catalog=compose_catalog,
    )
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []

    if missing_required:
        blocking_reasons.append("missing_required_runtime_reports:" + ",".join(missing_required))
    if stale_required:
        blocking_reasons.append("stale_required_runtime_reports:" + ",".join(stale_required))
    if invalid_sources:
        blocking_reasons.append("invalid_runtime_reports:" + ",".join(invalid_sources))
    if unsafe_sources:
        blocking_reasons.append("unsafe_runtime_safety_flags:" + ",".join(unsafe_sources))
    if not collection_requested:
        blocking_reasons.append("container_collection_not_requested")
    if container_snapshot["status"] == "blocked":
        blocking_reasons.append("container_snapshot_blocked")
    if component_rollup["critical_component_count"] > 0:
        blocking_reasons.append("critical_runtime_components_present")

    if stale_optional:
        warning_reasons.append("stale_optional_runtime_reports:" + ",".join(stale_optional))
    if compose_catalog["status"] == "degraded":
        warning_reasons.append("compose_service_catalog_degraded")
    if container_snapshot["status"] in {"degraded", "unknown", "unavailable"}:
        warning_reasons.append("container_snapshot_" + container_snapshot["status"])
    if component_rollup["warning_component_count"] > 0:
        warning_reasons.append("warning_runtime_components_present")

    if blocking_reasons:
        status = "blocked"
        reason = ";".join(sorted(set(blocking_reasons)))
    elif warning_reasons:
        status = "degraded"
        reason = ";".join(sorted(set(warning_reasons)))
    else:
        status = "ok"
        reason = "paper_runtime_health_and_freshness_current"

    critical_stale_count = len(stale_required)
    warning_stale_count = len(stale_optional)
    paper_runtime_fresh = not missing_required and not stale_required and not invalid_sources
    service_statuses = container_snapshot.get("service_statuses", {})
    paper_runtime_alive = collection_requested and all(
        isinstance(service_statuses.get(service), Mapping)
        and service_statuses[service].get("status") == "ok"
        for service in CRITICAL_PAPER_SERVICES
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "dashboard_name": DASHBOARD_NAME,
        "generated_at_utc": iso(current_time),
        "status": status,
        "reason": reason,
        "paper_runtime_alive": paper_runtime_alive,
        "paper_runtime_fresh": paper_runtime_fresh,
        "paper_runtime_health_status": status,
        "paper_runtime_freshness_status": "fresh" if paper_runtime_fresh else "stale_or_missing",
        "container_collection_requested": collection_requested,
        "required_services": list(EXPECTED_PAPER_SERVICES),
        "critical_services": list(CRITICAL_PAPER_SERVICES),
        "optional_services": list(OPTIONAL_PAPER_SERVICES),
        "compose_service_catalog": compose_catalog,
        "docker_services_status": container_snapshot["status"],
        "container_snapshot_status": container_snapshot["status"],
        "container_snapshot": container_snapshot,
        "container_service_statuses": service_statuses,
        "runtime_reports": report_sources,
        "component_rollup": component_rollup,
        "freqtrade_paper_status": _service_status(service_statuses, "freqtrade-paper"),
        "smartcrypto_bot_status": _service_status(service_statuses, "smartcrypto-bot-paper"),
        "phase14_feedback_sync_status": _service_status(service_statuses, "phase14-feedback-sync-paper"),
        "qlib_refresh_status": _service_status(service_statuses, "qlib-refresh-supervisor-paper"),
        "dashboard_status": _service_status(service_statuses, "smartcrypto-dashboard-paper"),
        "notifications_status": _service_status(service_statuses, "trade-event-notifications-paper"),
        "missing_required_sources": missing_required,
        "stale_required_sources": stale_required,
        "stale_optional_sources": stale_optional,
        "invalid_sources": invalid_sources,
        "stale_sources": sorted(stale_required + stale_optional),
        "critical_stale_count": critical_stale_count,
        "warning_stale_count": warning_stale_count,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": next_required_actions(
            missing_required=missing_required,
            stale_required=stale_required,
            stale_optional=stale_optional,
            invalid_sources=invalid_sources,
            status=status,
        ),
        "output_path": str(output_path),
        "write_performed": bool(write),
        "report_materialized": bool(write and output_path.exists()),
        "safety": safety_payload(),
        **safety_payload(),
    }

    if write:
        write_json(output_path, report)
        report["report_materialized"] = output_path.exists()

    return report


def inspect_runtime_report(root: Path, contract: RuntimeReportContract, *, now: datetime) -> dict[str, Any]:
    path = root / contract.path
    source: dict[str, Any] = {
        "name": contract.name,
        "path": contract.path,
        "component": contract.component,
        "required": contract.required,
        "exists": path.exists(),
        "max_age_seconds": contract.max_age_seconds,
        "payload_status": "missing" if not path.exists() else "unknown",
        "source_status": None,
        "timestamp_utc": None,
        "age_seconds": None,
        "freshness_status": "missing" if not path.exists() else "unknown",
        "reason": None,
        "unsafe_safety_flags": [],
    }
    if not path.exists():
        source["reason"] = "runtime_report_missing"
        return source

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except json.JSONDecodeError as exc:
        source.update({"payload_status": "invalid", "freshness_status": "invalid", "reason": f"invalid_json:{exc}"})
        return source
    if not isinstance(payload, Mapping):
        source.update({"payload_status": "invalid", "freshness_status": "invalid", "reason": "json_payload_not_object"})
        return source

    source_status = str(payload.get("status") or "ok").lower()
    timestamp = first_timestamp(payload, contract.timestamp_keys)
    age = age_seconds(now, timestamp)
    unsafe_flags = unsafe_safety_flags(payload)
    freshness_status = "fresh"
    reason = "fresh"
    if age is None:
        freshness_status = "unknown"
        reason = "timestamp_missing_or_invalid"
    elif age > float(contract.max_age_seconds):
        freshness_status = "stale"
        reason = f"age_seconds_exceeds_max:{age}>{contract.max_age_seconds}"

    if source_status in {"blocked", "failed", "critical", "error"}:
        freshness_status = "blocked" if freshness_status != "stale" else "stale"
        reason = f"source_status_{source_status}"

    source.update(
        {
            "payload_status": "ok",
            "source_status": source_status,
            "timestamp_utc": iso(timestamp) if timestamp else None,
            "age_seconds": age,
            "freshness_status": freshness_status,
            "reason": reason,
            "unsafe_safety_flags": unsafe_flags,
            "last_modified_utc": file_mtime_iso(path),
        }
    )
    return source


def build_component_rollup(
    report_sources: list[dict[str, Any]],
    *,
    container_snapshot: Mapping[str, Any],
    compose_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in report_sources:
        grouped[str(source["component"])].append(source)

    components: dict[str, dict[str, Any]] = {}
    for component, sources in sorted(grouped.items()):
        required = [source for source in sources if source["required"]]
        missing = [source["name"] for source in sources if source["required"] and not source["exists"]]
        stale = [source["name"] for source in sources if source["required"] and source["freshness_status"] == "stale"]
        invalid = [source["name"] for source in sources if source["payload_status"] == "invalid"]
        blocked = [source["name"] for source in sources if source["source_status"] in {"blocked", "failed", "critical", "error"}]
        warning = [source["name"] for source in sources if source["freshness_status"] in {"unknown"}]
        if missing or stale or invalid or blocked:
            status = "blocked"
            reason = ";".join(missing + stale + invalid + blocked)
        elif warning:
            status = "degraded"
            reason = ";".join(warning)
        else:
            status = "ok" if required or sources else "unknown"
            reason = "ok"
        components[component] = {
            "status": status,
            "reason": reason,
            "required_source_count": len(required),
            "source_count": len(sources),
            "missing_required_sources": missing,
            "stale_required_sources": stale,
            "invalid_sources": invalid,
            "blocked_sources": blocked,
        }

    collection_requested = container_snapshot.get("collection_requested") is True
    for service, component_name in SERVICE_COMPONENTS.items():
        service_component = infer_service_component_status(service, container_snapshot, compose_catalog)
        existing = components.get(component_name)
        if existing is None:
            components[component_name] = service_component
        elif collection_requested:
            components[component_name] = merge_component_status(existing, service_component)
        else:
            existing["container_status"] = service_component["status"]
            existing["container_reason"] = service_component["reason"]

    critical_count = sum(1 for component in components.values() if component["status"] == "blocked")
    warning_count = sum(1 for component in components.values() if component["status"] == "degraded")
    if critical_count > 0:
        status = "blocked"
    elif warning_count > 0:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "reason": "ok" if status == "ok" else "runtime_components_not_all_ok",
        "component_count": len(components),
        "critical_component_count": critical_count,
        "warning_component_count": warning_count,
        "components": components,
    }


def infer_service_component_status(service: str, container_snapshot: Mapping[str, Any], compose_catalog: Mapping[str, Any]) -> dict[str, Any]:
    if service in set(compose_catalog.get("missing_expected_services") or []):
        return {"status": "degraded", "reason": "service_missing_from_compose"}
    statuses = container_snapshot.get("service_statuses")
    if isinstance(statuses, Mapping) and isinstance(statuses.get(service), Mapping):
        service_status = dict(statuses[service])
        return {
            **service_status,
            "container_status": service_status.get("status", "unknown"),
            "container_reason": service_status.get("reason", "container_status_unknown"),
        }
    return {
        "status": "unknown",
        "reason": str(container_snapshot.get("reason") or "container_collection_disabled"),
    }


def merge_component_status(
    evidence_component: Mapping[str, Any],
    service_component: Mapping[str, Any],
) -> dict[str, Any]:
    rank = {"unknown": 0, "ok": 1, "degraded": 2, "blocked": 3}
    evidence_status = str(evidence_component.get("status") or "unknown")
    service_status = str(service_component.get("status") or "unknown")
    status = evidence_status if rank.get(evidence_status, 0) >= rank.get(service_status, 0) else service_status
    return {
        **dict(evidence_component),
        "status": status,
        "reason": (
            str(evidence_component.get("reason") or "unknown")
            if status == evidence_status
            else str(service_component.get("reason") or "unknown")
        ),
        "container_status": service_status,
        "container_reason": str(service_component.get("reason") or "unknown"),
        "container": service_component.get("container"),
    }


def collect_compose_service_catalog(root: Path) -> dict[str, Any]:
    compose_path = root / "docker-compose.paper.yml"
    if not compose_path.exists():
        return {
            "status": "missing",
            "reason": "docker_compose_paper_missing",
            "path": str(compose_path),
            "services": [],
            "expected_services": list(EXPECTED_PAPER_SERVICES),
            "missing_expected_services": list(EXPECTED_PAPER_SERVICES),
        }
    text = compose_path.read_text(encoding="utf-8")
    block = text.split("\nservices:", 1)[1] if "\nservices:" in text else text
    block = block.split("\nvolumes:", 1)[0]
    services = sorted({line.split(":", 1)[0].strip() for line in block.splitlines() if line.startswith("  ") and line.strip().endswith(":")})
    missing = sorted(set(EXPECTED_PAPER_SERVICES) - set(services))
    return {
        "status": "ok" if not missing else "degraded",
        "reason": "ok" if not missing else "missing_expected_services:" + ",".join(missing),
        "path": str(compose_path),
        "services": services,
        "expected_services": list(EXPECTED_PAPER_SERVICES),
        "missing_expected_services": missing,
    }


def disabled_container_snapshot() -> dict[str, Any]:
    return {
        "status": "disabled",
        "reason": "container_collection_not_requested",
        "collection_requested": False,
        "containers": [],
        "expected_services": list(EXPECTED_PAPER_SERVICES),
        "critical_services": list(CRITICAL_PAPER_SERVICES),
        "optional_services": list(OPTIONAL_PAPER_SERVICES),
        "missing_expected_services": [],
        "unhealthy_services": [],
        "non_running_services": [],
        "service_statuses": {
            service: {"status": "unknown", "reason": "container_collection_not_requested"}
            for service in EXPECTED_PAPER_SERVICES
        },
    }


def collect_docker_container_snapshot(
    *,
    project_root: str | Path = ".",
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    compose_path = root / "docker-compose.paper.yml"
    if not compose_path.exists():
        return unavailable_container_snapshot("docker_compose_paper_missing", status="unknown")
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "ps", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=root,
        )
    except (FileNotFoundError, OSError) as exc:
        return unavailable_container_snapshot("docker_unavailable", error=exc)
    except subprocess.TimeoutExpired as exc:
        return unavailable_container_snapshot("docker_compose_ps_timeout", status="degraded", error=exc)
    if result.returncode != 0:
        return unavailable_container_snapshot(
            "docker_compose_ps_failed",
            status="degraded",
            stderr=result.stderr,
        )

    containers = parse_compose_ps_output(result.stdout)
    if result.stdout.strip() and not containers:
        return unavailable_container_snapshot("docker_compose_ps_invalid_json", status="degraded")
    service_statuses = build_service_statuses(containers)
    missing = sorted(
        service for service, payload in service_statuses.items() if payload["reason"] == "container_missing"
    )
    unhealthy = sorted(
        service for service, payload in service_statuses.items() if payload["reason"] == "container_unhealthy"
    )
    non_running = sorted(
        service for service, payload in service_statuses.items() if payload["status"] != "ok"
    )
    critical_failures = sorted(set(non_running) & set(CRITICAL_PAPER_SERVICES))
    optional_failures = sorted(set(non_running) & set(OPTIONAL_PAPER_SERVICES))
    if critical_failures:
        status = "blocked"
        reason = "critical_services_not_running:" + ",".join(critical_failures)
    elif optional_failures:
        status = "degraded"
        reason = "optional_services_not_running:" + ",".join(optional_failures)
    else:
        status = "ok"
        reason = "docker_compose_services_running"
    return {
        "status": status,
        "reason": reason,
        "collection_requested": True,
        "collector": "docker_compose_ps",
        "compose_path": str(compose_path),
        "containers": containers,
        "expected_services": list(EXPECTED_PAPER_SERVICES),
        "critical_services": list(CRITICAL_PAPER_SERVICES),
        "optional_services": list(OPTIONAL_PAPER_SERVICES),
        "missing_expected_services": missing,
        "unhealthy_services": unhealthy,
        "non_running_services": non_running,
        "service_statuses": service_statuses,
    }


def unavailable_container_snapshot(
    reason: str,
    *,
    status: str = "unknown",
    error: BaseException | None = None,
    stderr: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "collection_requested": True,
        "collector": "docker_compose_ps",
        "containers": [],
        "expected_services": list(EXPECTED_PAPER_SERVICES),
        "critical_services": list(CRITICAL_PAPER_SERVICES),
        "optional_services": list(OPTIONAL_PAPER_SERVICES),
        "missing_expected_services": [],
        "unhealthy_services": [],
        "non_running_services": [],
        "service_statuses": {
            service: {"status": "unknown", "reason": reason}
            for service in EXPECTED_PAPER_SERVICES
        },
    }
    if error is not None:
        payload["error"] = f"{type(error).__name__}: {error}"
    if stderr:
        payload["stderr"] = stderr[-500:]
    return payload


def parse_compose_ps_output(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    rows: list[Any]
    try:
        decoded = json.loads(text)
        rows = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                return []
    containers = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        containers.append(
            {
                "service": str(row.get("Service") or row.get("service") or ""),
                "name": str(row.get("Name") or row.get("Names") or row.get("name") or ""),
                "image": str(row.get("Image") or row.get("image") or ""),
                "state": str(row.get("State") or row.get("state") or ""),
                "status": str(row.get("Status") or row.get("status") or ""),
                "health": str(row.get("Health") or row.get("health") or ""),
            }
        )
    return containers


def build_service_statuses(containers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for service in EXPECTED_PAPER_SERVICES:
        matches = [
            item
            for item in containers
            if item.get("service") == service or service in str(item.get("name") or "")
        ]
        if not matches:
            statuses[service] = {
                "status": "blocked" if service in CRITICAL_PAPER_SERVICES else "degraded",
                "reason": "container_missing",
            }
            continue
        classified = [classify_container(item) for item in matches]
        rank = {"ok": 0, "degraded": 1, "blocked": 2}
        selected = max(classified, key=lambda item: rank[item["status"]])
        statuses[service] = selected
    return statuses


def classify_container(container: Mapping[str, Any]) -> dict[str, Any]:
    state = str(container.get("state") or "").strip().lower()
    status_text = str(container.get("status") or "").strip().lower()
    health = str(container.get("health") or "").strip().lower()
    combined = " ".join((state, status_text, health))
    if "unhealthy" in combined:
        status, reason = "blocked", "container_unhealthy"
    elif any(token in combined for token in ("exited", "dead", "restarting")):
        status, reason = "blocked", "container_not_running"
    elif state == "running" or status_text.startswith("up"):
        status, reason = "ok", "container_running"
    else:
        status, reason = "degraded", "container_state_unknown"
    return {"status": status, "reason": reason, "container": dict(container)}


def _service_status(service_statuses: Any, service: str) -> str:
    if not isinstance(service_statuses, Mapping):
        return "unknown"
    payload = service_statuses.get(service)
    return str(payload.get("status") or "unknown") if isinstance(payload, Mapping) else "unknown"


def first_timestamp(payload: Mapping[str, Any], keys: tuple[str, ...]) -> datetime | None:
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key in keys:
                parsed = parse_datetime(item.get(key))
                if parsed is not None:
                    return parsed
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ensure_aware_utc(parsed)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_seconds(now: datetime, timestamp: datetime | None) -> float | None:
    if timestamp is None:
        return None
    return round(max(0.0, (now - timestamp).total_seconds()), 3)


def unsafe_safety_flags(payload: Mapping[str, Any]) -> list[str]:
    unsafe: list[str] = []
    for flag in SAFE_TRUE_FLAGS:
        if flag in payload and payload.get(flag) is not True:
            unsafe.append(flag)
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)
    return unsafe


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
        "changes_model": False,
        "changes_config": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "writes_official_trades_master": False,
        "runs_ocr": False,
        "imports_trades": False,
    }


def next_required_actions(
    *,
    missing_required: list[str],
    stale_required: list[str],
    stale_optional: list[str],
    invalid_sources: list[str],
    status: str,
) -> list[str]:
    actions = ["keep_live_disabled", "continue_paper_shadow_only"]
    if missing_required:
        actions.append("restore_missing_required_paper_runtime_reports")
    if stale_required:
        actions.append("refresh_stale_required_paper_runtime_reports")
    if stale_optional:
        actions.append("refresh_stale_optional_paper_runtime_reports")
    if invalid_sources:
        actions.append("repair_invalid_runtime_json_reports")
    if status != "ok":
        actions.append("inspect_paper_runtime_services")
    return sorted(set(actions))


def resolve_under_root(root: Path, output: str | Path) -> Path:
    candidate = Path(output)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output_path_outside_project_root:{resolved}") from exc
    return resolved


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def file_mtime_iso(path: Path) -> str | None:
    try:
        return iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
    except OSError:
        return None


def iso(value: datetime) -> str:
    return ensure_aware_utc(value).isoformat().replace("+00:00", "Z")
