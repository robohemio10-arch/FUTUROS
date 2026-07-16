"""Explicit apply and verification for the canonical XLSX transition V2."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
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
    build_canonical_xlsx_transition_plan,
    materialize_candidate_imported_at,
    render_markdown,
    semantic_rows_sha256,
)
from .transaction import (
    TransitionLock,
    _read_parquet_rows,
    _read_xlsx_rows,
    build_verified_targets,
    create_verified_backups,
    restore_from_backups,
)


FaultHook = Callable[[str], None]
SAFE_ERROR = re.compile(r"^[A-Za-z0-9_.:,=\- ]{1,240}$")


def apply_canonical_xlsx_transition(
    *,
    project_root: str | Path,
    expected_plan_sha256: str | None,
    authorization_phrase: str | None,
    transition_contract_path: str | Path = DEFAULT_TRANSITION_CONTRACT,
    output_json: str | Path = DEFAULT_REPORT_JSON,
    output_markdown: str | Path = DEFAULT_REPORT_MARKDOWN,
    fault_hook: FaultHook | None = None,
) -> dict[str, Any]:
    """Execute V2 only after exact plan and authorization gates."""

    root = Path(project_root).resolve()
    base = _apply_base()
    try:
        contract = load_transition_contract(
            _resolve(root, transition_contract_path),
            project_root=root,
        )
    except TransitionContractError as exc:
        return _blocked(
            base, "transition_contract_invalid", [str(exc)]
        )
    base["transition_id"] = contract.transition_id
    if authorization_phrase != AUTHORIZATION_PHRASE:
        return _blocked(base, "authorization_phrase_invalid")
    if not _is_sha256(expected_plan_sha256):
        return _blocked(base, "expected_plan_sha256_invalid")
    plan = build_canonical_xlsx_transition_plan(
        project_root=root,
        transition_contract_path=transition_contract_path,
        write_report=False,
    )
    if plan.get("status") != "ok":
        return _blocked(
            base,
            "recomputed_plan_blocked",
            plan.get("validation_errors", []),
        )
    if plan.get("plan_sha256") != expected_plan_sha256:
        return _blocked(base, "plan_sha256_mismatch")
    if plan.get("materialization_ready") is not True:
        return _blocked(
            base,
            "candidate_materialization_blocked",
            plan.get("materialization_blockers", []),
        )

    master_xlsx = root / contract.pre_state.master_xlsx_path
    master_parquet = root / contract.pre_state.master_parquet_path
    before_hashes = {
        "xlsx": file_sha256(master_xlsx),
        "parquet": file_sha256(master_parquet),
    }
    base.update(
        plan_sha256=expected_plan_sha256,
        before_hashes=before_hashes,
        master_rows_before=contract.pre_state.master_row_count,
        appended_rows=contract.append_state.candidate_count,
        master_rows_after=contract.target_state.expected_row_count,
        schema_column_count=contract.target_state.canonical_schema_column_count,
    )
    lock = TransitionLock(
        root
        / "data/locks/bitradex_ocr_legacy_canonical_xlsx_transition_v2.lock",
        contract.transition_id,
    )
    try:
        lock.acquire()
    except FileExistsError:
        return _blocked(base, "transition_lock_exists")

    backup = None
    target_directory: Path | None = None
    target = None
    failed_stage = "lock_acquired"
    try:
        base["apply_executed"] = True
        _fault(fault_hook, failed_stage)
        failed_stage = "backup_creation"
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = create_verified_backups(
            root=root, contract=contract, run_id=run_id
        )
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
        failed_stage = "backup_verified"
        _fault(fault_hook, failed_stage)
        columns = list(plan["plan"]["canonical_columns"])
        candidates = _read_candidates(
            root / contract.append_state.source_preview_csv, columns
        )
        candidates, imported_at = materialize_candidate_imported_at(
            candidates,
            columns,
            contract.imported_at_policy.value_utc,
        )
        if imported_at != plan["plan"]["imported_at_materialization"]:
            raise RuntimeError("candidate_imported_at_evidence_drift")

        failed_stage = "target_build"
        target_directory = Path(
            tempfile.mkdtemp(
                prefix=".bitradex-canonical-xlsx-v2-",
                dir=master_parquet.parent,
            )
        )
        target = build_verified_targets(
            master_parquet=master_parquet,
            destination_directory=target_directory,
            contract=contract,
            candidates=candidates,
            columns=columns,
            semantic_hasher=semantic_rows_sha256,
        )
        base.update(
            candidate_hashes={
                "xlsx_sha256": target.xlsx_sha256,
                "parquet_sha256": target.parquet_sha256,
                "xlsx_semantic_sha256": target.xlsx_semantic_sha256,
                "parquet_semantic_sha256": target.parquet_semantic_sha256,
                "prefix_semantic_sha256": target.prefix_semantic_sha256,
                "tail_semantic_sha256": target.tail_semantic_sha256,
            },
            candidate_row_counts={
                "xlsx": target.xlsx_row_count,
                "parquet": target.parquet_row_count,
            },
        )
        failed_stage = "targets_verified"
        _fault(fault_hook, failed_stage)

        base["master_replace_started"] = True
        base["master_write_performed"] = True
        failed_stage = "parquet_replace"
        os.replace(target.parquet_path, master_parquet)
        _fault(fault_hook, "parquet_replaced")
        failed_stage = "xlsx_replace"
        os.replace(target.xlsx_path, master_xlsx)
        _fault(fault_hook, "xlsx_replaced")
        failed_stage = "post_apply_attestation"
        verification = verify_canonical_xlsx_transition(
            project_root=root,
            transition_contract_path=transition_contract_path,
        )
        if verification.get("status") != "ok":
            raise RuntimeError("post_apply_attestation_failed")
        _fault(fault_hook, "post_apply_verified")
        base.update(
            status="ok",
            reason="canonical_xlsx_transition_committed",
            decision="CANONICAL_XLSX_TRANSITION_COMMITTED",
            failed_stage=None,
            error_code=None,
            transaction_committed=True,
            import_executed=True,
            writes_trader_master=True,
            writes_xlsx=True,
            writes_parquet=True,
            post_apply_verification=verification,
            after_hashes=_master_hashes(master_xlsx, master_parquet),
            masters_preserved=False,
            report_write_performed=True,
            write_performed=True,
        )
        _persist_report(
            root, output_json, output_markdown, base
        )
        return base
    except Exception as exc:
        base.update(
            status="blocked",
            reason="canonical_xlsx_transition_failed",
            decision="APPLY_BLOCKED",
            failed_stage=failed_stage,
            error_code=_sanitized_error_code(exc),
            transaction_committed=False,
            import_executed=False,
            writes_trader_master=False,
            writes_xlsx=False,
            writes_parquet=False,
        )
        if base["master_replace_started"] and backup is not None:
            base["rollback_attempted"] = True
            base["rollback_succeeded"] = restore_from_backups(
                master_xlsx=master_xlsx,
                master_parquet=master_parquet,
                backup=backup,
            )
        after = _master_hashes(master_xlsx, master_parquet)
        base["after_failure_hashes"] = after
        base["masters_preserved"] = after == before_hashes
        try:
            base["report_write_performed"] = True
            base["write_performed"] = True
            _persist_report(
                root, output_json, output_markdown, base
            )
        except OSError:
            base["report_write_performed"] = False
            base["write_performed"] = False
        return base
    finally:
        if target_directory is not None:
            shutil.rmtree(target_directory, ignore_errors=True)
        lock.release()


def verify_canonical_xlsx_transition(
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
        return {
            "status": "blocked",
            "reason": "transition_contract_invalid",
            "validation_errors": [str(exc)],
        }
    columns = _load_columns(root, contract.source_contract)
    try:
        parquet_rows = _read_parquet_rows(
            root / contract.pre_state.master_parquet_path, columns
        )
        xlsx_rows = _read_xlsx_rows(
            root / contract.pre_state.master_xlsx_path,
            columns,
            contract.target_state.canonical_xlsx_sheet,
        )
    except RuntimeError as exc:
        return {
            "status": "blocked",
            "reason": "post_transition_artifact_invalid",
            "validation_errors": [str(exc)],
        }
    parquet_hash = semantic_rows_sha256(parquet_rows, columns)
    xlsx_hash = semantic_rows_sha256(xlsx_rows, columns)
    verified = (
        len(parquet_rows)
        == len(xlsx_rows)
        == contract.target_state.expected_row_count
        and len(columns)
        == contract.target_state.canonical_schema_column_count
        and parquet_hash
        == xlsx_hash
        == contract.target_state.expected_target_semantic_sha256
    )
    return {
        "status": "ok" if verified else "blocked",
        "reason": (
            "post_transition_attestation_ok"
            if verified
            else "post_transition_attestation_failed"
        ),
        "master_xlsx_row_count": len(xlsx_rows),
        "master_parquet_row_count": len(parquet_rows),
        "schema_column_count": len(columns),
        "canonical_xlsx_sheet": contract.target_state.canonical_xlsx_sheet,
        "cross_format_semantic_equality": parquet_hash == xlsx_hash,
        "xlsx_semantic_sha256": xlsx_hash,
        "parquet_semantic_sha256": parquet_hash,
        "operational_authority": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def _read_candidates(
    path: Path, columns: list[str]
) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {column: row.get(column) for column in columns}
            for row in csv.DictReader(handle)
        ]


def _load_columns(root: Path, source_contract_path: str) -> list[str]:
    payload = json.loads(
        (root / source_contract_path).read_text(encoding="utf-8-sig")
    )
    return [
        str(item)
        for item in payload["historical_master_schema"]["columns"]
    ]


def _persist_report(
    root: Path,
    output_json: str | Path,
    output_markdown: str | Path,
    report: Mapping[str, Any],
) -> None:
    json_path = _safe_report_path(root, output_json, ".json")
    markdown_path = _safe_report_path(root, output_markdown, ".md")
    _atomic_write(
        json_path,
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
    )
    _atomic_write(markdown_path, render_markdown(report))


def _safe_report_path(
    root: Path, value: str | Path, suffix: str
) -> Path:
    path = _resolve(root, value)
    allowed = (root / "data/reports").resolve()
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise OSError("unsafe_report_path") from exc
    if path.suffix.casefold() != suffix or path.is_symlink():
        raise OSError("unsafe_report_path")
    return path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
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


def _master_hashes(xlsx: Path, parquet: Path) -> dict[str, str | None]:
    return {
        "xlsx": file_sha256(xlsx) if xlsx.is_file() else None,
        "parquet": file_sha256(parquet) if parquet.is_file() else None,
    }


def _apply_base() -> dict[str, Any]:
    return {
        "schema_version": (
            "bitradex_ocr_legacy_canonical_xlsx_transition_report_v2"
        ),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "APPLY_BLOCKED",
        "transaction_committed": False,
        "apply_executed": False,
        "import_executed": False,
        "write_performed": False,
        "report_write_performed": False,
        "master_write_performed": False,
        "master_replace_started": False,
        "backup_created": False,
        "rollback_attempted": False,
        "rollback_succeeded": False,
        "failed_stage": None,
        "error_code": None,
        "masters_preserved": True,
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


def _blocked(
    base: dict[str, Any], reason: str, errors: Any = None
) -> dict[str, Any]:
    result = dict(base)
    result.update(reason=reason, validation_errors=list(errors or []))
    return result


def _sanitized_error_code(exc: Exception) -> str:
    value = str(exc).strip()
    if value and SAFE_ERROR.fullmatch(value):
        return value.replace(" ", "_")
    return f"transaction_error:{type(exc).__name__}"


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
