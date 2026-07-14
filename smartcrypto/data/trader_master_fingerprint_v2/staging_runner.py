"""Read-only runner for Trader Master fingerprint V2 staging validation."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.data.trade_file_readonly import read_trade_file

from .fingerprint_spec import (
    CASEFOLDED_FIELDS,
    DECIMAL_QUANTIZATION,
    FINGERPRINT_FIELD_ORDER,
    FINGERPRINT_SPEC_VERSION,
    NORMALIZER_VERSION,
)
from .staging_validator import KillSwitchMonitor, SAFETY_FLAGS, validate_staging_records


SCHEMA_VERSION = "trader_master_staging_validator_v2"
DEFAULT_STAGING_PATH = Path("data/staging/trader_master/trades_staging.csv")
DEFAULT_JSON_REPORT = Path("data/reports/trader_master_staging_validator_v2.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/trader_master_staging_validator_v2.md")
DEFAULT_KILL_SWITCH = Path("data/KILL_SWITCH")
ALLOWED_REPORT_ROOT = Path("data/reports")

REUSED_CONTRACTS = {
    "tabular_loader": "smartcrypto.data.trade_file_readonly.read_trade_file",
    "supported_extensions": [".csv", ".parquet", ".xls", ".xlsx"],
}

REPOSITORY_INVENTORY = {
    "legacy_identity_contracts": [
        "smartcrypto.data.trade_file_readonly.build_dedup_key",
        "smartcrypto.research.paper_closed_trades_readonly_source_contract._row_fingerprint",
        "smartcrypto.learning.paper_autolearning.feedback_store.row_fingerprint_for",
    ],
    "trader_master_writers": [
        "smartcrypto.data.trades_importer.write_master",
        "smartcrypto.learning.paper_autolearning.master_consolidation",
        "scripts.apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master",
        "scripts.sync_ocr_master_v11_phase5_sidecars",
    ],
    "v2_integration_decision": "isolated_read_only_validator_no_writer_replacement",
}


def build_trader_master_staging_validation_report(
    *,
    project_root: str | Path,
    staging_file: str | Path = DEFAULT_STAGING_PATH,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    kill_switch_path: str | Path = DEFAULT_KILL_SWITCH,
    batch_size: int = 1_000,
    write_to_master_requested: bool = False,
    generated_at_utc: str | None = None,
    kill_switch_monitor: KillSwitchMonitor | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    source = _resolve(root, staging_file)
    json_report = _resolve(root, output_json)
    markdown_report = _resolve(root, output_markdown)
    kill_path = _resolve(root, kill_switch_path)
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    base = _base_report(
        root=root,
        source=source,
        json_report=json_report,
        markdown_report=markdown_report,
        generated_at=generated_at,
        write_report=write_report,
        write_to_master_requested=write_to_master_requested,
    )

    write_errors = _validate_report_paths(root, json_report, markdown_report) if write_report else []
    if write_to_master_requested:
        return _finish_blocked(
            base,
            reason="write_to_master_forbidden",
            validation_errors=["write_to_master_forbidden"],
            write_report=write_report,
            write_errors=write_errors,
            json_report=json_report,
            markdown_report=markdown_report,
        )
    if write_errors:
        return _finish_blocked(
            base,
            reason="unsafe_report_output_path",
            validation_errors=write_errors,
            write_report=False,
            write_errors=write_errors,
            json_report=json_report,
            markdown_report=markdown_report,
        )

    monitor = kill_switch_monitor or KillSwitchMonitor(kill_path)
    if monitor.check(force=True):
        aborted = _finish_blocked(
            base | {"partial_artifact_status": "aborted"},
            reason="kill_switch_active_at_boot",
            validation_errors=["kill_switch_active_at_boot"],
            write_report=write_report,
            write_errors=[],
            json_report=json_report,
            markdown_report=markdown_report,
        )
        return aborted

    if not source.exists() or not source.is_file():
        return _finish_blocked(
            base,
            reason="staging_source_missing",
            validation_errors=[f"staging_source_missing:{_display_path(source, root)}"],
            write_report=write_report,
            write_errors=[],
            json_report=json_report,
            markdown_report=markdown_report,
        )

    source_sha = _file_sha256(source)
    try:
        frame = read_trade_file(source)
    except Exception as exc:
        return _finish_blocked(
            base,
            reason="staging_source_unreadable",
            validation_errors=[f"staging_source_unreadable:{type(exc).__name__}:{exc}"],
            write_report=write_report,
            write_errors=[],
            json_report=json_report,
            markdown_report=markdown_report,
        )

    duplicate_columns = sorted(
        {str(column) for column in frame.columns[frame.columns.duplicated(keep=False)]}
    )
    if duplicate_columns:
        return _finish_blocked(
            base | {"source_sha256": source_sha, "duplicate_columns": duplicate_columns},
            reason="duplicate_staging_columns",
            validation_errors=[f"duplicate_staging_columns:{duplicate_columns}"],
            write_report=write_report,
            write_errors=[],
            json_report=json_report,
            markdown_report=markdown_report,
        )

    records = frame.to_dict(orient="records")
    run_id = f"staging-v2-{source_sha[:16]}"
    validation = validate_staging_records(
        records,
        source_file=_display_path(source, root),
        source_sha256=source_sha,
        ingestion_run_id=run_id,
        batch_size=batch_size,
        kill_switch=monitor,
    )
    report = {
        **base,
        **validation,
        "source_sha256": source_sha,
        "ingestion_run_id": run_id,
        "source_column_count": len(frame.columns),
        "source_columns": sorted(str(column) for column in frame.columns),
        "duplicate_columns": [],
        "write_requested": bool(write_report),
        "write_performed": False,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    return _maybe_write(report, write_report, json_report, markdown_report)


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Trader Master Fingerprint Spec V2 - Staging Validation",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Source: `{report.get('staging_file')}`",
            f"- Raw rows: `{report.get('raw_row_count', 0)}`",
            f"- Accepted rows: `{report.get('accepted_row_count', 0)}`",
            f"- Quarantined rows: `{report.get('quarantined_row_count', 0)}`",
            f"- Staging duplicates: `{report.get('staging_duplicate_count', 0)}`",
            f"- Fingerprint collisions: `{report.get('observed_fingerprint_collision_count', 0)}`",
            f"- Canonical trade ID coverage: `{report.get('canonical_trade_id_coverage', 0)}%`",
            "- Trader Master write performed: `false`",
            "- Exchange orders sent: `false`",
            "",
            "## Validation errors",
            "",
            *[f"- `{item}`" for item in report.get("validation_errors", [])],
            "",
            "This report is research-only and cannot import or promote staging rows.",
            "",
        ]
    )


def _base_report(
    *,
    root: Path,
    source: Path,
    json_report: Path,
    markdown_report: Path,
    generated_at: str,
    write_report: bool,
    write_to_master_requested: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "staging_file": _display_path(source, root),
        "status": "blocked",
        "reason": "not_evaluated",
        "partial_artifact_status": "none",
        "fingerprint_field_order": list(FINGERPRINT_FIELD_ORDER),
        "decimal_quantization": DECIMAL_QUANTIZATION,
        "casefolded_fields": list(CASEFOLDED_FIELDS),
        "null_representation": "json_null",
        "canonical_serialization": "utf8_json_fixed_field_order_compact",
        "reused_contracts": REUSED_CONTRACTS,
        "repository_inventory": REPOSITORY_INVENTORY,
        "write_requested": bool(write_report),
        "write_performed": False,
        "write_to_master_requested": bool(write_to_master_requested),
        "output_paths": {
            "json": _display_path(json_report, root),
            "markdown": _display_path(markdown_report, root),
        },
        "raw_row_count": 0,
        "accepted_row_count": 0,
        "quarantined_row_count": 0,
        "staging_duplicate_count": 0,
        "duplicate_canonical_trade_id_count": 0,
        "duplicate_fingerprint_count": 0,
        "observed_fingerprint_collision_count": 0,
        "canonical_trade_id_coverage": 0.0,
        "fingerprint_deterministic": True,
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
        "validation_errors": [],
        "blockers": [],
        "warnings": [],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _finish_blocked(
    report: dict[str, Any],
    *,
    reason: str,
    validation_errors: list[str],
    write_report: bool,
    write_errors: list[str],
    json_report: Path,
    markdown_report: Path,
) -> dict[str, Any]:
    report.update(
        {
            "status": "blocked",
            "reason": reason,
            "validation_errors": sorted(set(validation_errors)),
            "blockers": sorted(set(validation_errors)),
            "warnings": sorted(set(write_errors)),
        }
    )
    return _maybe_write(report, write_report, json_report, markdown_report)


def _maybe_write(
    report: dict[str, Any],
    write_report: bool,
    json_report: Path,
    markdown_report: Path,
) -> dict[str, Any]:
    if not write_report:
        return report
    final = dict(report)
    final["write_performed"] = True
    _atomic_write_text(
        json_report,
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
    )
    _atomic_write_text(markdown_report, render_markdown(final))
    return final


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_report_paths(root: Path, *paths: Path) -> list[str]:
    allowed_root = (root / ALLOWED_REPORT_ROOT).resolve()
    errors: list[str] = []
    for path in paths:
        try:
            path.resolve().relative_to(allowed_root)
        except ValueError:
            errors.append(f"report_path_outside_data_reports:{path}")
        if path.suffix.casefold() not in {".json", ".md"}:
            errors.append(f"unsupported_report_extension:{path.suffix}")
    return sorted(set(errors))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(root: Path, value: str | Path) -> Path:
    """Resolve a runner input path without changing the filesystem."""

    return _resolve(root, value)


def validate_report_output_paths(root: Path, *paths: Path) -> list[str]:
    """Expose the canonical report-path boundary to source adapters."""

    return _validate_report_paths(root, *paths)


def maybe_write_validation_report(
    report: dict[str, Any],
    *,
    write_report: bool,
    json_report: Path,
    markdown_report: Path,
) -> dict[str, Any]:
    """Write only canonical JSON/Markdown reports when explicitly requested."""

    return _maybe_write(report, write_report, json_report, markdown_report)
