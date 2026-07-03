"""Deterministic, research-only remediation for critical AI feature missingness.

This module reads existing report evidence and a single dataset source (the
dataset manifest's ``selected_training_dataset``). It derives ``feature_notional``
and ``feature_quantity`` only from permitted raw fields present on the same
dataset row, and proves before/after missingness from the dataset loaded in
memory. It never joins with outcome, feedback, or label sources, never reads
target/outcome/pnl/label/result/exit_reason/close_reason/future_ret/win/loss/
profit fields, and never mutates any active feature contract, dataset
manifest, dataset, model, registry, or runtime artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ai_feature_missingness_remediation_implementation_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_FEATURE_CONTRACT = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_DATASET_MANIFEST = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_DESIGN_REPORT = Path("data/reports/ai_feature_missingness_remediation_design_v1.json")
DEFAULT_REPORT_JSON = Path("data/reports/ai_feature_missingness_remediation_implementation_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_feature_missingness_remediation_implementation_v1.md")

AFFECTED_FEATURES = ("feature_notional", "feature_quantity")
CRITICAL_NULL_RATE = 0.3

FORBIDDEN_FIELD_PATTERNS = (
    "target",
    "outcome",
    "pnl",
    "label",
    "result",
    "exit_reason",
    "close_reason",
    "future_ret",
    "win",
    "loss",
    "profit",
)

QUANTITY_ALIASES = (
    "feature_quantity",
    "quantity",
    "qty",
    "amount",
    "trade_amount",
    "volume",
    "volume_posicao",
    "volume_fechado",
)
ENTRY_PRICE_ALIASES = (
    "feature_entry_price",
    "entry_price",
    "open_rate",
    "avg_entry_price",
    "preco_abertura",
    "price",
)
NOTIONAL_ALIASES = (
    "feature_notional",
    "notional",
    "raw_notional",
    "trade_notional",
)
NOTIONAL_LEGACY_ALIASES = ("notiional",)
NOTIONAL_RAW_ALIASES = NOTIONAL_ALIASES + NOTIONAL_LEGACY_ALIASES

AMBIGUOUS_PRICE_ALIAS = "price"
SELF_ALIAS_QUANTITY = "feature_quantity"
SELF_ALIAS_NOTIONAL = "feature_notional"

ALIAS_COLUMNS = frozenset(QUANTITY_ALIASES) | frozenset(ENTRY_PRICE_ALIASES) | frozenset(NOTIONAL_RAW_ALIASES)

REQUIRED_REPORT_SOURCES = (
    ("feature_contract", DEFAULT_FEATURE_CONTRACT),
    ("dataset_manifest", DEFAULT_DATASET_MANIFEST),
    ("design_report", DEFAULT_DESIGN_REPORT),
)


@dataclass(frozen=True)
class SourcePayload:
    source_id: str
    relative_path: str
    path: Path
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


@dataclass(frozen=True)
class DatasetLocation:
    relative_path: str | None
    resolved_path: Path | None
    exists: bool
    sha256: str | None

    def public_record(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "path": str(self.resolved_path) if self.resolved_path is not None else None,
            "exists": self.exists,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RowDerivation:
    feature_quantity_before: float | None
    feature_quantity_after: float | None
    feature_quantity_source_field: str | None
    feature_notional_before: float | None
    feature_notional_after: float | None
    feature_notional_source_fields: tuple[str, ...]
    feature_notional_method: str
    entry_price_source_field: str | None
    forbidden_fields_present: tuple[str, ...]


def build_ai_feature_missingness_remediation_implementation_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a read-only, research-only remediation proof for missing AI features."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()

    sources, payloads = load_required_report_sources(root)
    blockers: list[str] = [
        f"missing_required_source:{source.relative_path}"
        for source in sources
        if not source.exists or source.load_error is not None
    ]
    warnings: list[str] = []

    dataset_manifest = payloads.get("dataset_manifest", {})
    dataset_location = resolve_selected_dataset_path(root, dataset_manifest)

    remediation_result: dict[str, Any] | None = None
    dataset_forbidden_fields: list[str] = []
    dataset_load_error: str | None = None

    if dataset_location.resolved_path is None or not dataset_location.exists:
        blockers.append("missing_required_source:selected_training_dataset")
    else:
        try:
            rows, dataset_forbidden_fields, dataset_load_error = load_dataset(dataset_location.resolved_path)
        except Exception as exc:  # defensive boundary for arbitrary external dataset files
            rows, dataset_forbidden_fields, dataset_load_error = None, [], f"dataset_read_failed:{exc.__class__.__name__}"
        if dataset_load_error is not None or rows is None:
            blockers.append(f"dataset_source_unreadable:{dataset_location.relative_path}:{dataset_load_error}")
        else:
            remediation_result = remediate_rows(rows)
            for feature in remediation_result["remediated_features"]:
                if feature["blocked_reason"]:
                    blockers.append(f"insufficient_source_fields:{feature['feature_name']}")
                elif feature.get("after_null_rate") is not None and feature["after_null_rate"] >= CRITICAL_NULL_RATE:
                    warnings.append(f"residual_missingness_critical:{feature['feature_name']}")
            warnings.extend(remediation_result["warnings"])

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    status, reason = decide_status(blockers, warnings)
    safety = safety_flags()

    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)

    forbidden_fields_present = sorted(
        set(dataset_forbidden_fields) | set((remediation_result or {}).get("forbidden_fields_present", []))
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "selected_training_dataset": dataset_location.public_record(),
        "affected_features": list(AFFECTED_FEATURES),
        "remediated_features": (remediation_result or {}).get("remediated_features", []),
        "row_count": (remediation_result or {}).get("row_count"),
        "forbidden_fields_present": forbidden_fields_present,
        "forbidden_fields_used": [],
        "dataset_load_error": dataset_load_error,
        "no_join_sources_used": True,
        "blockers": blockers,
        "warnings": warnings,
        "non_goals": build_non_goals(),
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


def load_required_report_sources(project_root: Path) -> tuple[list[SourcePayload], dict[str, dict[str, Any]]]:
    records: list[SourcePayload] = []
    payloads: dict[str, dict[str, Any]] = {}
    for source_id, relative_path in REQUIRED_REPORT_SOURCES:
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
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )
        if payload:
            payloads[source_id] = payload
    return records, payloads


def resolve_selected_dataset_path(root: Path, dataset_manifest: Mapping[str, Any]) -> DatasetLocation:
    raw_path = dataset_manifest.get("selected_training_dataset")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return DatasetLocation(relative_path=None, resolved_path=None, exists=False, sha256=None)
    candidate = Path(raw_path)
    resolved = candidate if candidate.is_absolute() else root / candidate
    exists = resolved.is_file()
    return DatasetLocation(
        relative_path=raw_path,
        resolved_path=resolved,
        exists=exists,
        sha256=file_sha256(resolved) if exists else None,
    )


def is_forbidden_field(name: Any) -> bool:
    normalized = str(name).strip().lower()
    return any(pattern in normalized for pattern in FORBIDDEN_FIELD_PATTERNS)


def sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    clean: dict[str, Any] = {}
    forbidden_found: list[str] = []
    for key, value in row.items():
        if is_forbidden_field(key):
            forbidden_found.append(str(key))
            continue
        clean[str(key)] = value
    return clean, forbidden_found


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:  # filters float NaN and NaN-like sentinels
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if hasattr(value, "item"):
        try:
            item_value = value.item()
        except (TypeError, ValueError):
            return None
        return to_number(item_value)
    return None


def first_numeric_alias(row: Mapping[str, Any], aliases: Sequence[str]) -> tuple[float | None, str | None]:
    for alias in aliases:
        if alias in row:
            number = to_number(row[alias])
            if number is not None:
                return number, alias
    return None, None


def derive_row(raw_row: Mapping[str, Any]) -> RowDerivation:
    clean_row, forbidden_found = sanitize_row(raw_row)

    before_quantity = to_number(clean_row.get("feature_quantity"))
    before_notional = to_number(clean_row.get("feature_notional"))

    quantity_value, quantity_field = first_numeric_alias(clean_row, QUANTITY_ALIASES)
    entry_price_value, entry_price_field = first_numeric_alias(clean_row, ENTRY_PRICE_ALIASES)
    notional_raw_value, notional_raw_field = first_numeric_alias(clean_row, NOTIONAL_RAW_ALIASES)

    if notional_raw_value is not None:
        notional_value: float | None = notional_raw_value
        notional_method = "raw_notional_field"
        notional_source_fields: tuple[str, ...] = (notional_raw_field,) if notional_raw_field else ()
    elif quantity_value is not None and entry_price_value is not None:
        notional_value = abs(quantity_value * entry_price_value)
        notional_method = "derived_abs_quantity_times_entry_price"
        notional_source_fields = tuple(field for field in (quantity_field, entry_price_field) if field)
    else:
        notional_value = None
        notional_method = "unavailable"
        notional_source_fields = ()

    return RowDerivation(
        feature_quantity_before=before_quantity,
        feature_quantity_after=quantity_value,
        feature_quantity_source_field=quantity_field,
        feature_notional_before=before_notional,
        feature_notional_after=notional_value,
        feature_notional_source_fields=notional_source_fields,
        feature_notional_method=notional_method,
        entry_price_source_field=entry_price_field,
        forbidden_fields_present=tuple(sorted(set(forbidden_found))),
    )


def remediate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row_count = len(rows)
    derivations = [derive_row(row) for row in rows]

    forbidden_fields_present = sorted({field for d in derivations for field in d.forbidden_fields_present})
    ambiguous_price_used = any(d.entry_price_source_field == AMBIGUOUS_PRICE_ALIAS for d in derivations)

    quantity_before_null = sum(1 for d in derivations if d.feature_quantity_before is None)
    quantity_after_null = sum(1 for d in derivations if d.feature_quantity_after is None)
    notional_before_null = sum(1 for d in derivations if d.feature_notional_before is None)
    notional_after_null = sum(1 for d in derivations if d.feature_notional_after is None)

    quantity_fields_used = sorted(
        {
            d.feature_quantity_source_field
            for d in derivations
            if d.feature_quantity_source_field and d.feature_quantity_source_field != SELF_ALIAS_QUANTITY
        }
    )
    notional_fields_used = sorted(
        {
            field
            for d in derivations
            for field in d.feature_notional_source_fields
            if field != SELF_ALIAS_NOTIONAL
        }
    )

    quantity_derivable = quantity_after_null < row_count
    notional_derivable = notional_after_null < row_count

    if quantity_fields_used:
        quantity_method = "prefer_existing_then_raw_quantity_alias"
    elif quantity_derivable:
        quantity_method = "no_new_derivation_all_values_already_present"
    else:
        quantity_method = "unavailable"

    notional_new_methods = sorted(
        {
            d.feature_notional_method
            for d in derivations
            if d.feature_notional_method != "unavailable"
            and any(field != SELF_ALIAS_NOTIONAL for field in d.feature_notional_source_fields)
        }
    )
    if notional_new_methods:
        notional_method = "+".join(notional_new_methods)
    elif notional_derivable:
        notional_method = "no_new_derivation_all_values_already_present"
    else:
        notional_method = "unavailable"

    quantity_result = summarize_feature(
        "feature_quantity",
        row_count,
        quantity_before_null,
        quantity_after_null,
        quantity_fields_used,
        quantity_derivable,
        quantity_method,
    )
    notional_result = summarize_feature(
        "feature_notional",
        row_count,
        notional_before_null,
        notional_after_null,
        notional_fields_used,
        notional_derivable,
        notional_method,
    )

    return {
        "row_count": row_count,
        "remediated_features": [quantity_result, notional_result],
        "forbidden_fields_present": forbidden_fields_present,
        "forbidden_fields_used": [],
        "warnings": ["ambiguous_price_alias_used"] if ambiguous_price_used else [],
    }


def summarize_feature(
    feature_name: str,
    row_count: int,
    before_null: int,
    after_null: int,
    fields_used: Sequence[str],
    derivation_possible: bool,
    method: str,
) -> dict[str, Any]:
    before_rate = round(before_null / row_count, 10) if row_count else None
    after_rate = round(after_null / row_count, 10) if row_count else None
    null_rate_delta = (
        round(after_rate - before_rate, 10) if before_rate is not None and after_rate is not None else None
    )
    return {
        "feature_name": feature_name,
        "row_count_before": row_count,
        "row_count_after": row_count,
        "before_null_count": before_null,
        "before_null_rate": before_rate,
        "after_null_count": after_null,
        "after_null_rate": after_rate,
        "null_count_delta": after_null - before_null,
        "null_rate_delta": null_rate_delta,
        "derivation_method": method,
        "source_fields_used": sorted(fields_used),
        "derivation_possible": derivation_possible,
        "blocked_reason": None if derivation_possible else "insufficient_source_fields",
    }


def get_columns(path: Path) -> tuple[list[str], str | None]:
    suffix = path.suffix.lower()
    try:
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

                schema_names = pq.ParquetFile(path).schema.names
                return [str(column) for column in schema_names], None
            except ImportError:
                frame = pd.read_parquet(path)
                return [str(column) for column in frame.columns], None
    except (OSError, ValueError, ImportError) as exc:
        return [], f"schema_read_failed:{exc.__class__.__name__}"
    return [], f"unsupported_source_suffix:{suffix}"


def load_structured_dataset(path: Path, suffix: str) -> tuple[list[dict[str, Any]] | None, list[str], str | None]:
    columns, columns_error = get_columns(path)
    if columns_error:
        return None, [], columns_error
    forbidden_in_schema = sorted(column for column in columns if is_forbidden_field(column))
    permitted_columns = [column for column in columns if column in ALIAS_COLUMNS]
    try:
        import pandas as pd  # type: ignore[import-not-found]

        if suffix == ".csv":
            frame = pd.read_csv(path, usecols=permitted_columns)
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, usecols=permitted_columns)
        elif suffix == ".parquet":
            frame = pd.read_parquet(path, columns=permitted_columns)
        else:
            return None, forbidden_in_schema, f"unsupported_source_suffix:{suffix}"
    except (OSError, ValueError, ImportError) as exc:
        return None, forbidden_in_schema, f"dataset_read_failed:{exc.__class__.__name__}"
    rows = frame.to_dict(orient="records")
    return rows, forbidden_in_schema, None


def load_json_dataset(path: Path) -> tuple[list[dict[str, Any]] | None, list[str], str | None]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [], f"invalid_json:{exc.__class__.__name__}"
    if isinstance(parsed, dict):
        raw_rows: list[Any] = [parsed]
    elif isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        raw_rows = parsed
    else:
        return None, [], "json_schema_not_object_or_record_list"
    forbidden_present: set[str] = set()
    clean_rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        clean_row, forbidden_found = sanitize_row(raw_row)
        forbidden_present.update(forbidden_found)
        clean_rows.append(clean_row)
    return clean_rows, sorted(forbidden_present), None


def load_dataset(path: Path) -> tuple[list[dict[str, Any]] | None, list[str], str | None]:
    if not path.exists() or not path.is_file():
        return None, [], "missing_source_file"
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_json_dataset(path)
    if suffix in {".csv", ".xlsx", ".xls", ".parquet"}:
        return load_structured_dataset(path, suffix)
    return None, [], f"unsupported_source_suffix:{suffix}"


def decide_status(blockers: Sequence[str], warnings: Sequence[str]) -> tuple[str, str]:
    if blockers:
        return "blocked", "feature_missingness_remediation_implementation_blocked"
    if warnings:
        return "warning", "feature_missingness_remediation_implementation_warnings"
    return "ok", "feature_missingness_remediation_implementation_complete_research_only"


def build_non_goals() -> list[str]:
    return [
        "No join with outcome_events, feedback, label, or PnL sources",
        "No active feature contract mutation",
        "No active dataset manifest mutation",
        "No model training",
        "No model promotion",
        "No registry write",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No Freqtrade or RiskManager changes",
        "No orders or private exchange access",
        "No parquet, SQLite, model, or runtime writes outside the two allowed report files",
    ]


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "live_trading_enabled": False,
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
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
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


def render_markdown(report: Mapping[str, Any]) -> str:
    dataset_info = report.get("selected_training_dataset") or {}
    blockers = list_of_strings(report.get("blockers"))
    warnings = list_of_strings(report.get("warnings"))
    return "\n".join(
        [
            "# AI Feature Missingness Remediation Implementation V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Affected features: `{', '.join(list_of_strings(report.get('affected_features')))}`",
            "",
            "This artifact is a research-only, read-only implementation. It derives `feature_notional` and",
            "`feature_quantity` deterministically from permitted raw fields already present on the same dataset",
            "row, and proves before/after missingness from the dataset loaded in memory. It never joins with",
            "outcome, feedback, or label sources, and never reads target/outcome/pnl/label/result/exit_reason/",
            "close_reason/future_ret/win/loss/profit fields.",
            "",
            "## Selected Training Dataset",
            "",
            f"- Path: `{dataset_info.get('path')}`",
            f"- Exists: `{dataset_info.get('exists')}`",
            f"- Row count: `{report.get('row_count')}`",
            "",
            "## Missingness Proof (Before/After)",
            "",
            *markdown_features(report.get("remediated_features", [])),
            "",
            "## Forbidden Fields",
            "",
            f"- Present in source, stripped before derivation: `{report.get('forbidden_fields_present')}`",
            f"- Used in derivation, must always be empty: `{report.get('forbidden_fields_used')}`",
            "",
            "## Blockers",
            "",
            *([f"- `{item}`" for item in blockers] if blockers else ["- None"]),
            "",
            "## Warnings",
            "",
            *([f"- `{item}`" for item in warnings] if warnings else ["- None"]),
            "",
            "## Safety Invariants",
            "",
            "- `operational_authority=false`",
            "- `release_allowed=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "- `changes_risk=false`",
            "- `changes_model=false`",
            "- `can_apply_to_freqtrade=false`",
            "- `can_apply_to_risk_manager=false`",
            "- `can_promote_rules=false`",
            "- `can_promote_model=false`",
            "",
            "## Forbidden Actions (Non-Goals)",
            "",
            *[f"- {item}" for item in report.get("non_goals", [])],
            "",
        ]
    )


def markdown_features(features: Any) -> list[str]:
    rows = list_of_mappings(features)
    if not rows:
        return ["- No remediated features computed."]
    lines = []
    for item in rows:
        lines.append(
            f"- `{item.get('feature_name')}`: before_null_rate=`{item.get('before_null_rate')}`, "
            f"after_null_rate=`{item.get('after_null_rate')}`, null_count_delta=`{item.get('null_count_delta')}`, "
            f"method=`{item.get('derivation_method')}`, source_fields_used=`{item.get('source_fields_used')}`, "
            f"blocked_reason=`{item.get('blocked_reason')}`"
        )
    return lines


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


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]
