"""Fail-closed orchestration for sanitized evidence bundles."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import SCHEMA_VERSION, ScanResult
from .scanner import normalize_allowlist, scan_directory, scan_source, scan_zip

DEFAULT_REPORT_PATH = Path("data/reports/evidence_bundle_secret_redaction_gate_v1.json")
ALLOWED_REPORT_ROOT = Path("data/reports")
ALLOWED_BUNDLE_ROOT = Path("data/reports/evidence_bundles")
BUNDLE_FILENAME = "sanitized_evidence_bundle.zip"


def build_sanitized_evidence_bundle_v1(
    *,
    project_root: str | Path,
    source: str | Path | None = None,
    allowed_files: Iterable[str] = (),
    write_report: bool = False,
    report_path: str | Path | None = None,
    build_sanitized_bundle: bool = False,
    output_dir: str | Path | None = None,
    max_file_bytes: int = 2_000_000,
    compose_output_mode: str = "not-compose",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    source_path = _resolve_optional(root, source)
    report_output = _resolve(root, report_path, DEFAULT_REPORT_PATH)
    bundle_output_dir = _resolve_optional(root, output_dir)
    report_errors = _validate_report_output(root, report_output, write_report)
    bundle_errors = _validate_bundle_output(root, bundle_output_dir, build_sanitized_bundle)

    if source_path is None:
        scan = ScanResult(source_type="missing")
        scan.merge_blocker("input_not_found")
    else:
        scan = scan_source(
            source_path,
            allowed_files=allowed_files,
            max_file_bytes=max_file_bytes,
            compose_output_mode=compose_output_mode,
        )

    structural_blockers = sorted(set(scan.blockers) | set(report_errors) | set(bundle_errors))
    bundle_path: Path | None = None
    bundle_sha256: str | None = None
    bundle_created = False
    final_validation: dict[str, Any] | None = None
    build_error: str | None = None

    if build_sanitized_bundle and not structural_blockers and bundle_output_dir is not None:
        try:
            bundle_path, bundle_sha256, final_validation = _materialize_sanitized_bundle(
                bundle_output_dir=bundle_output_dir,
                sanitized_files=scan.sanitized_files,
                max_file_bytes=max_file_bytes,
            )
            bundle_created = True
        except BundleBuildError as exc:
            build_error = str(exc)
            structural_blockers.append(build_error)

    redaction_performed = bool(bundle_created and scan.findings)
    unresolved_secret_count = 0 if redaction_performed else len(scan.findings)
    status, reason, decision = _decide(
        source_path=source_path,
        scan=scan,
        output_errors=report_errors + bundle_errors,
        build_requested=build_sanitized_bundle,
        bundle_created=bundle_created,
        build_error=build_error,
        unresolved_secret_count=unresolved_secret_count,
    )
    compose_allowed = not scan.compose_interpolation_detected and unresolved_secret_count == 0
    if scan.compose_file_count == 0:
        compose_allowed = True
    compose_blocked_reason = (
        "compose_interpolation_detected_use_docker_compose_config_no_interpolate"
        if scan.compose_interpolation_detected
        else None
    )
    findings = [finding.to_dict(blocking=unresolved_secret_count > 0) for finding in scan.findings]
    blockers = sorted(set(structural_blockers) | ({"secret_findings_unredacted"} if unresolved_secret_count else set()))
    if build_error:
        blockers.append(build_error)
    safety = safety_flags(read_only=not write_report and not build_sanitized_bundle)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_path": _display_path(root, source_path) if source_path else None,
        "source_type": scan.source_type,
        "scanned_file_count": scan.scanned_file_count,
        "scanned_archive_entry_count": scan.scanned_archive_entry_count,
        "allowed_file_count": scan.allowed_file_count,
        "redacted_file_count": scan.redacted_file_count if redaction_performed else 0,
        "blocked_file_count": scan.blocked_file_count,
        "secret_finding_count": len(scan.findings),
        "blocking_secret_finding_count": unresolved_secret_count,
        "forbidden_file_count": scan.forbidden_file_count,
        "allowlist_violation_count": scan.allowlist_violation_count,
        "unsafe_archive_entry_count": scan.unsafe_archive_entry_count,
        "compose_interpolation_detected": scan.compose_interpolation_detected,
        "compose_output_redacted": bool(redaction_performed and scan.compose_file_count),
        "compose_output_allowed": compose_allowed,
        "compose_output_blocked_reason": compose_blocked_reason,
        "redaction_performed": redaction_performed,
        "bundle_created": bundle_created,
        "bundle_path": _display_path(root, bundle_path) if bundle_path else None,
        "bundle_sha256": bundle_sha256,
        "report_path": _display_path(root, report_output),
        "final_bundle_validation": final_validation,
        "findings": findings,
        "warnings": sorted(set(scan.warnings)),
        "blockers": sorted(set(blockers)),
        "write_report_requested": bool(write_report),
        "build_sanitized_bundle_requested": bool(build_sanitized_bundle),
        "write_performed": bool(bundle_created),
        **safety,
        "safety_flags": safety,
    }

    if write_report and not report_errors:
        final_report = dict(report)
        final_report["write_performed"] = True
        _atomic_write_text(
            report_output,
            json.dumps(final_report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        )
        report = final_report
    return report


class BundleBuildError(RuntimeError):
    pass


def _materialize_sanitized_bundle(
    *,
    bundle_output_dir: Path,
    sanitized_files: Mapping[str, bytes],
    max_file_bytes: int,
) -> tuple[Path, str, dict[str, Any]]:
    bundle_output_dir.mkdir(parents=True, exist_ok=True)
    final_path = bundle_output_dir / BUNDLE_FILENAME
    temporary_zip: Path | None = None
    allowlist = normalize_allowlist(sanitized_files.keys())
    try:
        with tempfile.TemporaryDirectory(prefix=".evidence_staging_", dir=bundle_output_dir) as temporary_dir:
            staging = Path(temporary_dir)
            for relative_path, payload in sorted(sanitized_files.items()):
                target = staging / Path(relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)

            staging_scan = scan_directory(
                staging,
                allowed_files=allowlist,
                max_file_bytes=max_file_bytes,
                compose_output_mode="no-interpolate",
            )
            if staging_scan.blockers or staging_scan.findings:
                raise BundleBuildError("sanitized_staging_validation_failed")

            handle = tempfile.NamedTemporaryFile(
                dir=bundle_output_dir,
                prefix=".sanitized_bundle_",
                suffix=".zip",
                delete=False,
            )
            temporary_zip = Path(handle.name)
            handle.close()
            _write_deterministic_zip(temporary_zip, sanitized_files)

            final_scan = scan_zip(
                temporary_zip,
                allowed_files=allowlist,
                max_file_bytes=max_file_bytes,
                compose_output_mode="no-interpolate",
            )
            if final_scan.blockers or final_scan.findings:
                raise BundleBuildError("final_bundle_validation_failed")
            digest = hashlib.sha256(temporary_zip.read_bytes()).hexdigest()
            os.replace(temporary_zip, final_path)
            temporary_zip = None
            return (
                final_path,
                digest,
                {
                    "status": "ok",
                    "scanned_archive_entry_count": final_scan.scanned_archive_entry_count,
                    "secret_finding_count": len(final_scan.findings),
                    "unsafe_archive_entry_count": final_scan.unsafe_archive_entry_count,
                    "blockers": [],
                },
            )
    finally:
        if temporary_zip is not None:
            temporary_zip.unlink(missing_ok=True)


def _write_deterministic_zip(path: Path, files: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path, payload in sorted(files.items()):
            info = zipfile.ZipInfo(relative_path.replace("\\", "/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _decide(
    *,
    source_path: Path | None,
    scan: ScanResult,
    output_errors: list[str],
    build_requested: bool,
    bundle_created: bool,
    build_error: str | None,
    unresolved_secret_count: int,
) -> tuple[str, str, str]:
    if source_path is None or scan.source_type == "missing":
        return "blocked", "input_not_found", "BLOCKED_INPUT_NOT_FOUND"
    if output_errors:
        return "blocked", "output_outside_allowed_root", "BLOCKED_OUTPUT_OUTSIDE_ALLOWED_ROOT"
    if scan.unsafe_archive_entry_count:
        return "blocked", "unsafe_archive_entry", "BLOCKED_UNSAFE_ARCHIVE_ENTRY"
    if scan.forbidden_file_count:
        return "blocked", "forbidden_file", "BLOCKED_FORBIDDEN_FILE"
    if scan.allowlist_violation_count:
        return "blocked", "allowlist_violation", "BLOCKED_ALLOWLIST_VIOLATION"
    if scan.blocked_file_count:
        return "blocked", "blocked_file_policy", "BLOCKED_FORBIDDEN_FILE"
    if scan.compose_interpolation_detected:
        return "blocked", "compose_interpolation_detected", "BLOCKED_COMPOSE_INTERPOLATION"
    if build_error or (build_requested and not bundle_created):
        return "blocked", "sanitized_bundle_build_failed", "BLOCKED_SECRET_FINDINGS"
    if unresolved_secret_count:
        return "blocked", "secret_findings_detected", "BLOCKED_SECRET_FINDINGS"
    if bundle_created and scan.findings:
        return "ok", "bundle_created_after_deterministic_redaction", "BUNDLE_SAFE_AFTER_REDACTION"
    return "ok", "source_passed_allowlist_and_secret_scan", "BUNDLE_SAFE_TO_CREATE"


def safety_flags(*, read_only: bool) -> dict[str, bool]:
    return {
        "paper_only": True,
        "security_only": True,
        "read_only": read_only,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "runs_training": False,
        "writes_runtime": False,
        "writes_feedback": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_models": False,
        "writes_registries": False,
    }


def _validate_report_output(root: Path, output: Path, requested: bool) -> list[str]:
    if not requested:
        return []
    allowed = (root / ALLOWED_REPORT_ROOT).resolve()
    if not _is_under(output, allowed) or output.suffix.casefold() != ".json":
        return ["report_output_outside_data_reports"]
    return []


def _validate_bundle_output(root: Path, output: Path | None, requested: bool) -> list[str]:
    if not requested:
        return []
    if output is None:
        return ["explicit_bundle_output_dir_required"]
    allowed = (root / ALLOWED_BUNDLE_ROOT).resolve()
    if not _is_under(output, allowed):
        return ["bundle_output_outside_allowed_root"]
    return []


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    candidate = Path(value) if value is not None else default
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _resolve_optional(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _display_path(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
