from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.dashboard_snapshots.contracts import (
    DashboardPageId,
    DashboardSourceContract,
    SourceKind,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    DASHBOARD_SNAPSHOT_FILENAMES,
    SOURCE_CATALOG,
)


class RequiredLevel(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    FUTURE_SOURCE_PENDING = "FUTURE_SOURCE_PENDING"
    GENERATED = "GENERATED"


class RuntimeSourceType(str, Enum):
    JSON_REPORT = "JSON_REPORT"
    JSON_SNAPSHOT = "JSON_SNAPSHOT"
    PARQUET_REPORT = "PARQUET_REPORT"
    SQLITE_READ_REPLICA = "SQLITE_READ_REPLICA"
    JSONL_EVENT_LOG = "JSONL_EVENT_LOG"
    RUNTIME_STATE = "RUNTIME_STATE"
    CONFIG_READONLY = "CONFIG_READONLY"
    UNKNOWN = "UNKNOWN"


class RuntimeSourceStatus(str, Enum):
    OK = "OK"
    MISSING_REQUIRED = "MISSING_REQUIRED"
    MISSING_OPTIONAL = "MISSING_OPTIONAL"
    FUTURE_SOURCE_PENDING = "FUTURE_SOURCE_PENDING"
    STALE = "STALE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_JSON = "INVALID_JSON"
    EMPTY = "EMPTY"
    READ_ERROR = "READ_ERROR"
    UNKNOWN = "UNKNOWN"


PAGE_TITLES: dict[DashboardPageId, str] = {
    DashboardPageId.infrastructure: "01 Infraestrutura",
    DashboardPageId.portfolio_risk: "02 Portfólio e Risco",
    DashboardPageId.grid_monitor: "03 Grid Spot Monitor",
    DashboardPageId.opportunity_scanner: "04 Oportunidades",
    DashboardPageId.ai_governance: "05 IA / Qlib Governance",
    DashboardPageId.active_controls: "06 Controles Ativos",
    DashboardPageId.quantitative_reports: "07 Relatórios & TCA",
    DashboardPageId.alerts_messaging: "08 Alertas & Mensageria",
}

SCHEMA_OVERRIDES = {
    "data/reports/runtime_evidence_pack_v2.json": "runtime_evidence_pack_v2",
    "data/reports/dashboard_infrastructure_snapshot.json": "dashboard_infrastructure_snapshot_v1",
    "data/reports/dashboard_portfolio_risk_snapshot.json": "dashboard_portfolio_risk_snapshot_v1",
    "data/reports/dashboard_grid_monitor_snapshot.json": "dashboard_grid_monitor_snapshot_v1",
    "data/reports/dashboard_opportunity_scanner_snapshot.json": "dashboard_opportunity_scanner_snapshot_v1",
    "data/reports/dashboard_ai_governance_snapshot.json": "dashboard_ai_governance_snapshot_v1",
    "data/reports/dashboard_active_controls_snapshot.json": "dashboard_active_controls_snapshot_v1",
    "data/reports/dashboard_quantitative_reports_snapshot.json": "dashboard_quantitative_reports_snapshot_v1",
    "data/reports/dashboard_alerts_messaging_snapshot.json": "dashboard_alerts_messaging_snapshot_v1",
}

FRESHNESS_OVERRIDES_SECONDS = {
    "data/reports/market_data_health_audit_report.json": 300.0,
    "data/reports/market_data_health_runtime_sources_report.json": 300.0,
    "data/reports/paper_runtime_container_snapshot_report.json": 300.0,
    "data/reports/latest_qlib_predictions_report.json": 900.0,
    "data/reports/active_signals_report.json": 900.0,
    "data/runtime/kill_switch.json": 900.0,
    "data/runtime/runtime_safety_audit_config.json": 900.0,
}

SOURCE_TIMESTAMP_FIELDS = (
    "last_updated_utc",
    "generated_at_utc",
    "created_at_utc",
    "finished_utc",
    "updated_at_utc",
    "generated_at",
    "created_at",
)

SOURCE_BLOCKING_STATUSES = {
    RuntimeSourceStatus.MISSING_REQUIRED,
    RuntimeSourceStatus.INVALID_SCHEMA,
    RuntimeSourceStatus.INVALID_JSON,
    RuntimeSourceStatus.EMPTY,
    RuntimeSourceStatus.READ_ERROR,
}

SOURCE_DEGRADED_STATUSES = {
    RuntimeSourceStatus.MISSING_OPTIONAL,
    RuntimeSourceStatus.STALE,
    RuntimeSourceStatus.INVALID_SCHEMA,
    RuntimeSourceStatus.INVALID_JSON,
    RuntimeSourceStatus.EMPTY,
    RuntimeSourceStatus.READ_ERROR,
    RuntimeSourceStatus.UNKNOWN,
}


@dataclass(frozen=True)
class RuntimeSourceDefinition:
    source_id: str
    canonical_path: str
    display_name: str
    owner_domain: str
    source_type: RuntimeSourceType
    required_level: RequiredLevel
    expected_schema_version: str | None
    freshness_policy: dict[str, Any] | None
    consumer_snapshots: tuple[str, ...]
    consumer_pages: tuple[str, ...]
    missing_behavior: str
    stale_behavior: str
    future_source_pending_behavior: str
    operator_hint: str
    runbook_hint: str
    safety_impact: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_type"] = self.source_type.value
        payload["required_level"] = self.required_level.value
        payload["consumer_snapshots"] = list(self.consumer_snapshots)
        payload["consumer_pages"] = list(self.consumer_pages)
        return payload


def source_catalog_records() -> tuple[RuntimeSourceDefinition, ...]:
    consumers: dict[str, set[DashboardPageId]] = {}
    contracts: dict[str, DashboardSourceContract] = {}
    for page_id, page_sources in SOURCE_CATALOG.items():
        for source in page_sources:
            contracts.setdefault(source.path, source)
            consumers.setdefault(source.path, set()).add(page_id)

    definitions = [
        definition_from_contract(contracts[path], tuple(sorted(consumers[path], key=lambda page: page.value)))
        for path in sorted(contracts)
    ]
    return tuple(definitions)


def definition_from_contract(
    contract: DashboardSourceContract,
    consumer_pages: tuple[DashboardPageId, ...] | None = None,
) -> RuntimeSourceDefinition:
    pages = consumer_pages or (contract.page_id,)
    level = required_level(contract.source_kind)
    canonical_path = contract.path.replace("\\", "/")
    return RuntimeSourceDefinition(
        source_id=source_id_for_path(canonical_path),
        canonical_path=canonical_path,
        display_name=display_name_for_path(canonical_path),
        owner_domain=owner_domain_for_path(canonical_path, pages),
        source_type=source_type_for_path(canonical_path),
        required_level=level,
        expected_schema_version=SCHEMA_OVERRIDES.get(canonical_path),
        freshness_policy=freshness_policy_for_path(canonical_path),
        consumer_snapshots=tuple(
            f"data/reports/{DASHBOARD_SNAPSHOT_FILENAMES[page]}" for page in pages
        ),
        consumer_pages=tuple(page.value for page in pages),
        missing_behavior=missing_behavior(level),
        stale_behavior=stale_behavior(level),
        future_source_pending_behavior=(
            "Show as planned source; do not block operational readiness."
            if level is RequiredLevel.FUTURE_SOURCE_PENDING
            else "Not applicable."
        ),
        operator_hint=operator_hint(level, canonical_path),
        runbook_hint=runbook_hint(owner_domain_for_path(canonical_path, pages)),
        safety_impact=safety_impact(level),
    )


def evaluate_source(
    definition: RuntimeSourceDefinition,
    project_root: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    current = ensure_utc(now_utc)
    matches = resolve_source_paths(project_root, definition.canonical_path)
    if definition.required_level is RequiredLevel.GENERATED:
        return source_status_payload(
            definition,
            RuntimeSourceStatus.OK,
            "generated_by_current_dashboard_snapshot_build",
            matches[0] if matches else project_root / definition.canonical_path,
            bool(matches),
            current,
            None,
        )
    if not matches:
        status = missing_source_status(definition.required_level)
        return source_status_payload(
            definition,
            status,
            missing_reason(status),
            project_root / definition.canonical_path,
            False,
            current,
            None,
        )

    target = max(matches, key=lambda path: path.stat().st_mtime)
    try:
        stat = target.stat()
        if stat.st_size <= 0:
            return source_status_payload(
                definition,
                RuntimeSourceStatus.EMPTY,
                "source_file_is_empty",
                target,
                True,
                current,
                None,
            )
        payload = load_validation_payload(target, definition.source_type)
        if payload is not None and is_empty_payload(payload):
            return source_status_payload(
                definition,
                RuntimeSourceStatus.EMPTY,
                "source_payload_is_empty",
                target,
                True,
                current,
                payload,
            )
        if definition.expected_schema_version:
            actual_schema = payload.get("schema_version") if isinstance(payload, Mapping) else None
            if actual_schema != definition.expected_schema_version:
                return source_status_payload(
                    definition,
                    RuntimeSourceStatus.INVALID_SCHEMA,
                    f"expected_schema:{definition.expected_schema_version};actual:{actual_schema or 'missing'}",
                    target,
                    True,
                    current,
                    payload,
                )
        age = source_age_seconds(target, payload, current)
        max_age = max_age_seconds(definition)
        if max_age is not None and age is not None and age > max_age:
            return source_status_payload(
                definition,
                RuntimeSourceStatus.STALE,
                f"source_age_exceeds_policy:{age:.3f}>{max_age:.3f}",
                target,
                True,
                current,
                payload,
            )
        return source_status_payload(
            definition,
            RuntimeSourceStatus.OK,
            "source_available_and_valid",
            target,
            True,
            current,
            payload,
        )
    except json.JSONDecodeError as exc:
        return source_status_payload(
            definition,
            RuntimeSourceStatus.INVALID_JSON,
            f"invalid_json:{exc.msg}",
            target,
            True,
            current,
            None,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return source_status_payload(
            definition,
            RuntimeSourceStatus.READ_ERROR,
            f"read_error:{type(exc).__name__}",
            target,
            True,
            current,
            None,
        )


def build_runtime_source_closeout(
    project_root: Path,
    now_utc: datetime,
    snapshot_statuses: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    definitions = source_catalog_records()
    statuses = [evaluate_source(definition, project_root, now_utc) for definition in definitions]
    page_matrix = build_page_source_matrix(statuses, snapshot_statuses or {})
    counts = {
        "required_sources_total": sum(item["required_level"] == RequiredLevel.REQUIRED.value for item in statuses),
        "required_sources_ok": sum(
            item["required_level"] == RequiredLevel.REQUIRED.value
            and item["status"] == RuntimeSourceStatus.OK.value
            for item in statuses
        ),
        "required_sources_missing": sum(
            item["status"] == RuntimeSourceStatus.MISSING_REQUIRED.value for item in statuses
        ),
        "stale_sources_total": sum(item["status"] == RuntimeSourceStatus.STALE.value for item in statuses),
        "future_sources_total": sum(
            item["required_level"] == RequiredLevel.FUTURE_SOURCE_PENDING.value for item in statuses
        ),
    }
    page_counts = {
        "pages_total": len(page_matrix),
        "pages_ok": sum(item["current_page_status"] == "OK" for item in page_matrix),
        "pages_degraded": sum(item["current_page_status"] == "DEGRADED" for item in page_matrix),
        "pages_blocked": sum(item["current_page_status"] == "BLOCKED" for item in page_matrix),
        "pages_unknown": sum(item["current_page_status"] == "UNKNOWN" for item in page_matrix),
    }
    dashboard_status = dashboard_status_from_pages(page_matrix)
    global_blocking = sorted(
        {
            f"{item['source_id']}:{item['status']}"
            for item in statuses
            if item["blocks_dashboard_readiness"]
        }
    )
    return {
        "dashboard_status": dashboard_status,
        "source_matrix": statuses,
        "page_source_matrix": page_matrix,
        "global_blocking_reasons": global_blocking,
        **counts,
        **page_counts,
    }


def build_page_source_matrix(
    statuses: list[dict[str, Any]],
    snapshot_statuses: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in DashboardPageId:
        page_sources = [item for item in statuses if page.value in item["consumer_pages"]]
        required = [item["source_id"] for item in page_sources if item["required_level"] == RequiredLevel.REQUIRED.value]
        optional = [item["source_id"] for item in page_sources if item["required_level"] == RequiredLevel.OPTIONAL.value]
        future = [item["source_id"] for item in page_sources if item["required_level"] == RequiredLevel.FUTURE_SOURCE_PENDING.value]
        blocking = [item["source_id"] for item in page_sources if item["blocks_page_operational_view"]]
        degraded = [
            item["source_id"]
            for item in page_sources
            if item["status"] in {status.value for status in SOURCE_DEGRADED_STATUSES}
            and not item["blocks_page_operational_view"]
        ]
        missing_optional = [
            item["source_id"]
            for item in page_sources
            if item["status"] == RuntimeSourceStatus.MISSING_OPTIONAL.value
        ]
        page_status = page_status_from_sources(
            page_sources,
            str(snapshot_statuses.get(page.value, "UNKNOWN")),
        )
        rows.append(
            {
                "page_id": page.value,
                "page_title": PAGE_TITLES[page],
                "snapshot_id": page.value,
                "snapshot_path": f"data/reports/{DASHBOARD_SNAPSHOT_FILENAMES[page]}",
                "required_sources": sorted(required),
                "optional_sources": sorted(optional),
                "future_sources": sorted(future),
                "current_page_status": page_status,
                "blocking_sources": sorted(blocking),
                "degraded_sources": sorted(degraded),
                "missing_optional_sources": sorted(missing_optional),
                "operator_summary": operator_summary(page_status, blocking, degraded),
            }
        )
    return rows


def source_status_payload(
    definition: RuntimeSourceDefinition,
    status: RuntimeSourceStatus,
    reason: str,
    target: Path,
    exists: bool,
    now_utc: datetime,
    payload: Any,
) -> dict[str, Any]:
    modified = modified_utc(target) if exists else None
    age = source_age_seconds(target, payload, now_utc) if exists else None
    max_age = max_age_seconds(definition)
    blocks_page = definition.required_level is RequiredLevel.REQUIRED and (
        status in SOURCE_BLOCKING_STATUSES
        or (status is RuntimeSourceStatus.STALE and definition.stale_behavior == "BLOCK")
    )
    blocks_readiness = blocks_page or (
        definition.required_level is RequiredLevel.REQUIRED
        and status is RuntimeSourceStatus.STALE
        and definition.stale_behavior == "BLOCK"
    )
    return {
        **definition.to_dict(),
        "status": status.value,
        "severity": severity_for_status(status, definition.required_level),
        "reason": reason,
        "path": path_for_report(target),
        "exists": exists,
        "last_modified_utc": modified,
        "age_seconds": age,
        "max_age_seconds": max_age,
        "stale": status is RuntimeSourceStatus.STALE,
        "blocks_dashboard_readiness": blocks_readiness,
        "blocks_page_operational_view": blocks_page,
    }


def load_validation_payload(path: Path, source_type: RuntimeSourceType) -> Any:
    if source_type in {RuntimeSourceType.JSON_REPORT, RuntimeSourceType.JSON_SNAPSHOT, RuntimeSourceType.RUNTIME_STATE, RuntimeSourceType.CONFIG_READONLY}:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if source_type is RuntimeSourceType.JSONL_EVENT_LOG:
        rows: list[Any] = []
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise json.JSONDecodeError(
                            f"invalid_jsonl_line_{line_number}:{exc.msg}", exc.doc, exc.pos
                        ) from exc
        return rows
    return None


def resolve_source_paths(project_root: Path, canonical_path: str) -> list[Path]:
    normalized = canonical_path.replace("\\", "/")
    if any(marker in normalized for marker in "*?["):
        return sorted(path for path in project_root.glob(normalized) if path.is_file())
    target = project_root / normalized
    return [target] if target.is_file() else []


def required_level(kind: SourceKind) -> RequiredLevel:
    return {
        SourceKind.REQUIRED_EXISTING_SOURCE: RequiredLevel.REQUIRED,
        SourceKind.OPTIONAL_EXISTING_SOURCE: RequiredLevel.OPTIONAL,
        SourceKind.FUTURE_SOURCE: RequiredLevel.FUTURE_SOURCE_PENDING,
        SourceKind.GENERATED_BY_THIS_BRANCH: RequiredLevel.GENERATED,
    }[kind]


def source_type_for_path(path: str) -> RuntimeSourceType:
    lowered = path.lower()
    if lowered.endswith(".jsonl"):
        return RuntimeSourceType.JSONL_EVENT_LOG
    if lowered.endswith(".parquet"):
        return RuntimeSourceType.PARQUET_REPORT
    if lowered.endswith((".sqlite", ".sqlite3", ".db")):
        return RuntimeSourceType.SQLITE_READ_REPLICA
    if lowered.endswith(".json"):
        if "dashboard_" in lowered and "snapshot" in lowered:
            return RuntimeSourceType.JSON_SNAPSHOT
        if "/runtime/" in lowered:
            return RuntimeSourceType.RUNTIME_STATE
        if "config" in lowered or "/contracts/" in lowered:
            return RuntimeSourceType.CONFIG_READONLY
        return RuntimeSourceType.JSON_REPORT
    return RuntimeSourceType.UNKNOWN


def owner_domain_for_path(path: str, pages: tuple[DashboardPageId, ...]) -> str:
    lowered = path.lower()
    if any(marker in lowered for marker in ("phase5", "training_dataset", "ocr")):
        return "ocr_dataset_pipeline"
    if any(marker in lowered for marker in ("qlib", "ai_shadow", "model_", "predictions")):
        return "ai_qlib_governance"
    if "market_data" in lowered or "signal" in lowered:
        return "market_data"
    if any(marker in lowered for marker in ("risk", "kill_switch", "ledger", "reconciliation", "financial_performance")):
        return "portfolio_risk"
    if any(marker in lowered for marker in ("alert", "notification", "event_log")):
        return "alerts_messaging"
    if "soak" in lowered:
        return "paper_shadow_soak"
    if "runtime_evidence" in lowered:
        return "runtime_evidence"
    if pages:
        return {
            DashboardPageId.infrastructure: "infrastructure",
            DashboardPageId.portfolio_risk: "portfolio_risk",
            DashboardPageId.grid_monitor: "grid_monitor",
            DashboardPageId.opportunity_scanner: "opportunity_scanner",
            DashboardPageId.ai_governance: "ai_qlib_governance",
            DashboardPageId.active_controls: "active_controls",
            DashboardPageId.quantitative_reports: "quantitative_reports",
            DashboardPageId.alerts_messaging: "alerts_messaging",
        }[pages[0]]
    return "unknown"


def source_id_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").replace("*", "wildcard")
    return "src_" + "_".join(part for part in _slug(normalized).split("_") if part)


def display_name_for_path(path: str) -> str:
    name = Path(path.replace("*", "latest")).name
    return name.rsplit(".", maxsplit=1)[0].replace("_", " ").strip().title()


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value)


def freshness_policy_for_path(path: str) -> dict[str, Any] | None:
    max_age = FRESHNESS_OVERRIDES_SECONDS.get(path)
    if max_age is None:
        return None
    return {"basis": "payload_timestamp_or_file_mtime", "max_age_seconds": max_age}


def max_age_seconds(definition: RuntimeSourceDefinition) -> float | None:
    policy = definition.freshness_policy
    if not policy:
        return None
    value = policy.get("max_age_seconds")
    return float(value) if value is not None else None


def missing_source_status(level: RequiredLevel) -> RuntimeSourceStatus:
    if level is RequiredLevel.REQUIRED:
        return RuntimeSourceStatus.MISSING_REQUIRED
    if level is RequiredLevel.OPTIONAL:
        return RuntimeSourceStatus.MISSING_OPTIONAL
    if level is RequiredLevel.FUTURE_SOURCE_PENDING:
        return RuntimeSourceStatus.FUTURE_SOURCE_PENDING
    return RuntimeSourceStatus.UNKNOWN


def missing_reason(status: RuntimeSourceStatus) -> str:
    return {
        RuntimeSourceStatus.MISSING_REQUIRED: "required_source_not_found",
        RuntimeSourceStatus.MISSING_OPTIONAL: "optional_source_not_found",
        RuntimeSourceStatus.FUTURE_SOURCE_PENDING: "planned_source_not_yet_available",
        RuntimeSourceStatus.UNKNOWN: "generated_source_not_materialized",
    }[status]


def missing_behavior(level: RequiredLevel) -> str:
    return {
        RequiredLevel.REQUIRED: "BLOCK_CONSUMER_PAGE",
        RequiredLevel.OPTIONAL: "DEGRADE_WITH_EXPLICIT_WARNING",
        RequiredLevel.FUTURE_SOURCE_PENDING: "SHOW_PLANNED_PENDING_WITHOUT_FAILURE",
        RequiredLevel.GENERATED: "GENERATE_DURING_DASHBOARD_BUILD",
    }[level]


def stale_behavior(level: RequiredLevel) -> str:
    return "BLOCK" if level is RequiredLevel.REQUIRED else "DEGRADE"


def operator_hint(level: RequiredLevel, path: str) -> str:
    if level is RequiredLevel.FUTURE_SOURCE_PENDING:
        return "No action required; track the planned producer in its dedicated roadmap item."
    if level is RequiredLevel.GENERATED:
        return "Run scripts/build_dashboard_snapshots.py to regenerate this read-only snapshot."
    return f"Run the documented producer for {path}, then rebuild dashboard snapshots."


def runbook_hint(owner_domain: str) -> str:
    return f"Consult the {owner_domain} runbook; do not generate data from Streamlit."


def safety_impact(level: RequiredLevel) -> str:
    if level is RequiredLevel.REQUIRED:
        return "Missing or invalid data blocks the affected operational view; no safety gate is bypassed."
    if level is RequiredLevel.OPTIONAL:
        return "Missing data degrades observability only and does not enable live or orders."
    if level is RequiredLevel.FUTURE_SOURCE_PENDING:
        return "Planned source is informational and cannot block or authorize runtime actions."
    return "Generated read-only dashboard artifact with no operational authority."


def severity_for_status(status: RuntimeSourceStatus, level: RequiredLevel) -> str:
    if status is RuntimeSourceStatus.OK:
        return "INFO"
    if status is RuntimeSourceStatus.FUTURE_SOURCE_PENDING:
        return "INFO"
    if level is RequiredLevel.REQUIRED and status in SOURCE_BLOCKING_STATUSES:
        return "CRITICAL"
    if status in {RuntimeSourceStatus.STALE, RuntimeSourceStatus.INVALID_JSON, RuntimeSourceStatus.INVALID_SCHEMA, RuntimeSourceStatus.READ_ERROR, RuntimeSourceStatus.EMPTY}:
        return "HIGH" if level is RequiredLevel.REQUIRED else "WARNING"
    return "WARNING"


def source_age_seconds(path: Path, payload: Any, now_utc: datetime) -> float | None:
    timestamp = timestamp_from_payload(payload)
    if timestamp is None:
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None
    return max((ensure_utc(now_utc) - timestamp).total_seconds(), 0.0)


def timestamp_from_payload(payload: Any) -> datetime | None:
    if not isinstance(payload, Mapping):
        return None
    for key in SOURCE_TIMESTAMP_FIELDS:
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_utc(parsed)


def modified_utc(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def path_for_report(path: Path) -> str:
    return path.as_posix()


def ensure_utc(value: datetime) -> datetime:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def is_empty_payload(payload: Any) -> bool:
    return payload in ({}, [])


def page_status_from_sources(page_sources: list[dict[str, Any]], snapshot_status: str) -> str:
    statuses = {str(item["status"]) for item in page_sources}
    normalized_snapshot = snapshot_status.upper()
    if any(item["blocks_page_operational_view"] for item in page_sources):
        return "BLOCKED"
    if normalized_snapshot in {"ERROR", "BLOCKED", "MISSING_REQUIRED"}:
        return "BLOCKED"
    if statuses & {status.value for status in SOURCE_DEGRADED_STATUSES}:
        return "DEGRADED"
    if normalized_snapshot in {"WARNING", "DEGRADED", "STALE", "MISSING_OPTIONAL"}:
        return "DEGRADED"
    if normalized_snapshot == "OK":
        return "OK"
    return "UNKNOWN"


def dashboard_status_from_pages(page_matrix: list[dict[str, Any]]) -> str:
    statuses = {str(item["current_page_status"]) for item in page_matrix}
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "DEGRADED" in statuses:
        return "DEGRADED"
    if statuses == {"OK"}:
        return "OK"
    return "UNKNOWN"


def operator_summary(status: str, blocking: list[str], degraded: list[str]) -> str:
    if status == "BLOCKED":
        return f"Regenerate or repair required sources: {', '.join(sorted(blocking))}."
    if status == "DEGRADED":
        return f"Review stale or optional sources: {', '.join(sorted(degraded))}."
    if status == "OK":
        return "All required runtime sources are available and valid."
    return "Source state is incomplete; inspect the source matrix before relying on this page."
