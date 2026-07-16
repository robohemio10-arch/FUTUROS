"""Explicit apply/verify entrypoints for the guarded legacy append."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract import (
    AUTHORIZATION_PHRASE,
    DEFAULT_TRANSITION_CONTRACT,
    TransitionContractError,
    file_sha256,
    load_transition_contract,
)
from .planner import (
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
    build_authorized_append_plan,
    materialize_candidate_imported_at,
    semantic_rows_sha256,
)
from .transaction import (
    TransitionLock,
    build_verified_candidates,
    create_verified_backups,
    restore_from_backups,
)


FaultHook = Callable[[str], None]


def apply_authorized_append(
    *,
    project_root: str | Path,
    expected_plan_sha256: str | None,
    authorization_phrase: str | None,
    transition_contract_path: str | Path = DEFAULT_TRANSITION_CONTRACT,
    output_json: str | Path = DEFAULT_REPORT_JSON,
    output_markdown: str | Path = DEFAULT_REPORT_MARKDOWN,
    fault_hook: FaultHook | None = None,
) -> dict[str, Any]:
    """Execute the guarded transaction only after all explicit preflight gates."""

    root = Path(project_root).resolve()
    base = _apply_base()
    try:
        contract_path = _resolve(root, transition_contract_path)
        contract = load_transition_contract(contract_path, project_root=root)
    except TransitionContractError as exc:
        return _blocked(base, "transition_contract_invalid", [str(exc)])
    if contract.transition_state == "failed_pre_replace_superseded":
        return _blocked(
            base,
            "transition_v1_superseded_after_xlsx_layout_mismatch",
        )
    if authorization_phrase != AUTHORIZATION_PHRASE:
        return _blocked(base, "authorization_phrase_invalid")
    if not _is_sha256(expected_plan_sha256):
        return _blocked(base, "expected_plan_sha256_invalid")
    plan = build_authorized_append_plan(
        project_root=root,
        transition_contract_path=transition_contract_path,
        write_report=False,
    )
    if plan.get("status") != "ok":
        return _blocked(base, "recomputed_plan_blocked", plan.get("validation_errors", []))
    if plan.get("plan_sha256") != expected_plan_sha256:
        return _blocked(base, "plan_sha256_mismatch")
    if plan.get("materialization_ready") is not True:
        return _blocked(base, "candidate_materialization_blocked", plan.get("materialization_blockers", []))

    base.update(
        transition_id=contract.transition_id,
        plan_sha256=expected_plan_sha256,
        source_hashes=dict(plan.get("source_hashes") or {}),
        before_hashes={
            "xlsx": file_sha256(root / contract.pre_state.master_xlsx_path),
            "parquet": file_sha256(root / contract.pre_state.master_parquet_path),
        },
        master_rows_before=contract.pre_state.master_row_count,
        appended_rows=contract.append_state.candidate_count,
        master_rows_after=contract.append_state.expected_post_row_count,
        schema_column_count=contract.pre_state.master_schema_column_count,
        imported_at_source_verified=plan.get("imported_at_source_verified"),
        imported_at_utc=plan.get("imported_at_utc"),
        imported_at_missing_count_after_materialization=plan.get(
            "imported_at_missing_count_after_materialization"
        ),
        imported_at_unique_count_after_materialization=plan.get(
            "imported_at_unique_count_after_materialization"
        ),
    )
    lock = TransitionLock(
        root / "data/locks/bitradex_ocr_legacy_append_v1.lock",
        contract.transition_id,
    )
    try:
        lock.acquire()
    except FileExistsError:
        return _blocked(base, "transition_lock_exists")

    master_xlsx = root / contract.pre_state.master_xlsx_path
    master_parquet = root / contract.pre_state.master_parquet_path
    backup = None
    replaced = False
    candidate_evidence = None
    try:
        base["apply_executed"] = True
        _fault(fault_hook, "lock_acquired")
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = create_verified_backups(root=root, contract=contract, run_id=run_id)
        base.update(
            backup_created=True,
            backup_paths={
                "xlsx": backup.xlsx_path.relative_to(root).as_posix(),
                "parquet": backup.parquet_path.relative_to(root).as_posix(),
            },
            backup_hashes={
                "xlsx": backup.xlsx_sha256,
                "parquet": backup.parquet_sha256,
            },
            backup_sizes={
                "xlsx": backup.xlsx_size,
                "parquet": backup.parquet_size,
            },
        )
        _fault(fault_hook, "backup_verified")
        candidates = _read_candidates(
            root / contract.append_state.source_preview_csv,
            plan["plan"]["historical_columns"],
        )
        plan_imported_at = str(plan["plan"]["imported_at_policy"]["value_utc"])
        if plan_imported_at != contract.imported_at_policy.value_utc:
            raise RuntimeError("plan_imported_at_contract_mismatch")
        candidates, imported_at_evidence = materialize_candidate_imported_at(
            candidates,
            plan["plan"]["historical_columns"],
            plan_imported_at,
        )
        if imported_at_evidence["missing_count_after_materialization"] != 0:
            raise RuntimeError("candidate_imported_at_missing_before_swap")
        if imported_at_evidence["unique_count_after_materialization"] != 1:
            raise RuntimeError("candidate_imported_at_not_uniform_before_swap")
        if imported_at_evidence["exact_value"] != plan_imported_at:
            raise RuntimeError("candidate_imported_at_value_mismatch_before_swap")
        if imported_at_evidence != plan["plan"]["imported_at_materialization"]:
            raise RuntimeError("candidate_imported_at_evidence_drift")
        base["imported_at_materialization"] = imported_at_evidence
        candidate_evidence = build_verified_candidates(
            root=root,
            contract=contract,
            candidates=candidates,
            columns=plan["plan"]["historical_columns"],
        )
        base.update(
            candidate_hashes={
                "xlsx_semantic_sha256": candidate_evidence.xlsx_semantic_sha256,
                "parquet_semantic_sha256": candidate_evidence.parquet_semantic_sha256,
                "tail_semantic_sha256": candidate_evidence.candidate_tail_semantic_sha256,
            },
            candidate_sizes={
                "xlsx": candidate_evidence.xlsx_size,
                "parquet": candidate_evidence.parquet_size,
            },
            candidate_row_counts={
                "xlsx": candidate_evidence.xlsx_row_count,
                "parquet": candidate_evidence.parquet_row_count,
            },
        )
        _fault(fault_hook, "candidates_verified")
        os.replace(candidate_evidence.parquet_path, master_parquet)
        replaced = True
        _fault(fault_hook, "parquet_replaced")
        os.replace(candidate_evidence.xlsx_path, master_xlsx)
        _fault(fault_hook, "xlsx_replaced")
        verification = verify_authorized_append(
            project_root=root,
            transition_contract_path=transition_contract_path,
        )
        if verification.get("status") != "ok":
            raise RuntimeError("post_apply_attestation_failed")
        base["post_apply_verification"] = verification
        _fault(fault_hook, "post_apply_verified")
        base.update(
            status="ok",
            reason="authorized_append_committed",
            decision="AUTHORIZED_APPEND_COMMITTED",
            transaction_committed=True,
            import_executed=True,
            write_performed=True,
            writes_trader_master=True,
            writes_xlsx=True,
            writes_parquet=True,
            after_hashes={
                "xlsx": file_sha256(master_xlsx),
                "parquet": file_sha256(master_parquet),
            },
        )
        _write_apply_reports(root, output_json, output_markdown, base)
        return base
    except Exception as exc:
        base.update(
            status="blocked",
            reason="authorized_append_transaction_failed",
            decision="APPLY_BLOCKED",
            transaction_committed=False,
            import_executed=False,
            write_performed=False,
            writes_trader_master=False,
            writes_xlsx=False,
            writes_parquet=False,
            original_error=type(exc).__name__,
        )
        if replaced and backup is not None:
            base["rollback_attempted"] = True
            base["rollback_succeeded"] = restore_from_backups(
                master_xlsx=master_xlsx,
                master_parquet=master_parquet,
                backup=backup,
            )
        return base
    finally:
        if candidate_evidence is not None:
            candidate_evidence.xlsx_path.unlink(missing_ok=True)
            candidate_evidence.parquet_path.unlink(missing_ok=True)
        lock.release()


def verify_authorized_append(
    *,
    project_root: str | Path,
    transition_contract_path: str | Path = DEFAULT_TRANSITION_CONTRACT,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    try:
        contract = load_transition_contract(
            _resolve(root, transition_contract_path),
            project_root=root,
        )
    except TransitionContractError as exc:
        return {"status": "blocked", "reason": "transition_contract_invalid", "errors": [str(exc)]}
    from .transaction import _read_parquet_rows, _read_xlsx_rows

    columns = _load_historical_columns(root, contract.source_contract)
    parquet_rows = _read_parquet_rows(root / contract.pre_state.master_parquet_path, columns)
    xlsx_rows = _read_xlsx_rows(
        root / contract.pre_state.master_xlsx_path,
        columns,
        contract.append_state.xlsx_sheet,
    )
    source_rows = _read_candidates(
        root / contract.append_state.source_preview_csv,
        columns,
    )
    source_rows, imported_at_evidence = materialize_candidate_imported_at(
        source_rows,
        columns,
        contract.imported_at_policy.value_utc,
    )
    prefix_length = contract.pre_state.master_row_count
    expected_count = contract.append_state.expected_post_row_count
    parquet_semantic_hash = semantic_rows_sha256(parquet_rows, columns)
    xlsx_semantic_hash = semantic_rows_sha256(xlsx_rows, columns)
    source_tail_hash = semantic_rows_sha256(source_rows, columns)
    parquet_tail_hash = semantic_rows_sha256(parquet_rows[prefix_length:], columns)
    xlsx_tail_hash = semantic_rows_sha256(xlsx_rows[prefix_length:], columns)
    prefix_semantics_equal = semantic_rows_sha256(
        parquet_rows[:prefix_length], columns
    ) == semantic_rows_sha256(xlsx_rows[:prefix_length], columns)
    row_counts_ok = len(parquet_rows) == len(xlsx_rows) == expected_count
    schema_ok = len(columns) == contract.pre_state.master_schema_column_count
    semantic_equality = parquet_semantic_hash == xlsx_semantic_hash
    tail_semantics_equal = source_tail_hash == parquet_tail_hash == xlsx_tail_hash
    verified = row_counts_ok and schema_ok and prefix_semantics_equal and semantic_equality and tail_semantics_equal
    return {
        "status": "ok" if verified else "blocked",
        "reason": "post_append_attestation_ok" if verified else "post_append_attestation_failed",
        "master_xlsx_row_count": len(xlsx_rows),
        "master_parquet_row_count": len(parquet_rows),
        "schema_column_count": len(columns),
        "schema_columns": columns,
        "prefix_semantics_equal": prefix_semantics_equal,
        "tail_semantics_equal": tail_semantics_equal,
        "cross_format_semantic_equality": semantic_equality,
        "xlsx_semantic_sha256": xlsx_semantic_hash,
        "parquet_semantic_sha256": parquet_semantic_hash,
        "source_tail_semantic_sha256": source_tail_hash,
        "imported_at_source_verified": True,
        "imported_at_utc": contract.imported_at_policy.value_utc,
        "imported_at_materialization": imported_at_evidence,
        "writes_runtime": False,
        "writes_sqlite": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def _load_historical_columns(root: Path, source_contract_path: str) -> list[str]:
    payload = json.loads((root / source_contract_path).read_text(encoding="utf-8-sig"))
    return [str(item) for item in payload["historical_master_schema"]["columns"]]


def _read_candidates(path: Path, columns: list[str]) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {column: row.get(column) for column in columns}
            for row in csv.DictReader(handle)
        ]


def _write_apply_reports(
    root: Path, output_json: str | Path, output_markdown: str | Path, report: Mapping[str, Any]
) -> None:
    from .planner import _atomic_write, render_markdown

    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    _atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")
    _atomic_write(markdown_path, render_markdown(report))
    attestation = root / "data/reports/trader_master_legacy_post_append_attestation_v1.json"
    _atomic_write(attestation, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _apply_base() -> dict[str, Any]:
    return {
        "schema_version": "bitradex_ocr_legacy_authorized_append_report_v1",
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "APPLY_BLOCKED",
        "transaction_committed": False,
        "apply_executed": False,
        "import_executed": False,
        "write_performed": False,
        "backup_created": False,
        "rollback_attempted": False,
        "rollback_succeeded": False,
        "writes_trader_master": False,
        "writes_xlsx": False,
        "writes_parquet": False,
        "writes_sqlite": False,
        "writes_runtime": False,
        "funding_fee_value": None,
        "funding_assumed_zero": False,
        "funding_derived_as_residual": False,
        "synthetic_order_id_authoritative": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "updates_qlib": False,
        "updates_ai_shadow": False,
    }


def _blocked(base: dict[str, Any], reason: str, errors: Any = None) -> dict[str, Any]:
    result = dict(base)
    result.update(reason=reason, validation_errors=list(errors or []))
    return result


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _is_sha256(value: str | None) -> bool:
    if value is None or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _fault(hook: FaultHook | None, stage: str) -> None:
    if hook is not None:
        hook(stage)
