"""Deterministic no-write planner for the canonical XLSX transition V2."""

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
from .transaction import build_verified_targets, inspect_legacy_workbook


DEFAULT_REPORT_JSON = Path(
    "data/reports/bitradex_ocr_legacy_canonical_xlsx_transition_v2.json"
)
DEFAULT_REPORT_MARKDOWN = Path(
    "data/reports/bitradex_ocr_legacy_canonical_xlsx_transition_v2.md"
)
PLAN_DECISION = "REQUIRES_EXPLICIT_V2_APPLY_CONFIRMATION"
COLLISION_FIELDS = ("_dedup_key", "_relaxed_dedup_key")

PLAN_SAFETY: dict[str, bool] = {
    "operational_authority": False,
    "apply_executed": False,
    "import_executed": False,
    "write_performed": False,
    "report_write_performed": False,
    "master_write_performed": False,
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


def build_canonical_xlsx_transition_plan(
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
    report = _base_report(
        transition_contract_path,
        write_report,
        json_path,
        markdown_path,
    )
    if write_report:
        output_errors = _validate_report_paths(
            root, json_path, markdown_path
        )
        if output_errors:
            return _finish(
                report,
                "blocked",
                "unsafe_report_path",
                output_errors,
                True,
                json_path,
                markdown_path,
            )

    contract_path, contract_error = _resolve_input(
        root, transition_contract_path
    )
    if contract_error:
        return _finish(
            report,
            "blocked",
            contract_error,
            [contract_error],
            write_report,
            json_path,
            markdown_path,
        )
    try:
        contract = load_transition_contract(
            contract_path, project_root=root
        )
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

    inputs: dict[str, Path] = {}
    for name, value in (
        ("source_contract", contract.source_contract),
        (
            "preview_summary",
            contract.append_state.source_preview_summary,
        ),
        ("preview_csv", contract.append_state.source_preview_csv),
        ("master_xlsx", contract.pre_state.master_xlsx_path),
        ("master_parquet", contract.pre_state.master_parquet_path),
    ):
        path, error = _resolve_input(root, value)
        inputs[name] = path
        if error:
            errors.append(f"{name}:{error}")
    if errors:
        return _finish(
            report,
            "blocked",
            "transition_preflight_blocked",
            errors,
            write_report,
            json_path,
            markdown_path,
        )

    try:
        source_contract = load_legacy_contract(inputs["source_contract"])
        columns = tuple(source_contract.historical_master_schema.columns)
    except Exception as exc:
        errors.append(f"source_contract_invalid:{type(exc).__name__}")
        columns = ()
    if len(columns) != contract.pre_state.canonical_schema_column_count:
        errors.append("canonical_schema_column_count_mismatch")

    try:
        legacy_layout = inspect_legacy_workbook(
            inputs["master_xlsx"],
            data_sheet=contract.pre_state.legacy_data_sheet,
            summary_sheet=contract.pre_state.legacy_summary_sheet,
            expected_header=contract.pre_state.legacy_data_header,
        )
    except RuntimeError as exc:
        legacy_layout = {
            "layout_verified": False,
            "classification": "legacy_ocr_evidence_workbook",
        }
        errors.append(str(exc))
    if legacy_layout.get("data_row_count") != contract.pre_state.master_row_count:
        errors.append("legacy_xlsx_row_count_mismatch")

    source_hashes = {
        "source_contract_sha256": file_sha256(inputs["source_contract"]),
        "transition_contract_sha256": file_sha256(contract_path),
        "preview_summary_sha256": file_sha256(inputs["preview_summary"]),
        "preview_csv_sha256": file_sha256(inputs["preview_csv"]),
        "master_xlsx_sha256": file_sha256(inputs["master_xlsx"]),
        "master_parquet_sha256": file_sha256(inputs["master_parquet"]),
    }
    imported_at_source = validate_imported_at_source(
        root, contract.imported_at_policy
    )
    source_hashes["imported_at_source_sha256"] = file_sha256(
        imported_at_source
    )
    if (
        source_hashes["master_xlsx_sha256"]
        != contract.pre_state.master_xlsx_sha256
    ):
        errors.append("master_xlsx_pre_state_hash_mismatch")
    if (
        source_hashes["master_parquet_sha256"]
        != contract.pre_state.master_parquet_sha256
    ):
        errors.append("master_parquet_pre_state_hash_mismatch")

    preview_errors = _validate_preview_summary(
        inputs["preview_summary"], contract
    )
    errors.extend(preview_errors)
    candidates, preview_header, candidate_errors = _read_candidates(
        inputs["preview_csv"],
        columns,
        contract.append_state.candidate_count,
    )
    errors.extend(candidate_errors)
    candidates, imported_at_evidence = materialize_candidate_imported_at(
        candidates,
        columns,
        contract.imported_at_policy.value_utc,
    )
    if (
        imported_at_evidence["missing_count_after_materialization"] != 0
        or imported_at_evidence["unique_count_after_materialization"] != 1
    ):
        errors.append("candidate_imported_at_materialization_incomplete")

    bundle = read_trader_master_readonly(
        project_root=root,
        trader_master_path=inputs["master_parquet"],
    )
    if bundle.report.get("status") != "ok":
        errors.append(f"master_read_blocked:{bundle.report.get('reason')}")
    master_rows = [dict(row) for row in bundle.source_rows]
    if len(master_rows) != contract.pre_state.master_row_count:
        errors.append("master_row_count_mismatch")
    if tuple(bundle.report.get("trader_master_schema_columns", ())) != columns:
        errors.append("master_parquet_schema_mismatch")

    collision_report, collision_errors = _collision_analysis(
        candidates, master_rows
    )
    errors.extend(collision_errors)
    prefix_hash = semantic_rows_sha256(master_rows, columns)
    tail_hash = semantic_rows_sha256(candidates, columns)
    target_hash = semantic_rows_sha256(
        [*master_rows, *candidates], columns
    )
    if prefix_hash != contract.target_state.expected_prefix_semantic_sha256:
        errors.append("target_prefix_semantic_hash_mismatch")
    if tail_hash != contract.target_state.expected_tail_semantic_sha256:
        errors.append("target_tail_semantic_hash_mismatch")
    if target_hash != contract.target_state.expected_target_semantic_sha256:
        errors.append("target_semantic_hash_mismatch")

    target_evidence: dict[str, Any] = {}
    temporary_path: Path | None = None
    if not errors:
        try:
            with tempfile.TemporaryDirectory(
                prefix="bitradex-canonical-xlsx-v2-"
            ) as temporary:
                temporary_path = Path(temporary)
                built = build_verified_targets(
                    master_parquet=inputs["master_parquet"],
                    destination_directory=temporary_path,
                    contract=contract,
                    candidates=candidates,
                    columns=columns,
                    semantic_hasher=semantic_rows_sha256,
                )
                target_evidence = {
                    "xlsx_row_count": built.xlsx_row_count,
                    "parquet_row_count": built.parquet_row_count,
                    "xlsx_semantic_sha256": built.xlsx_semantic_sha256,
                    "parquet_semantic_sha256": built.parquet_semantic_sha256,
                    "prefix_semantic_sha256": built.prefix_semantic_sha256,
                    "tail_semantic_sha256": built.tail_semantic_sha256,
                    "xlsx_candidate_sha256": built.xlsx_sha256,
                    "parquet_candidate_sha256": built.parquet_sha256,
                    "xlsx_candidate_size": built.xlsx_size,
                    "parquet_candidate_size": built.parquet_size,
                }
        except Exception as exc:
            errors.append(_sanitized_error_code(exc))
    temporary_artifacts_removed = (
        temporary_path is None or not temporary_path.exists()
    )
    if not temporary_artifacts_removed:
        errors.append("planner_temporary_artifacts_not_removed")

    pre_policy_valid, post_policy_valid = _governance_policy_checks(
        root, contract, columns
    )
    if not pre_policy_valid:
        errors.append("pre_transition_policy_invalid")
    if not post_policy_valid:
        errors.append("post_transition_target_policy_invalid")

    target_plan_evidence = {
        key: value
        for key, value in target_evidence.items()
        if key
        in {
            "xlsx_row_count",
            "parquet_row_count",
            "xlsx_semantic_sha256",
            "parquet_semantic_sha256",
            "prefix_semantic_sha256",
            "tail_semantic_sha256",
        }
    }
    canonical_plan = {
        "schema_version": "bitradex_ocr_legacy_canonical_xlsx_plan_v2",
        "transition_id": contract.transition_id,
        "transition_state": contract.transition_state,
        "batch_id": contract.batch_id,
        "source_hashes": source_hashes,
        "authorized_source_sha256": contract.source_hashes(),
        "master_rows_before": len(master_rows),
        "candidate_rows": len(candidates),
        "target_rows": contract.target_state.expected_row_count,
        "canonical_columns": list(columns),
        "pre_xlsx_classification": contract.pre_state.xlsx_classification,
        "canonical_xlsx_sheet": contract.target_state.canonical_xlsx_sheet,
        "prefix_semantic_sha256": prefix_hash,
        "tail_semantic_sha256": tail_hash,
        "target_semantic_sha256": target_hash,
        "imported_at_materialization": imported_at_evidence,
        "collision_analysis": collision_report,
        "target_evidence": target_plan_evidence,
        "pre_transition_policy_valid": pre_policy_valid,
        "post_transition_target_policy_valid": post_policy_valid,
    }
    plan_sha256 = canonical_sha256(canonical_plan)
    cross_format_equal = bool(target_evidence) and (
        target_evidence.get("xlsx_semantic_sha256")
        == target_evidence.get("parquet_semantic_sha256")
        == target_hash
    )
    report.update(
        plan=canonical_plan,
        plan_sha256=plan_sha256,
        source_hashes=source_hashes,
        preview_header=preview_header,
        legacy_xlsx_layout=legacy_layout,
        xlsx_pre_layout_verified=legacy_layout.get("layout_verified") is True,
        canonical_xlsx_target_ready=not errors and cross_format_equal,
        target_rows=contract.target_state.expected_row_count,
        target_schema_column_count=len(columns),
        target_semantic_sha256=target_hash,
        cross_format_semantic_equality=cross_format_equal,
        imported_at_materialization=imported_at_evidence,
        planner_temporary_artifacts_removed=temporary_artifacts_removed,
        pre_transition_policy_valid=pre_policy_valid,
        post_transition_target_policy_valid=post_policy_valid,
        materialization_ready=not errors,
        materialization_blockers=sorted(set(errors)),
        apply_allowed=False,
        **collision_report,
        **target_evidence,
    )
    return _finish(
        report,
        "ok" if not errors else "blocked",
        (
            "canonical_xlsx_transition_plan_ready"
            if not errors
            else "canonical_xlsx_transition_plan_blocked"
        ),
        errors,
        write_report,
        json_path,
        markdown_path,
    )


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_rows_sha256(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> str:
    payload = [
        [_semantic_value(row.get(column)) for column in columns]
        for row in rows
    ]
    return canonical_sha256({"columns": list(columns), "rows": payload})


def materialize_candidate_imported_at(
    candidates: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    imported_at_utc: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "imported_at" not in columns:
        raise ValueError("candidate_imported_at_column_missing")
    before_missing = sum(
        not _text(row.get("imported_at")) for row in candidates
    )
    materialized: list[dict[str, Any]] = []
    for source in candidates:
        row = {column: source.get(column) for column in columns}
        row["imported_at"] = imported_at_utc
        materialized.append(row)
    values = [_text(row.get("imported_at")) for row in materialized]
    return materialized, {
        "row_count": len(materialized),
        "missing_count_before_materialization": before_missing,
        "missing_count_after_materialization": sum(
            not value for value in values
        ),
        "unique_count_after_materialization": len(set(values)),
        "exact_value": imported_at_utc,
        "source_role": (
            "package_ingestion_provenance_not_trade_event_timestamp"
        ),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    errors = report.get("validation_errors") or []
    lines = [
        "# Bitradex OCR Legacy Canonical XLSX Transition V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Plan SHA-256: `{report.get('plan_sha256')}`",
        f"- Legacy XLSX layout verified: `{report.get('xlsx_pre_layout_verified')}`",
        f"- Canonical XLSX target ready: `{report.get('canonical_xlsx_target_ready')}`",
        f"- Target rows: `{report.get('target_rows')}`",
        f"- Target columns: `{report.get('target_schema_column_count')}`",
        f"- Cross-format semantics equal: `{report.get('cross_format_semantic_equality')}`",
        f"- Pre-transition policy valid: `{report.get('pre_transition_policy_valid')}`",
        f"- Post-transition target policy valid: `{report.get('post_transition_target_policy_valid')}`",
        "",
        "The current XLSX is classified as a legacy OCR evidence workbook.",
        "The canonical prefix comes only from the read-only Parquet copy.",
        "This plan performs no master write, import, risk change, runtime update, or order.",
        "",
        "## Validation errors",
        "",
    ]
    lines.extend(f"- `{item}`" for item in errors)
    if not errors:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _read_candidates(
    path: Path,
    columns: Sequence[str],
    expected_count: int,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = [str(item) for item in (reader.fieldnames or [])]
            missing = sorted(set(columns) - set(header))
            rows = [
                {column: row.get(column) for column in columns}
                for row in reader
            ]
    except (OSError, UnicodeError, csv.Error) as exc:
        return [], [], [f"preview_csv_unreadable:{type(exc).__name__}"]
    errors: list[str] = []
    if missing:
        errors.append("preview_csv_missing_columns:" + ",".join(missing))
    if len(rows) != expected_count:
        errors.append(
            "candidate_row_count_mismatch:"
            f"expected={expected_count}:actual={len(rows)}"
        )
    return rows, header, errors


def _validate_preview_summary(
    path: Path, contract: TransitionContract
) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"preview_summary_unreadable:{type(exc).__name__}"]
    source = payload.get("source", {}) if isinstance(payload, Mapping) else {}
    gates = payload.get("gates", {}) if isinstance(payload, Mapping) else {}
    checks = {
        "root": isinstance(payload, Mapping),
        "status": isinstance(payload, Mapping)
        and payload.get("status") == "ok",
        "incoming_rows": isinstance(payload, Mapping)
        and (
            payload.get("incoming_rows")
            == contract.append_state.candidate_count
            or (
                isinstance(source, Mapping)
                and source.get("incoming_row_count")
                == contract.append_state.candidate_count
            )
        ),
        "import_not_executed": isinstance(payload, Mapping)
        and (
            payload.get("import_executed") is False
            or payload.get("official_import_executed") is False
        ),
        "master_preserved": isinstance(payload, Mapping)
        and (
            payload.get("master_preserved") is True
            or (
                isinstance(gates, Mapping)
                and gates.get("master_source_hashes_preserved") is True
            )
        ),
    }
    return [
        f"preview_summary_gate_failed:{name}"
        for name, passed in checks.items()
        if not passed
    ]


def _collision_analysis(
    candidates: Sequence[Mapping[str, Any]],
    master_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    report: dict[str, Any] = {}
    errors: list[str] = []
    for field in COLLISION_FIELDS:
        values = [_text(row.get(field)) for row in candidates]
        nonempty = [value for value in values if value]
        duplicate_excess = sum(
            count - 1
            for count in Counter(nonempty).values()
            if count > 1
        )
        master_values = {
            _text(row.get(field))
            for row in master_rows
            if _text(row.get(field))
        }
        overlap = set(nonempty) & master_values
        report[f"{field}_internal_duplicate_excess_count"] = duplicate_excess
        report[f"{field}_master_overlap_count"] = len(overlap)
        if len(nonempty) != len(candidates):
            errors.append(f"collision_guard_missing:{field}")
        if duplicate_excess:
            errors.append(f"collision_guard_internal_duplicate:{field}")
        if overlap:
            errors.append(f"collision_guard_master_overlap:{field}")
    return report, errors


def _governance_policy_checks(
    root: Path,
    contract: TransitionContract,
    columns: Sequence[str],
) -> tuple[bool, bool]:
    policy_path = (
        root / "config/trader_master_legacy_research_only_policy_v1.json"
    )
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, False
    restricted = (
        "fingerprint_v2_compatible",
        "authoritative_for_identity",
        "authoritative_for_deduplication",
        "authoritative_for_financial_decomposition",
        "bridge_authorized",
        "import_authorized",
        "write_authorized",
        "operational_training_authorized",
        "paper_signal_selection_authorized",
        "live_signal_selection_authorized",
        "risk_decision_authorized",
        "order_execution_authorized",
        "operational_authority",
    )
    safety = all(payload.get(field) is False for field in restricted)
    pre_valid = (
        safety
        and payload.get("dataset_classification")
        == contract.target_state.dataset_classification
        and payload.get("expected_sha256")
        == contract.pre_state.master_parquet_sha256
        and payload.get("expected_row_count")
        == contract.pre_state.master_row_count
        and tuple(payload.get("expected_schema_columns", ()))
        == tuple(columns)
    )
    post_valid = (
        safety
        and contract.target_state.dataset_classification
        == "research_only_legacy_non_v2"
        and contract.target_state.expected_row_count == 3562
        and contract.target_state.canonical_schema_column_count
        == len(columns)
        and contract.safety.operational_authority is False
    )
    return pre_valid, post_valid


def _semantic_value(value: Any) -> str | None:
    if value is None or (
        isinstance(value, float) and math.isnan(value)
    ):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _sanitized_error_code(exc: Exception) -> str:
    value = str(exc).strip()
    if value and all(
        character.isalnum() or character in "_:-=,. "
        for character in value
    ):
        return value.replace(" ", "_")[:240]
    return f"target_build_failed:{type(exc).__name__}"


def _apply_contract_fields(
    report: dict[str, Any], contract: TransitionContract
) -> None:
    report.update(
        transition_id=contract.transition_id,
        transition_state=contract.transition_state,
        batch_id=contract.batch_id,
        master_rows_before=contract.pre_state.master_row_count,
        candidate_rows=contract.append_state.candidate_count,
        target_rows=contract.target_state.expected_row_count,
        pre_xlsx_classification=contract.pre_state.xlsx_classification,
        canonical_xlsx_sheet=contract.target_state.canonical_xlsx_sheet,
    )


def _base_report(
    contract_path: str | Path,
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": (
            "bitradex_ocr_legacy_canonical_xlsx_transition_report_v2"
        ),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": PLAN_DECISION,
        "transition_contract_path": str(contract_path),
        "plan_sha256": None,
        "apply_allowed": False,
        "master_rows_before": 0,
        "candidate_rows": 0,
        "target_rows": 0,
        "target_schema_column_count": 0,
        "xlsx_pre_layout_verified": False,
        "canonical_xlsx_target_ready": False,
        "cross_format_semantic_equality": False,
        "pre_transition_policy_valid": False,
        "post_transition_target_policy_valid": False,
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
    final.update(
        status=status,
        reason=reason,
        validation_errors=sorted(set(errors)),
    )
    if write_report:
        final["write_performed"] = True
        final["report_write_performed"] = True
        _atomic_write(
            json_path,
            json.dumps(
                final,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
        )
        _atomic_write(markdown_path, render_markdown(final))
    return final


def _resolve_input(
    root: Path, value: str | Path
) -> tuple[Path, str | None]:
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
        if (
            path.suffix.casefold() not in {".json", ".md"}
            or _has_symlink_component(path)
        ):
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
    if value is None or (
        isinstance(value, float) and math.isnan(value)
    ):
        return ""
    return str(value).strip()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
