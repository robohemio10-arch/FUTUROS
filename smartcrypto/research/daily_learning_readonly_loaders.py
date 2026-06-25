"""Read-only metadata loaders for Daily Paper/Master Learning Loop sources."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import (
    DAILY_LEARNING_SCHEMA_VERSION,
    SAFETY_FLAGS,
    SOURCE_MAP_SCHEMA_VERSION,
    build_daily_learning_contract_payload,
    build_daily_learning_source_map,
)


READONLY_LOADERS_SCHEMA_VERSION = "daily_learning_readonly_loaders_v1"

ALLOWED_NEXT_STEPS = [
    "criar KPI pack diario em branch futura",
    "criar divergence/alignment diario em branch futura",
    "criar candle coverage/entry features em branch futura",
    "criar mistake/winner catalog em branch futura",
    "criar pattern mining research em branch futura",
]

FORBIDDEN_ACTIONS = [
    "calcular KPIs nesta branch",
    "comparar Paper vs trades_master nesta branch",
    "carregar linhas de trades nesta branch",
    "carregar candles em massa nesta branch",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
]

LOADER_SCOPE: dict[str, bool] = {
    "loads_source_metadata": True,
    "loads_trade_rows": False,
    "loads_candle_rows": False,
    "loads_excel_rows": False,
    "loads_sqlite_rows": False,
    "computes_kpis": False,
    "computes_divergence": False,
    "computes_alignment": False,
    "computes_features": False,
    "writes_reports": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
}

READINESS_POLICY: dict[str, bool] = {
    "readonly_loader_report_is_not_readiness_evidence": True,
    "readonly_loader_outputs_do_not_release_live": True,
    "readonly_loader_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}


def build_daily_learning_readonly_loader_report(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a fail-safe report from metadata-only source inspection."""
    root = _resolve_project_root(project_root)
    contract = build_daily_learning_contract_payload(root)
    source_map = build_daily_learning_source_map(root)
    source_payloads = load_daily_learning_sources_readonly(root)
    sources = source_payloads["sources"]
    missing_required = [
        source["source_id"]
        for source in sources
        if source["required"] is True and source["exists"] is False
    ]
    available_required = [
        source["source_id"]
        for source in sources
        if source["required"] is True and source["exists"] is True
    ]
    optional_available = [
        source["source_id"]
        for source in sources
        if source["required"] is False and source["exists"] is True
    ]
    optional_missing = [
        source["source_id"]
        for source in sources
        if source["required"] is False and source["exists"] is False
    ]
    report: dict[str, Any] = {
        "schema_version": READONLY_LOADERS_SCHEMA_VERSION,
        "source_map_schema_version": SOURCE_MAP_SCHEMA_VERSION,
        "contracts_schema_version": DAILY_LEARNING_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "readonly_loaders_do_not_grant_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "source_summary": {
            "total_sources": len(sources),
            "required_sources": len(missing_required) + len(available_required),
            "optional_sources": len(optional_available) + len(optional_missing),
            "available_sources": len(available_required) + len(optional_available),
            "missing_sources": len(missing_required) + len(optional_missing),
            "missing_required_sources": len(missing_required),
            "optional_missing_sources": len(optional_missing),
        },
        "sources": sources,
        "missing_required_source_ids": missing_required,
        "available_required_source_ids": available_required,
        "optional_available_source_ids": optional_available,
        "optional_missing_source_ids": optional_missing,
        "freshness_summary": _build_freshness_summary(sources),
        "loader_scope": dict(LOADER_SCOPE),
        "readiness_policy": dict(READINESS_POLICY),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "contract_payload_status": contract.get("status"),
        "source_map_status": source_map.get("status"),
    }
    report["validation_errors"] = validate_daily_learning_readonly_loader_report(
        report
    )
    return report


def load_daily_learning_sources_readonly(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect all source-map entries through metadata-only checks."""
    root = _resolve_project_root(project_root)
    source_map = build_daily_learning_source_map(root)
    sources = [
        inspect_source_readonly(root, source)
        for source in source_map.get("sources", [])
        if isinstance(source, Mapping)
    ]
    return {
        "schema_version": READONLY_LOADERS_SCHEMA_VERSION,
        "source_map_schema_version": SOURCE_MAP_SCHEMA_VERSION,
        "project_root": str(root),
        "sources": sources,
    }


def inspect_source_readonly(
    project_root: Path,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect a single source path without loading rows or content."""
    source_id = str(source.get("source_id") or "unknown_source")
    expected_path = str(source.get("expected_path") or "")
    required = bool(source.get("required"))
    base_payload = {
        "source_id": source_id,
        "category": source.get("category"),
        "expected_path": expected_path,
        "required": required,
        "freshness_policy": source.get("freshness_policy"),
        "exists": False,
        "status": "invalid_path",
        "reason": "invalid_expected_path",
        "read_attempted": False,
        "write_attempted": False,
        "current_branch_reads_source": bool(
            source.get("current_branch_reads_source")
        ),
        "current_branch_writes_source": False,
        "loader_allowed_future_branch": bool(
            source.get("loader_allowed_future_branch")
        ),
        "metadata": _empty_metadata(expected_path),
        "sample_rows_loaded": 0,
        "kpis_computed": False,
        "financial_metrics_computed": False,
        "alignment_computed": False,
        "features_computed": False,
    }
    resolved = _resolve_expected_path(project_root, expected_path)
    if resolved is None:
        return base_payload
    try:
        resolved.relative_to(project_root)
    except ValueError:
        base_payload["metadata"] = _empty_metadata(str(resolved))
        return base_payload
    try:
        exists = resolved.exists()
        metadata = _metadata_for_path(resolved)
    except OSError as exc:
        base_payload.update(
            {
                "status": "read_error",
                "reason": f"metadata_read_error:{type(exc).__name__}",
                "metadata": _empty_metadata(str(resolved)),
            }
        )
        return base_payload
    status = "metadata_only" if exists else (
        "missing_required" if required else "missing_optional"
    )
    reason = "metadata_available_no_rows_loaded" if exists else (
        "required_source_missing" if required else "optional_source_missing"
    )
    base_payload.update(
        {
            "exists": exists,
            "status": status,
            "reason": reason,
            "metadata": metadata,
        }
    )
    return base_payload


def validate_daily_learning_readonly_loader_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the read-only loader report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": READONLY_LOADERS_SCHEMA_VERSION,
        "source_map_schema_version": SOURCE_MAP_SCHEMA_VERSION,
        "contracts_schema_version": DAILY_LEARNING_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "readonly_loaders_do_not_grant_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("loader_scope"))
    for key, expected in LOADER_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"loader_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        errors.append("sources_must_be_list")
        return errors
    source_ids: list[str] = []
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            errors.append(f"source_{index}_must_be_object")
            continue
        source_id = str(source.get("source_id") or f"source_{index}")
        source_ids.append(source_id)
        if source.get("write_attempted") is not False:
            errors.append(f"{source_id}_write_attempted_must_be_false")
        if source.get("sample_rows_loaded") != 0:
            errors.append(f"{source_id}_sample_rows_loaded_must_be_zero")
        for key in (
            "kpis_computed",
            "financial_metrics_computed",
            "alignment_computed",
            "features_computed",
            "current_branch_writes_source",
        ):
            if source.get(key) is not False:
                errors.append(f"{source_id}_{key}_must_be_false")
        if source.get("read_attempted") is not False:
            errors.append(f"{source_id}_read_attempted_must_be_false")
    if len(source_ids) != len(set(source_ids)):
        errors.append("source_ids_must_be_unique")
    missing_required = payload.get("missing_required_source_ids")
    if not isinstance(missing_required, list):
        errors.append("missing_required_source_ids_must_be_list")
    return errors


def _resolve_project_root(project_root: str | Path | None) -> Path:
    return Path("." if project_root is None else project_root).expanduser().resolve()


def _resolve_expected_path(project_root: Path, expected_path: str) -> Path | None:
    if not expected_path or "\x00" in expected_path:
        return None
    candidate = Path(expected_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _metadata_for_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_metadata(str(path))
    stat = path.stat()
    is_file = path.is_file()
    is_dir = path.is_dir()
    return {
        "path": str(path),
        "is_file": is_file,
        "is_dir": is_dir,
        "size_bytes": stat.st_size if is_file else None,
        "file_count": _directory_file_count(path) if is_dir else None,
        "mtime_utc": _timestamp_utc(stat.st_mtime),
        "extension": path.suffix.lower() if is_file else "",
    }


def _empty_metadata(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "is_file": False,
        "is_dir": False,
        "size_bytes": None,
        "file_count": None,
        "mtime_utc": None,
        "extension": Path(path).suffix.lower(),
    }


def _directory_file_count(path: Path) -> int:
    return sum(1 for child in path.iterdir() if child.is_file())


def _timestamp_utc(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).isoformat()


def _build_freshness_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    with_mtime = [
        source
        for source in sources
        if _mapping(source.get("metadata")).get("mtime_utc") is not None
    ]
    return {
        "policy_basis": "metadata_mtime_only",
        "freshness_does_not_release_operation": True,
        "sources_with_mtime": len(with_mtime),
        "sources_without_mtime": len(sources) - len(with_mtime),
        "available_daily_sources": [
            source["source_id"]
            for source in sources
            if source.get("freshness_policy") == "daily" and source.get("exists") is True
        ],
        "missing_daily_sources": [
            source["source_id"]
            for source in sources
            if source.get("freshness_policy") == "daily" and source.get("exists") is False
        ],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
