"""Research-only contract for contemporary source fields.

This module classifies source fields that may be used by a future branch to
derive ``feature_notional`` and ``feature_quantity``. It never derives feature
values, never joins external sources, and never mutates active datasets,
contracts, registries, models, or runtime state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ai_feature_source_fields_enrichment_contract_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_FEATURE_CONTRACT = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_DATASET_MANIFEST = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_DESIGN_REPORT = Path("data/reports/ai_feature_missingness_remediation_design_v1.json")
DEFAULT_REPORT_JSON = Path("data/reports/ai_feature_source_fields_enrichment_contract_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_feature_source_fields_enrichment_contract_v1.md")

TARGET_FEATURES = ("feature_notional", "feature_quantity")

FORBIDDEN_PATTERNS = (
    "label",
    "target",
    "outcome",
    "pnl",
    "profit",
    "win_loss",
    "future",
    "roi_hit",
    "stoploss_hit",
    "time_exit",
    "expected_value_proxy",
)

PRICE_FIELDS = ("price", "rate", "open", "close", "entry_price")
QUANTITY_FIELDS = ("amount", "quantity", "base_amount", "contracts")
NOTIONAL_FIELDS = ("stake_amount", "notional", "cost", "quote_amount")
CONTEXT_FIELDS = ("timestamp", "open_date", "symbol", "pair", "side")

AMBIGUOUS_PATTERNS = (
    "value",
    "volume",
    "size",
    "total",
    "balance",
    "margin",
    "position",
    "filled",
    "fee",
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


def build_ai_feature_source_fields_enrichment_contract_v1(
    *,
    project_root: str | Path,
    available_fields: Sequence[str] | None = None,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build source-field enrichment evidence in memory."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    sources, payloads = load_input_sources(root)
    discovered = list(available_fields) if available_fields is not None else discover_available_fields(root, payloads)
    classification = classify_source_fields(discovered)
    derivation = evaluate_derivation_readiness(classification)
    blockers = build_blockers(sources, discovered, derivation)
    warnings = build_warnings(classification)
    status, reason = decide_status(blockers, warnings)
    safety = safety_flags()
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)
    source_contract_status = "complete" if status == "ok" else "incomplete" if status == "warning" else "blocked"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "target_features": list(TARGET_FEATURES),
        "available_fields": sorted_unique(discovered),
        "allowed_source_fields": classification["allowed_source_fields"],
        "forbidden_fields_present": classification["forbidden_fields_present"],
        "forbidden_fields_used": [],
        "ambiguous_fields_requires_review": classification["ambiguous_fields_requires_review"],
        "missing_required_source_fields": derivation["missing_required_source_fields"],
        "can_derive_feature_notional": derivation["can_derive_feature_notional"],
        "can_derive_feature_quantity": derivation["can_derive_feature_quantity"],
        "source_contract_status": source_contract_status,
        "classification_policy": build_classification_policy(),
        "non_goals": build_non_goals(),
        "blockers": blockers,
        "warnings": warnings,
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


def load_input_sources(project_root: Path) -> tuple[list[SourcePayload], dict[str, dict[str, Any]]]:
    source_specs = (
        ("feature_contract", DEFAULT_FEATURE_CONTRACT, False),
        ("dataset_manifest", DEFAULT_DATASET_MANIFEST, False),
        ("feature_missingness_design", DEFAULT_DESIGN_REPORT, False),
    )
    records: list[SourcePayload] = []
    payloads: dict[str, dict[str, Any]] = {}
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
        if payload:
            payloads[source_id] = payload
    return records, payloads


def discover_available_fields(project_root: Path, payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    fields: list[str] = []
    feature_contract = payloads.get("feature_contract", {})
    dataset_manifest = payloads.get("dataset_manifest", {})
    design_report = payloads.get("feature_missingness_design", {})

    fields.extend(list_of_strings(feature_contract.get("source_fields")))
    fields.extend(list_of_strings(feature_contract.get("available_source_fields")))
    fields.extend(list_of_strings(feature_contract.get("raw_columns")))
    fields.extend(list_of_strings(dataset_manifest.get("available_fields")))
    fields.extend(list_of_strings(dataset_manifest.get("columns")))
    fields.extend(list_of_strings(dataset_manifest.get("raw_columns")))
    fields.extend(fields_from_design_report(design_report))

    for raw_path in source_paths_from_manifest(dataset_manifest):
        path = resolve_source_path(project_root, raw_path)
        columns, _error = inspect_columns(path)
        fields.extend(columns)
    return sorted_unique(fields)


def fields_from_design_report(design_report: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for row in list_of_mappings(design_report.get("source_field_availability")):
        fields.extend(list_of_strings(row.get("available_notional_fields")))
        fields.extend(list_of_strings(row.get("available_quantity_fields")))
        fields.extend(list_of_strings(row.get("available_entry_price_fields")))
        fields.extend(list_of_strings(row.get("columns_sample")))
    for row in list_of_mappings(design_report.get("derivation_candidates")):
        fields.extend(list_of_strings(row.get("source_fields")))
    return fields


def classify_source_fields(fields: Sequence[str]) -> dict[str, Any]:
    allowed = {
        "feature_notional": [],
        "feature_quantity": [],
        "context": [],
    }
    forbidden: list[str] = []
    ambiguous: list[str] = []
    for field in sorted_unique(fields):
        normalized = normalize_field(field)
        if is_forbidden_field(normalized):
            forbidden.append(field)
            continue
        if normalized in NOTIONAL_FIELDS:
            allowed["feature_notional"].append(field)
            continue
        if normalized in QUANTITY_FIELDS:
            allowed["feature_quantity"].append(field)
            allowed["feature_notional"].append(field)
            continue
        if normalized in PRICE_FIELDS:
            allowed["feature_notional"].append(field)
            continue
        if normalized in CONTEXT_FIELDS:
            allowed["context"].append(field)
            continue
        if is_ambiguous_field(normalized):
            ambiguous.append(field)
    return {
        "allowed_source_fields": {key: sorted_unique(value) for key, value in allowed.items()},
        "forbidden_fields_present": sorted_unique(forbidden),
        "ambiguous_fields_requires_review": sorted_unique(ambiguous),
    }


def evaluate_derivation_readiness(classification: Mapping[str, Any]) -> dict[str, Any]:
    allowed = classification.get("allowed_source_fields")
    allowed_map = allowed if isinstance(allowed, Mapping) else {}
    notional_fields = {normalize_field(field) for field in list_of_strings(allowed_map.get("feature_notional"))}
    quantity_fields = {normalize_field(field) for field in list_of_strings(allowed_map.get("feature_quantity"))}
    has_direct_notional = bool(notional_fields.intersection(NOTIONAL_FIELDS))
    has_quantity = bool(quantity_fields.intersection(QUANTITY_FIELDS))
    has_price = bool(notional_fields.intersection(PRICE_FIELDS))
    can_derive_quantity = has_quantity
    can_derive_notional = has_direct_notional or (has_quantity and has_price)
    missing: list[str] = []
    if not can_derive_quantity:
        missing.append("feature_quantity: amount|quantity|base_amount|contracts")
    if not can_derive_notional:
        missing.append("feature_notional: stake_amount|notional|cost|quote_amount OR quantity+price")
    return {
        "can_derive_feature_notional": can_derive_notional,
        "can_derive_feature_quantity": can_derive_quantity,
        "missing_required_source_fields": missing,
    }


def build_blockers(
    sources: Sequence[SourcePayload],
    available_fields: Sequence[str],
    derivation: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if not available_fields:
        blockers.append("no_available_source_fields")
    for source in sources:
        if source.required and (not source.exists or source.load_error):
            blockers.append(f"missing_required_source:{source.relative_path}")
    for item in list_of_strings(derivation.get("missing_required_source_fields")):
        blockers.append(f"missing_required_source_field:{item}")
    return sorted_unique(blockers)


def build_warnings(classification: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if classification.get("ambiguous_fields_requires_review"):
        warnings.append("ambiguous_source_fields_require_review")
    if classification.get("forbidden_fields_present"):
        warnings.append("forbidden_source_fields_present_but_not_used")
    return warnings


def decide_status(blockers: Sequence[str], warnings: Sequence[str]) -> tuple[str, str]:
    if blockers:
        return "blocked", "feature_source_fields_enrichment_contract_blocked"
    if warnings:
        return "warning", "feature_source_fields_enrichment_contract_requires_review"
    return "ok", "feature_source_fields_enrichment_contract_complete_research_only"


def build_classification_policy() -> dict[str, Any]:
    return {
        "forbidden_patterns": list(FORBIDDEN_PATTERNS),
        "contemporary_price_fields": list(PRICE_FIELDS),
        "contemporary_quantity_fields": list(QUANTITY_FIELDS),
        "contemporary_notional_fields": list(NOTIONAL_FIELDS),
        "context_fields": list(CONTEXT_FIELDS),
        "ambiguous_patterns": list(AMBIGUOUS_PATTERNS),
        "forbidden_fields_used_must_remain_empty": True,
    }


def build_non_goals() -> list[str]:
    return [
        "No derivation of feature_notional or feature_quantity in this branch",
        "No active dataset write",
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


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
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
        "active_contract_changed": False,
        "active_dataset_manifest_changed": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "runs_training": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_registry": False,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# AI Feature Source Fields Enrichment Contract V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Source contract status: `{report.get('source_contract_status')}`",
            f"- Target features: `{', '.join(list_of_strings(report.get('target_features')))}`",
            "",
            "This report classifies contemporary source fields for a future, safe derivation of",
            "`feature_notional` and `feature_quantity`. It does not derive features and does not change",
            "any active dataset, feature contract, model, registry, runtime, Freqtrade, or RiskManager component.",
            "",
            "## Available Fields",
            "",
            f"`{report.get('available_fields')}`",
            "",
            "## Allowed Source Fields",
            "",
            f"- feature_notional: `{mapping_or_empty(report.get('allowed_source_fields')).get('feature_notional', [])}`",
            f"- feature_quantity: `{mapping_or_empty(report.get('allowed_source_fields')).get('feature_quantity', [])}`",
            f"- context: `{mapping_or_empty(report.get('allowed_source_fields')).get('context', [])}`",
            "",
            "## Forbidden And Ambiguous Fields",
            "",
            f"- Forbidden fields present: `{report.get('forbidden_fields_present')}`",
            f"- Forbidden fields used: `{report.get('forbidden_fields_used')}`",
            f"- Ambiguous fields requiring review: `{report.get('ambiguous_fields_requires_review')}`",
            "",
            "## Derivation Readiness",
            "",
            f"- can_derive_feature_notional: `{report.get('can_derive_feature_notional')}`",
            f"- can_derive_feature_quantity: `{report.get('can_derive_feature_quantity')}`",
            f"- missing_required_source_fields: `{report.get('missing_required_source_fields')}`",
            "",
            "## Safety Invariants",
            "",
            "- `decision=MANTER_EM_RESEARCH`",
            "- `research_only=true`",
            "- `read_only=true`",
            "- `release_allowed=false`",
            "- `changes_feature_contract=false`",
            "- `changes_dataset_manifest=false`",
            "- `runs_training=false`",
            "- `writes_registry=false`",
            "- `writes_runtime=false`",
            "- `writes_sqlite=false`",
            "- `writes_parquet=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "",
            "## Non-Goals",
            "",
            *[f"- {item}" for item in list_of_strings(report.get("non_goals"))],
            "",
        ]
    )


def write_reports(report: Mapping[str, Any], report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def inspect_columns(path: Path) -> tuple[list[str], str | None]:
    if not path.exists() or not path.is_file():
        return [], "missing_source_file"
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                return sorted(str(key) for key in parsed), None
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return sorted(str(key) for key in parsed[0]), None
            return [], "json_schema_not_object_or_record_list"
        import pandas as pd  # type: ignore[import-not-found]

        if suffix == ".csv":
            frame = pd.read_csv(path, nrows=0)
            return [str(column) for column in frame.columns], None
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, nrows=0)
            return [str(column) for column in frame.columns], None
        if suffix == ".parquet":
            try:
                import pyarrow.parquet as pq  # type: ignore[import-not-found]

                return [str(column) for column in pq.ParquetFile(path).schema.names], None
            except ImportError:
                frame = pd.read_parquet(path)
                return [str(column) for column in frame.columns], None
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        return [], f"schema_read_failed:{exc.__class__.__name__}"
    return [], f"unsupported_source_suffix:{suffix}"


def source_paths_from_manifest(dataset_manifest: Mapping[str, Any]) -> list[str]:
    paths = []
    selected = dataset_manifest.get("selected_training_dataset")
    if isinstance(selected, str) and selected:
        paths.append(selected)
    paths.extend(list_of_strings(dataset_manifest.get("source_paths")))
    return sorted_unique(paths)


def resolve_source_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def normalize_field(value: str) -> str:
    return str(value).strip().lower()


def is_forbidden_field(normalized: str) -> bool:
    return any(pattern in normalized for pattern in FORBIDDEN_PATTERNS)


def is_ambiguous_field(normalized: str) -> bool:
    return any(pattern in normalized for pattern in AMBIGUOUS_PATTERNS)


def sorted_unique(values: Iterable[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


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


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
