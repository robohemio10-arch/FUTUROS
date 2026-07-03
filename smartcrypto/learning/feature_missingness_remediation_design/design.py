"""Design-only remediation diagnostics for critical AI feature missingness.

This module reads existing report evidence and, when available, source file
schemas. It produces a deterministic remediation design only. It does not
modify feature contracts, dataset manifests, datasets, models, registries,
runtime state, SQLite, or parquet artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ai_feature_missingness_remediation_design_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_FEATURE_CONTRACT = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_DATASET_MANIFEST = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_TARGET_STORE = Path("data/reports/financial_label_target_store_v1.json")
DEFAULT_DRIFT_MONITOR = Path("data/reports/ai_qlib_drift_regime_monitor_v1.json")
DEFAULT_EXECUTIVE_PACK = Path("data/reports/daily_evidence_readiness_executive_pack_v1.json")
DEFAULT_REPORT_JSON = Path("data/reports/ai_feature_missingness_remediation_design_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_feature_missingness_remediation_design_v1.md")

AFFECTED_FEATURES = ("feature_notional", "feature_quantity")
CRITICAL_NULL_RATE = 0.3
FORBIDDEN_SOURCE_PATTERNS = (
    "target",
    "outcome",
    "pnl",
    "net_pnl",
    "label",
    "result",
    "close_reason",
)
NOTIONAL_FIELDS = ("notional", "raw_notional", "trade_notional", "feature_notional")
QUANTITY_FIELDS = ("quantity", "qty", "amount", "trade_amount", "feature_quantity")
ENTRY_PRICE_FIELDS = (
    "entry_price",
    "feature_entry_price",
    "open_rate",
    "avg_entry_price",
    "preco_abertura",
    "price",
)


@dataclass(frozen=True)
class SourcePayload:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


def build_ai_feature_missingness_remediation_design_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a read-only, design-only remediation plan for missing AI features."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    sources = load_input_sources(root)
    payloads = {source.source_id: source.payload for source in sources if source.payload}
    missing_required = [
        f"missing_required_source:{source.relative_path}"
        for source in sources
        if source.required and (not source.exists or source.load_error is not None)
    ]

    feature_contract = payloads.get("feature_contract", {})
    dataset_manifest = payloads.get("dataset_manifest", {})
    drift_monitor = payloads.get("drift_monitor", {})
    target_store = payloads.get("target_store", {})

    source_availability = build_source_field_availability(root, dataset_manifest)
    missingness_findings = build_missingness_findings(
        feature_contract=feature_contract,
        dataset_manifest=dataset_manifest,
        drift_monitor=drift_monitor,
        source_availability=source_availability,
    )
    affected_features = [finding for finding in missingness_findings if finding["feature_name"] in AFFECTED_FEATURES]
    derivation_candidates = build_derivation_candidates(source_availability)
    remediation_design = build_remediation_design(affected_features, derivation_candidates)
    implementation_plan = build_implementation_plan(affected_features)
    validation_plan = build_validation_plan()
    non_goals = build_non_goals()
    lineage_hashes = build_lineage_hashes(payloads)
    blockers = sorted(
        set(
            missing_required
            + blockers_from_findings(affected_features)
            + blockers_from_derivations(derivation_candidates)
        )
    )
    warnings = sorted(set(warnings_from_source_availability(source_availability)))
    status, reason = decide_status(blockers, warnings, affected_features)
    safety = safety_flags()
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "missingness_findings": missingness_findings,
        "affected_features": [finding["feature_name"] for finding in affected_features],
        "source_field_availability": source_availability,
        "derivation_candidates": derivation_candidates,
        "remediation_design": remediation_design,
        "implementation_plan": implementation_plan,
        "validation_plan": validation_plan,
        "non_goals": non_goals,
        "blockers": blockers,
        "warnings": warnings,
        "lineage_hashes": lineage_hashes,
        "target_store_observed": {
            "exists": bool(target_store),
            "schema_version": target_store.get("schema_version"),
            "row_count": target_store.get("row_count"),
            "target_store_hash": target_store.get("target_store_hash"),
        },
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {"json": str(report_json), "markdown": str(report_md)},
        **safety,
        "safety_flags": safety,
    }
    if write_report:
        write_reports(report, report_json, report_md)
        report["write_performed"] = True
        write_json(report_json, report)
    return report


def load_input_sources(project_root: Path) -> list[SourcePayload]:
    source_specs = (
        ("feature_contract", DEFAULT_FEATURE_CONTRACT, True),
        ("dataset_manifest", DEFAULT_DATASET_MANIFEST, True),
        ("target_store", DEFAULT_TARGET_STORE, True),
        ("drift_monitor", DEFAULT_DRIFT_MONITOR, True),
        ("daily_evidence_readiness_executive_pack", DEFAULT_EXECUTIVE_PACK, False),
    )
    records: list[SourcePayload] = []
    for source_id, relative_path, required in source_specs:
        path = project_root / relative_path
        exists = path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None
        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                load_error = f"invalid_json:{exc.__class__.__name__}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
        records.append(
            SourcePayload(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )
    return records


def build_missingness_findings(
    *,
    feature_contract: Mapping[str, Any],
    dataset_manifest: Mapping[str, Any],
    drift_monitor: Mapping[str, Any],
    source_availability: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    row_count = to_int(
        dataset_manifest.get("row_count") or dataset_manifest.get("selected_training_dataset_rows"),
        default=0,
    )
    manifest_null_counts = mapping_or_empty(dataset_manifest.get("null_counts"))
    drift_rates = drift_missingness_by_feature(drift_monitor)
    feature_columns = list_of_strings(feature_contract.get("feature_columns"))
    observed_features = sorted(set(feature_columns).union(AFFECTED_FEATURES))
    findings: list[dict[str, Any]] = []
    for feature_name in observed_features:
        null_count = to_int(manifest_null_counts.get(feature_name), default=0)
        null_rate = drift_rates.get(feature_name)
        if null_rate is None:
            null_rate = round(null_count / row_count, 10) if row_count > 0 else None
        severity = classify_missingness(null_rate)
        required_fields = required_fields_for_feature(feature_name)
        available = fields_available_for_feature(feature_name, source_availability)
        missing = [field for field in required_fields if not field_family_available(field, available)]
        derivation_possible = derivation_possible_for_feature(feature_name, available)
        if feature_name not in AFFECTED_FEATURES and severity == "none":
            continue
        findings.append(
            {
                "feature_name": feature_name,
                "null_rate": null_rate,
                "null_count": null_count,
                "row_count": row_count,
                "severity": severity,
                "likely_root_cause": likely_root_cause(feature_name, severity, derivation_possible),
                "source_fields_required": required_fields,
                "source_fields_available": sorted(available),
                "source_fields_missing": missing,
                "derivation_possible": derivation_possible,
                "recommended_action": recommended_action(feature_name, derivation_possible, severity),
            }
        )
    return findings


def build_source_field_availability(project_root: Path, dataset_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths = source_paths_from_manifest(dataset_manifest)
    selected = dataset_manifest.get("selected_training_dataset")
    if isinstance(selected, str) and selected:
        paths.insert(0, selected)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = resolve_source_path(project_root, raw_path)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        columns, load_error = inspect_columns(path)
        normalized_columns = {normalize_name(column) for column in columns}
        records.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "load_error": load_error,
                "column_count": len(columns),
                "columns_sample": sorted(columns)[:40],
                "has_notional_field": any(normalize_name(field) in normalized_columns for field in NOTIONAL_FIELDS),
                "has_quantity_field": any(normalize_name(field) in normalized_columns for field in QUANTITY_FIELDS),
                "has_entry_price_field": any(normalize_name(field) in normalized_columns for field in ENTRY_PRICE_FIELDS),
                "available_notional_fields": matching_fields(columns, NOTIONAL_FIELDS),
                "available_quantity_fields": matching_fields(columns, QUANTITY_FIELDS),
                "available_entry_price_fields": matching_fields(columns, ENTRY_PRICE_FIELDS),
                "forbidden_source_fields_present": forbidden_fields(columns),
            }
        )
    return records


def build_derivation_candidates(source_availability: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    available_notional = source_fields(source_availability, "available_notional_fields")
    available_quantity = source_fields(source_availability, "available_quantity_fields")
    available_entry_price = source_fields(source_availability, "available_entry_price_fields")
    forbidden_present = sorted(
        {field for source in source_availability for field in list_of_strings(source.get("forbidden_source_fields_present"))}
    )
    notional_source_fields = available_notional or sorted(set(available_quantity + available_entry_price))
    notional_forbidden_used = forbidden_fields(notional_source_fields)
    quantity_forbidden_used = forbidden_fields(available_quantity)
    notional_method = (
        "prefer_raw_notional"
        if available_notional
        else "derive_abs_quantity_times_entry_price"
        if available_quantity and available_entry_price
        else "blocked"
    )
    quantity_method = "prefer_raw_quantity" if available_quantity else "blocked"
    return [
        {
            "feature_name": "feature_notional",
            "candidate_method": notional_method,
            "source_fields": notional_source_fields,
            "formula": "raw_notional" if available_notional else "abs(quantity * entry_price)",
            "derivation_possible": notional_method != "blocked",
            "blocked_reason": None if notional_method != "blocked" else "insufficient_source_fields",
            "anti_leakage_validated": not bool(notional_forbidden_used),
            "forbidden_fields_excluded": list(FORBIDDEN_SOURCE_PATTERNS),
            "forbidden_fields_detected_in_sources": forbidden_present,
            "forbidden_fields_used_by_candidate": notional_forbidden_used,
        },
        {
            "feature_name": "feature_quantity",
            "candidate_method": quantity_method,
            "source_fields": available_quantity,
            "formula": "raw_quantity_or_qty_or_amount",
            "derivation_possible": quantity_method != "blocked",
            "blocked_reason": None if quantity_method != "blocked" else "insufficient_source_fields",
            "anti_leakage_validated": not bool(quantity_forbidden_used),
            "forbidden_fields_excluded": list(FORBIDDEN_SOURCE_PATTERNS),
            "forbidden_fields_detected_in_sources": forbidden_present,
            "forbidden_fields_used_by_candidate": quantity_forbidden_used,
        },
    ]


def build_remediation_design(
    affected_features: Sequence[Mapping[str, Any]],
    derivation_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidates_by_feature = {str(item.get("feature_name")): item for item in derivation_candidates}
    return {
        "design_scope": "future_branch_only",
        "active_contract_change_allowed": False,
        "active_dataset_manifest_change_allowed": False,
        "recommended_builder_stage": "feature_builder_source_mapping_before_dataset_manifest",
        "affected_feature_count": len(affected_features),
        "steps": [
            {
                "step_id": "map_quantity_sources",
                "description": "Map quantity from explicit raw fields quantity, qty, amount, or trade_amount.",
                "applies_to": ["feature_quantity", "feature_notional"],
            },
            {
                "step_id": "map_notional_sources",
                "description": "Use raw notional when present; otherwise derive abs(quantity * entry_price).",
                "applies_to": ["feature_notional"],
            },
            {
                "step_id": "enforce_anti_leakage",
                "description": "Reject target, outcome, pnl, result, close_reason, and label columns as feature sources.",
                "applies_to": list(AFFECTED_FEATURES),
            },
            {
                "step_id": "rebuild_manifest_in_future_branch",
                "description": "Rebuild feature contract and dataset manifest only after tests prove null rates are remediated.",
                "applies_to": list(AFFECTED_FEATURES),
            },
        ],
        "candidate_by_feature": candidates_by_feature,
    }


def build_implementation_plan(affected_features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "step": 1,
            "title": "Add source-column mapping tests",
            "description": "Create fixtures with quantity, qty, amount, notional, and entry_price variants.",
            "required_before_runtime": True,
        },
        {
            "step": 2,
            "title": "Implement deterministic derivation in the canonical feature builder",
            "description": "Populate only feature_notional and feature_quantity from permitted pre-decision source fields.",
            "required_before_runtime": True,
        },
        {
            "step": 3,
            "title": "Rebuild report artifacts in a controlled future branch",
            "description": "Regenerate feature contract, dataset manifest, drift monitor, and executive pack evidence.",
            "required_before_runtime": True,
        },
        {
            "step": 4,
            "title": "Gate promotion explicitly",
            "description": "Require null_rate below critical threshold and no forbidden source usage before any later trainer branch.",
            "required_before_runtime": True,
        },
        {
            "step": 5,
            "title": "Keep this branch design-only",
            "description": f"Current affected features: {', '.join(str(item.get('feature_name')) for item in affected_features)}.",
            "required_before_runtime": False,
        },
    ]


def build_validation_plan() -> list[dict[str, Any]]:
    return [
        {
            "validation": "unit_feature_derivation",
            "expectation": "feature_quantity derives only from quantity, qty, amount, or trade_amount.",
        },
        {
            "validation": "unit_notional_derivation",
            "expectation": "feature_notional uses raw notional or abs(quantity * entry_price) only.",
        },
        {
            "validation": "anti_leakage_guard",
            "expectation": "target/outcome/pnl/result/close_reason/label columns are rejected as feature sources.",
        },
        {
            "validation": "manifest_null_rate_gate",
            "expectation": "future dataset manifest reports feature_notional and feature_quantity below critical null rate.",
        },
        {
            "validation": "drift_monitor_gate",
            "expectation": "feature_missingness_critical is absent only after real remediated evidence exists.",
        },
    ]


def build_non_goals() -> list[str]:
    return [
        "No active feature contract mutation",
        "No active dataset manifest mutation",
        "No model training",
        "No model promotion",
        "No registry write",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No Freqtrade or RiskManager changes",
        "No orders or private exchange access",
        "No parquet, SQLite, model, or runtime writes",
    ]


def blockers_from_findings(findings: Sequence[Mapping[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for finding in findings:
        if finding.get("severity") == "critical":
            blockers.append(f"critical_missingness:{finding.get('feature_name')}")
        if not finding.get("derivation_possible"):
            blockers.append(f"derivation_not_currently_possible:{finding.get('feature_name')}")
    return blockers


def blockers_from_derivations(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for candidate in candidates:
        if not candidate.get("derivation_possible"):
            blockers.append(f"insufficient_source_fields:{candidate.get('feature_name')}")
        if candidate.get("forbidden_fields_used_by_candidate"):
            blockers.append(f"forbidden_fields_present_in_sources:{candidate.get('feature_name')}")
    return blockers


def warnings_from_source_availability(source_availability: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not source_availability:
        return ["no_dataset_source_paths_available"]
    for source in source_availability:
        if source.get("exists") is False:
            warnings.append(f"source_missing:{source.get('path')}")
        if source.get("load_error"):
            warnings.append(f"source_schema_unreadable:{source.get('path')}:{source.get('load_error')}")
    return warnings


def decide_status(
    blockers: Sequence[str],
    warnings: Sequence[str],
    affected_features: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    if blockers:
        return "blocked", "feature_missingness_remediation_design_blocked"
    if any(item.get("severity") == "critical" for item in affected_features):
        return "blocked", "feature_missingness_critical_confirmed"
    if warnings:
        return "warning", "feature_missingness_remediation_design_warnings"
    return "ok", "feature_missingness_remediation_design_complete_research_only"


def render_markdown(report: Mapping[str, Any]) -> str:
    findings = report.get("missingness_findings", [])
    candidates = report.get("derivation_candidates", [])
    return "\n".join(
        [
            "# AI Feature Missingness Remediation Design V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Affected features: `{', '.join(list_of_strings(report.get('affected_features')))}`",
            "",
            "This artifact is design-only evidence. It diagnoses critical missingness without changing any active dataset,",
            "feature contract, model, registry, runtime, Freqtrade configuration, or risk component.",
            "",
            "## Missingness Findings",
            "",
            *markdown_findings(findings),
            "",
            "## Affected Features",
            "",
            f"- `{', '.join(list_of_strings(report.get('affected_features')))}`",
            "",
            "## Source Field Availability",
            "",
            *markdown_sources(report.get("source_field_availability", [])),
            "",
            "## Derivation Candidates",
            "",
            *markdown_candidates(candidates),
            "",
            "## Remediation Design",
            "",
            "- Map permitted raw fields before dataset manifest generation.",
            "- Derive `feature_notional` from raw notional or `abs(quantity * entry_price)` only.",
            "- Derive `feature_quantity` from raw quantity/qty/amount only.",
            "- Reject target/outcome/pnl/result/close_reason/label as feature sources.",
            "",
            "## Validation Plan",
            "",
            *[f"- `{item.get('validation')}`: {item.get('expectation')}" for item in report.get("validation_plan", [])],
            "",
            "## Safety Invariants",
            "",
            "- `operational_authority=false`",
            "- `readiness_release_authority=false`",
            "- `release_allowed=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "- `writes_runtime=false`",
            "- `writes_sqlite=false`",
            "- `writes_parquet=false`",
            "",
            "## Forbidden Actions",
            "",
            *[f"- {item}" for item in report.get("non_goals", [])],
            "",
            "## Next Branch Recommendation",
            "",
            "Implement and test the source-field mapping in the canonical feature builder in a future branch, then rebuild",
            "the feature contract, dataset manifest, drift monitor, and executive evidence without enabling runtime authority.",
            "",
        ]
    )


def markdown_findings(findings: Any) -> list[str]:
    rows = list_of_mappings(findings)
    if not rows:
        return ["- No missingness findings available."]
    return [
        (
            f"- `{item.get('feature_name')}`: null_rate=`{item.get('null_rate')}`, "
            f"null_count=`{item.get('null_count')}`, severity=`{item.get('severity')}`, "
            f"action=`{item.get('recommended_action')}`"
        )
        for item in rows
    ]


def markdown_sources(sources: Any) -> list[str]:
    rows = list_of_mappings(sources)
    if not rows:
        return ["- No source schemas available."]
    return [
        (
            f"- `{Path(str(item.get('path'))).name}`: quantity=`{item.get('available_quantity_fields')}`, "
            f"notional=`{item.get('available_notional_fields')}`, "
            f"entry_price=`{item.get('available_entry_price_fields')}`"
        )
        for item in rows
    ]


def markdown_candidates(candidates: Any) -> list[str]:
    rows = list_of_mappings(candidates)
    if not rows:
        return ["- No derivation candidates available."]
    return [
        (
            f"- `{item.get('feature_name')}`: method=`{item.get('candidate_method')}`, "
            f"possible=`{item.get('derivation_possible')}`, blocked_reason=`{item.get('blocked_reason')}`"
        )
        for item in rows
    ]


def write_reports(report: Mapping[str, Any], report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n", encoding="utf-8")


def drift_missingness_by_feature(drift_monitor: Mapping[str, Any]) -> dict[str, float]:
    section = mapping_or_empty(drift_monitor.get("feature_drift_section"))
    rows = list_of_mappings(section.get("feature_missingness"))
    output: dict[str, float] = {}
    for row in rows:
        feature = row.get("feature")
        null_rate = to_float(row.get("null_rate"))
        if isinstance(feature, str) and null_rate is not None:
            output[feature] = null_rate
    return output


def required_fields_for_feature(feature_name: str) -> list[str]:
    if feature_name == "feature_notional":
        return ["notional OR (quantity AND entry_price)"]
    if feature_name == "feature_quantity":
        return ["quantity OR qty OR amount"]
    return []


def fields_available_for_feature(feature_name: str, source_availability: Sequence[Mapping[str, Any]]) -> set[str]:
    if feature_name == "feature_notional":
        return set(source_fields(source_availability, "available_notional_fields") + source_fields(source_availability, "available_quantity_fields") + source_fields(source_availability, "available_entry_price_fields"))
    if feature_name == "feature_quantity":
        return set(source_fields(source_availability, "available_quantity_fields"))
    return set()


def derivation_possible_for_feature(feature_name: str, available: set[str]) -> bool:
    normalized = {normalize_name(field) for field in available}
    if feature_name == "feature_notional":
        return bool(normalized.intersection(map(normalize_name, NOTIONAL_FIELDS))) or (
            bool(normalized.intersection(map(normalize_name, QUANTITY_FIELDS)))
            and bool(normalized.intersection(map(normalize_name, ENTRY_PRICE_FIELDS)))
        )
    if feature_name == "feature_quantity":
        return bool(normalized.intersection(map(normalize_name, QUANTITY_FIELDS)))
    return False


def field_family_available(required_field: str, available: set[str]) -> bool:
    normalized = {normalize_name(field) for field in available}
    if "notional" in required_field:
        return bool(normalized.intersection(map(normalize_name, NOTIONAL_FIELDS))) or (
            bool(normalized.intersection(map(normalize_name, QUANTITY_FIELDS)))
            and bool(normalized.intersection(map(normalize_name, ENTRY_PRICE_FIELDS)))
        )
    if "quantity" in required_field:
        return bool(normalized.intersection(map(normalize_name, QUANTITY_FIELDS)))
    return True


def classify_missingness(null_rate: float | None) -> str:
    if null_rate is None:
        return "unknown"
    if null_rate >= CRITICAL_NULL_RATE:
        return "critical"
    if null_rate > 0:
        return "warning"
    return "none"


def likely_root_cause(feature_name: str, severity: str, derivation_possible: bool) -> str:
    if severity != "critical":
        return "no_critical_missingness_detected"
    if derivation_possible:
        return f"{feature_name}_source_fields_available_but_not_mapped_into_feature_dataset"
    return f"{feature_name}_raw_source_fields_missing_or_unreadable_in_current_evidence"


def recommended_action(feature_name: str, derivation_possible: bool, severity: str) -> str:
    if severity != "critical":
        return "monitor_only"
    if feature_name == "feature_notional" and derivation_possible:
        return "derive_notional_from_raw_notional_or_abs_quantity_times_entry_price_in_future_branch"
    if feature_name == "feature_quantity" and derivation_possible:
        return "map_quantity_from_raw_quantity_qty_or_amount_in_future_branch"
    return "block_until_source_fields_are_identified"


def inspect_columns(path: Path) -> tuple[list[str], str | None]:
    if not path.exists() or not path.is_file():
        return [], "missing_source_file"
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                return sorted(str(key) for key in parsed.keys()), None
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return sorted(str(key) for key in parsed[0].keys()), None
            return [], "json_schema_not_object_or_record_list"
        import pandas as pd  # type: ignore[import-not-found]

        if suffix == ".parquet":
            frame = pd.read_parquet(path)
            return [str(column) for column in frame.columns], None
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, nrows=0)
            return [str(column) for column in frame.columns], None
        if suffix == ".csv":
            frame = pd.read_csv(path, nrows=0)
            return [str(column) for column in frame.columns], None
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        return [], f"schema_read_failed:{exc.__class__.__name__}"
    return [], f"unsupported_source_suffix:{suffix}"


def source_paths_from_manifest(dataset_manifest: Mapping[str, Any]) -> list[str]:
    return [item for item in list_of_strings(dataset_manifest.get("source_paths")) if item]


def resolve_source_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def matching_fields(columns: Iterable[str], candidates: Sequence[str]) -> list[str]:
    normalized_candidates = {normalize_name(candidate) for candidate in candidates}
    return sorted(column for column in columns if normalize_name(column) in normalized_candidates)


def forbidden_fields(columns: Iterable[str]) -> list[str]:
    output = []
    for column in columns:
        normalized = normalize_name(column)
        if any(pattern in normalized for pattern in FORBIDDEN_SOURCE_PATTERNS):
            output.append(column)
    return sorted(set(output))


def source_fields(source_availability: Sequence[Mapping[str, Any]], field_key: str) -> list[str]:
    fields = {field for source in source_availability for field in list_of_strings(source.get(field_key))}
    return sorted(fields)


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for payload in payloads.values():
        for key in (
            "contract_hash",
            "feature_contract_hash",
            "dataset_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
        ):
            if key in payload and payload[key]:
                output[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, dict):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "design_only": True,
        "informational_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_feature_contract": False,
        "changes_dataset_manifest": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "runs_training": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def normalize_name(value: str) -> str:
    return str(value).strip().lower()


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
