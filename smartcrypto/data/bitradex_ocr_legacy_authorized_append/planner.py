"""Deterministic no-write planner for the guarded legacy append."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from smartcrypto.data.bitradex_ocr_legacy_compatibility import (
    build_legacy_compatibility_audit,
)
from smartcrypto.data.bitradex_ocr_legacy_compatibility.contract import (
    load_legacy_contract,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)

from .contract import (
    DEFAULT_TRANSITION_CONTRACT,
    TransitionContract,
    TransitionContractError,
    file_sha256,
    load_transition_contract,
    validate_imported_at_source,
    verify_authorized_source_hashes,
)


DEFAULT_REPORT_JSON = Path("data/reports/bitradex_ocr_legacy_authorized_append_v1.json")
DEFAULT_REPORT_MARKDOWN = Path("data/reports/bitradex_ocr_legacy_authorized_append_v1.md")
PLAN_DECISION = "REQUIRES_EXPLICIT_APPLY_CONFIRMATION"
COLLISION_FIELDS = ("_dedup_key", "_relaxed_dedup_key")

PLAN_SAFETY: dict[str, bool] = {
    "operational_authority": False,
    "apply_executed": False,
    "import_executed": False,
    "write_performed": False,
    "backup_created": False,
    "writes_trader_master": False,
    "writes_xlsx": False,
    "writes_parquet": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "changes_risk": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "updates_qlib": False,
    "updates_ai_shadow": False,
}


def build_authorized_append_plan(
    *,
    project_root: str | Path,
    transition_contract_path: str | Path = DEFAULT_TRANSITION_CONTRACT,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_REPORT_JSON,
    output_markdown: str | Path = DEFAULT_REPORT_MARKDOWN,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    report = _base_report(transition_contract_path, write_report, json_path, markdown_path)
    if write_report:
        output_errors = _validate_report_paths(root, json_path, markdown_path)
        if output_errors:
            return _finish(report, "blocked", "unsafe_report_path", output_errors, False, json_path, markdown_path)

    contract_path, error = _resolve_input(root, transition_contract_path)
    if error:
        return _finish(report, "blocked", error, [error], write_report, json_path, markdown_path)
    try:
        contract = load_transition_contract(contract_path, project_root=root)
    except TransitionContractError as exc:
        return _finish(
            report,
            "blocked",
            "transition_contract_invalid",
            [str(exc)],
            write_report,
            json_path,
            markdown_path,
        )
    _apply_contract_fields(report, contract)
    errors = list(verify_authorized_source_hashes(root, contract))

    source_contract_path, source_contract_error = _resolve_input(root, contract.source_contract)
    if source_contract_error:
        errors.append(f"source_contract:{source_contract_error}")
        source_contract = None
    else:
        try:
            source_contract = load_legacy_contract(source_contract_path)
        except Exception as exc:
            errors.append(f"source_contract_invalid:{type(exc).__name__}")
            source_contract = None

    preview_summary, summary_error = _resolve_input(root, contract.append_state.source_preview_summary)
    preview_csv, csv_error = _resolve_input(root, contract.append_state.source_preview_csv)
    master_xlsx, xlsx_error = _resolve_input(root, contract.pre_state.master_xlsx_path)
    master_parquet, parquet_error = _resolve_input(root, contract.pre_state.master_parquet_path)
    for name, path_error in (
        ("preview_summary", summary_error),
        ("preview_csv", csv_error),
        ("master_xlsx", xlsx_error),
        ("master_parquet", parquet_error),
    ):
        if path_error:
            errors.append(f"{name}:{path_error}")
    if errors:
        return _finish(report, "blocked", "transition_preflight_blocked", errors, write_report, json_path, markdown_path)

    compatibility = build_legacy_compatibility_audit(
        project_root=root,
        contract_path=source_contract_path,
        preview_summary_path=preview_summary,
        preview_csv_path=preview_csv,
        master_xlsx_path=master_xlsx,
        master_parquet_path=master_parquet,
        write_report=False,
    )
    compatibility_errors = _compatibility_errors(compatibility, contract)
    errors.extend(compatibility_errors)

    historical_columns = tuple(source_contract.historical_master_schema.columns) if source_contract else ()
    candidates, header, csv_errors = _read_candidates(
        preview_csv, historical_columns, contract.append_state.candidate_count
    )
    errors.extend(csv_errors)
    imported_at_source = validate_imported_at_source(
        root, contract.imported_at_policy
    )
    candidates, imported_at_evidence = materialize_candidate_imported_at(
        candidates,
        historical_columns,
        contract.imported_at_policy.value_utc,
    )
    if imported_at_evidence["missing_count_after_materialization"] != 0:
        errors.append("candidate_imported_at_materialization_incomplete")
    if imported_at_evidence["unique_count_after_materialization"] != 1:
        errors.append("candidate_imported_at_materialization_not_uniform")
    if imported_at_evidence["exact_value"] != contract.imported_at_policy.value_utc:
        errors.append("candidate_imported_at_materialization_value_mismatch")
    bundle = read_trader_master_readonly(project_root=root, trader_master_path=master_parquet)
    if bundle.report.get("status") != "ok":
        errors.append(f"master_read_blocked:{bundle.report.get('reason')}")
    master_rows = [dict(row) for row in bundle.source_rows]
    if len(master_rows) != contract.pre_state.master_row_count:
        errors.append("master_row_count_mismatch")

    collision_report, collision_errors = _collision_analysis(candidates, master_rows)
    errors.extend(collision_errors)
    synthetic_ids = [_text(row.get("order_id")) for row in candidates]
    synthetic_present = sum(bool(value) for value in synthetic_ids)
    synthetic_unique = len({value for value in synthetic_ids if value})
    if synthetic_present != len(candidates) or synthetic_unique != len(candidates):
        errors.append("synthetic_order_id_evidence_invalid")

    source_hashes = {
        "source_contract_sha256": file_sha256(source_contract_path),
        "transition_contract_sha256": file_sha256(contract_path),
        "imported_at_source_sha256": file_sha256(imported_at_source),
        "preview_summary_sha256": file_sha256(preview_summary),
        "preview_csv_sha256": file_sha256(preview_csv),
        "master_xlsx_sha256": file_sha256(master_xlsx),
        "master_parquet_sha256": file_sha256(master_parquet),
    }
    if source_hashes["master_xlsx_sha256"] != contract.pre_state.master_xlsx_sha256:
        errors.append("master_xlsx_pre_state_hash_mismatch")
    if source_hashes["master_parquet_sha256"] != contract.pre_state.master_parquet_sha256:
        errors.append("master_parquet_pre_state_hash_mismatch")

    candidate_semantic_hash = semantic_rows_sha256(candidates, historical_columns)
    prefix_semantic_hash = semantic_rows_sha256(master_rows, historical_columns)
    canonical_plan = {
        "schema_version": "bitradex_ocr_legacy_authorized_append_plan_v1",
        "transition_id": contract.transition_id,
        "transition_state": contract.transition_state,
        "batch_id": contract.batch_id,
        "source_hashes": source_hashes,
        "authorized_source_sha256": dict(contract.authorized_source_sha256),
        "master_rows_before": len(master_rows),
        "candidate_rows": len(candidates),
        "expected_rows_after": contract.append_state.expected_post_row_count,
        "historical_columns": list(historical_columns),
        "candidate_semantic_sha256": candidate_semantic_hash,
        "master_prefix_semantic_sha256": prefix_semantic_hash,
        "collision_analysis": collision_report,
        "synthetic_order_id_present_count": synthetic_present,
        "synthetic_order_id_unique_count": synthetic_unique,
        "synthetic_order_id_authoritative": False,
        "synthetic_order_id_used_as_v2_identity": False,
        "funding_fee_value": None,
        "funding_assumed_zero": False,
        "funding_derived_as_residual": False,
        "imported_at_policy": {
            "semantic_role": contract.imported_at_policy.semantic_role,
            "source_type": contract.imported_at_policy.source_type,
            "source_relative_path": contract.imported_at_policy.source_relative_path,
            "source_json_path": contract.imported_at_policy.source_json_path,
            "source_file_sha256": contract.imported_at_policy.source_file_sha256,
            "value_utc": contract.imported_at_policy.value_utc,
            "applies_to_all_candidate_rows": True,
            "is_trade_event_timestamp": False,
            "derived_from_trade_fields": False,
            "runtime_clock_allowed": False,
            "filesystem_timestamp_allowed": False,
            "batch_token_timestamp_allowed": False,
        },
        "imported_at_materialization": imported_at_evidence,
        "append_order": contract.append_state.append_order,
        "preserve_existing_prefix": contract.append_state.preserve_existing_prefix,
    }
    plan_sha256 = canonical_sha256(canonical_plan)
    report.update(
        plan=canonical_plan,
        plan_sha256=plan_sha256,
        source_hashes=source_hashes,
        transition_source_hashes_valid=not any("authorized_transition_source" in item for item in errors),
        compatibility_status=compatibility.get("status"),
        compatibility_reason=compatibility.get("reason"),
        preview_header=header,
        candidate_semantic_sha256=candidate_semantic_hash,
        master_prefix_semantic_sha256=prefix_semantic_hash,
        synthetic_order_id_present_count=synthetic_present,
        synthetic_order_id_unique_count=synthetic_unique,
        synthetic_order_id_authoritative=False,
        synthetic_order_id_used_as_v2_identity=False,
        imported_at_source_verified=True,
        imported_at_source_path=contract.imported_at_policy.source_relative_path,
        imported_at_source_sha256=contract.imported_at_policy.source_file_sha256,
        imported_at_source_json_path=contract.imported_at_policy.source_json_path,
        imported_at_utc=contract.imported_at_policy.value_utc,
        imported_at_missing_count_before_materialization=imported_at_evidence[
            "missing_count_before_materialization"
        ],
        imported_at_missing_count_after_materialization=imported_at_evidence[
            "missing_count_after_materialization"
        ],
        imported_at_unique_count_after_materialization=imported_at_evidence[
            "unique_count_after_materialization"
        ],
        materialization_ready=not errors,
        materialization_blockers=[] if not errors else sorted(set(errors)),
        apply_allowed=False,
        **collision_report,
    )
    return _finish(
        report,
        "ok" if not errors else "blocked",
        "authorized_append_plan_ready" if not errors else "authorized_append_plan_blocked",
        errors,
        write_report,
        json_path,
        markdown_path,
    )


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_rows_sha256(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    payload = [[_semantic_value(row.get(column)) for column in columns] for row in rows]
    return canonical_sha256({"columns": list(columns), "rows": payload})


def materialize_candidate_imported_at(
    candidates: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    imported_at_utc: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "imported_at" not in columns:
        raise ValueError("candidate_imported_at_column_missing")
    before_missing = sum(not _text(row.get("imported_at")) for row in candidates)
    materialized = []
    for source in candidates:
        row = {column: source.get(column) for column in columns}
        row["imported_at"] = imported_at_utc
        materialized.append(row)
    values = [_text(row.get("imported_at")) for row in materialized]
    evidence = {
        "row_count": len(materialized),
        "missing_count_before_materialization": before_missing,
        "missing_count_after_materialization": sum(not value for value in values),
        "unique_count_after_materialization": len(set(values)),
        "exact_value": imported_at_utc,
        "source_role": "package_ingestion_provenance_not_trade_event_timestamp",
    }
    return materialized, evidence


def _semantic_value(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _read_candidates(
    path: Path, columns: Sequence[str], expected_count: int
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = [str(item) for item in (reader.fieldnames or [])]
            missing = sorted(set(columns) - set(header))
            if missing:
                errors.append("preview_csv_missing_columns:" + ",".join(missing))
            rows = [{column: row.get(column) for column in columns} for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        return [], [], [f"preview_csv_unreadable:{type(exc).__name__}"]
    if len(rows) != expected_count:
        errors.append(f"candidate_row_count_mismatch:expected={expected_count}:actual={len(rows)}")
    return rows, header, errors


def _collision_analysis(
    candidates: Sequence[Mapping[str, Any]], master_rows: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    report: dict[str, Any] = {}
    errors: list[str] = []
    for field in COLLISION_FIELDS:
        candidate_values = [_text(row.get(field)) for row in candidates]
        nonempty = [value for value in candidate_values if value]
        duplicate_excess = sum(count - 1 for count in Counter(nonempty).values() if count > 1)
        master_values = {_text(row.get(field)) for row in master_rows if _text(row.get(field))}
        overlap = sorted(set(nonempty) & master_values)
        report[f"{field}_internal_duplicate_excess_count"] = duplicate_excess
        report[f"{field}_master_overlap_count"] = len(overlap)
        if len(nonempty) != len(candidates):
            errors.append(f"collision_guard_missing:{field}")
        if duplicate_excess:
            errors.append(f"collision_guard_internal_duplicate:{field}")
        if overlap:
            errors.append(f"collision_guard_master_overlap:{field}")
    source_values = [_text(row.get("source_file")) for row in candidates]
    report["source_file_distinct_count"] = len(set(source_values))
    report["source_file_duplicate_excess_count"] = sum(
        count - 1 for count in Counter(source_values).values() if count > 1
    )
    report["source_file_role"] = "batch_provenance_not_trade_identity"
    if any(not value for value in source_values):
        errors.append("source_file_missing")
    return report, errors


def _compatibility_errors(
    report: Mapping[str, Any], contract: TransitionContract
) -> list[str]:
    checks = {
        "compatibility_status": report.get("status") == "ok",
        "legacy_contract_compatible": report.get("legacy_contract_compatible") is True,
        "candidate_count": report.get("legacy_append_candidate_count") == 504,
        "master_rows_before": report.get("master_rows_before") == 3058,
        "expected_rows_after": report.get("expected_master_rows_after_authorized_append") == 3562,
        "official_import_allowed": report.get("official_import_allowed") is False,
        "import_executed": report.get("import_executed") is False,
        "contract_id": report.get("contract_id") == contract.source_contract_id,
    }
    return [f"legacy_compatibility_gate_failed:{name}" for name, passed in checks.items() if not passed]


def _apply_contract_fields(report: dict[str, Any], contract: TransitionContract) -> None:
    report.update(
        transition_id=contract.transition_id,
        transition_state=contract.transition_state,
        batch_id=contract.batch_id,
        master_rows_before=contract.pre_state.master_row_count,
        candidate_rows=contract.append_state.candidate_count,
        expected_rows_after=contract.append_state.expected_post_row_count,
        funding_fee_value=None,
        funding_assumed_zero=False,
        funding_derived_as_residual=False,
        synthetic_order_id_authoritative=False,
    )


def _base_report(
    contract_path: str | Path, write_report: bool, json_path: Path, markdown_path: Path
) -> dict[str, Any]:
    return {
        "schema_version": "bitradex_ocr_legacy_authorized_append_report_v1",
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": PLAN_DECISION,
        "transition_contract_path": str(contract_path),
        "plan_sha256": None,
        "apply_allowed": False,
        "master_rows_before": 0,
        "candidate_rows": 0,
        "expected_rows_after": 0,
        "validation_errors": [],
        "write_requested": bool(write_report),
        "output_json": str(json_path),
        "output_markdown": str(markdown_path),
        **PLAN_SAFETY,
        "safety_flags": dict(PLAN_SAFETY),
    }


def _finish(
    report: dict[str, Any],
    status: str,
    reason: str,
    errors: Sequence[str],
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    final = dict(report)
    final.update(status=status, reason=reason, validation_errors=sorted(set(errors)))
    if write_report:
        final["write_performed"] = True
        _atomic_write(json_path, json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")
        _atomic_write(markdown_path, render_markdown(final))
    return final


def render_markdown(report: Mapping[str, Any]) -> str:
    errors = report.get("validation_errors") or []
    lines = [
        "# Bitradex OCR Legacy Authorized Append V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Plan SHA-256: `{report.get('plan_sha256')}`",
        f"- Rows: `{report.get('master_rows_before')} + {report.get('candidate_rows')} = {report.get('expected_rows_after')}`",
        f"- Materialization ready: `{report.get('materialization_ready')}`",
        f"- Imported-at source verified: `{report.get('imported_at_source_verified')}`",
        f"- Imported-at UTC: `{report.get('imported_at_utc')}`",
        "",
        "This plan performs no append, backup, lock, runtime write, risk change, or order.",
        "Apply requires the exact plan hash and authorization phrase in a separate command.",
        "",
        "## Validation errors",
        "",
    ]
    lines.extend(f"- `{item}`" for item in errors)
    if not errors:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _resolve_input(root: Path, value: str | Path) -> tuple[Path, str | None]:
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    if _has_symlink_component(candidate):
        return candidate, "input_symlink_rejected"
    resolved = candidate.resolve()
    if not requested.is_absolute():
        try:
            resolved.relative_to(root)
        except ValueError:
            return resolved, "relative_input_outside_project_root"
    if not resolved.is_file():
        return resolved, "input_missing"
    return resolved, None


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _validate_report_paths(root: Path, *paths: Path) -> list[str]:
    allowed = (root / "data/reports").resolve()
    errors: list[str] = []
    for path in paths:
        try:
            path.relative_to(allowed)
        except ValueError:
            errors.append(f"report_path_outside_data_reports:{path}")
        if path.suffix.casefold() not in {".json", ".md"} or _has_symlink_component(path):
            errors.append(f"unsafe_report_path:{path}")
    return errors


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
