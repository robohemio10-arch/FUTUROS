from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.paper_shadow_soak_report import safety_payload, unsafe_safety_flags, write_report
from smartcrypto.ops.system_healthcheck import ensure_utc, iso

DEFAULT_BACKUP_ROOT = Path("data/backups")
DEFAULT_REPORT_PATH = Path("data/reports/backup_snapshot_report.json")
DEFAULT_RESTORE_REPORT_PATH = Path("data/reports/restore_dry_run_report.json")
MANIFEST_NAME = "backup_manifest.json"
SENSITIVE_NAME_TOKENS = (".env", "secret", "secrets", "token", "credential", "credentials", "private_key", "id_rsa")
SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
FREQTRADE_DB_TOKENS = ("freqtrade", "tradesv3", "paper.sqlite")


def create_backup_snapshot(
    *,
    inputs: list[str | Path],
    output_dir: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    allow_freqtrade_db: bool = False,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = ensure_utc(now or datetime.now(timezone.utc))
    snapshot_dir = Path(output_dir) if output_dir is not None else DEFAULT_BACKUP_ROOT / f"system_snapshot_{created_at.strftime('%Y%m%d_%H%M%S')}"
    safety = safety_payload(safety_overrides)
    blockers = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    files = collect_input_files(inputs)
    for source in files:
        reason = forbidden_backup_reason(source, allow_freqtrade_db=allow_freqtrade_db)
        if reason:
            blockers.append(reason)
    missing_inputs = [str(Path(item)) for item in inputs if not Path(item).exists()]
    if missing_inputs and strict:
        blockers.extend(f"missing_input:{path}" for path in missing_inputs)
    if blockers:
        report = backup_report(
            status="blocked",
            reason=";".join(sorted(set(blockers))),
            snapshot_dir=snapshot_dir,
            manifest_path=None,
            files=[],
            created_at=created_at,
            write_performed=False,
            missing_inputs=missing_inputs,
            safety=safety,
        )
        write_report(report, report_path)
        return report

    manifest_files = []
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(files, key=lambda path: str(path).lower()):
        relative = backup_relative_path(source, inputs)
        destination = snapshot_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest_files.append(
            {
                "relative_path": relative.as_posix(),
                "source_path": str(source),
                "sha256": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
            }
        )
    manifest = {
        "status": "ok",
        "manifest_version": 1,
        "created_at_utc": iso(created_at),
        "file_count": len(manifest_files),
        "total_size_bytes": sum(item["size_bytes"] for item in manifest_files),
        "files": manifest_files,
        **safety,
    }
    manifest_path = snapshot_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    report = backup_report(
        status="ok",
        reason="backup_snapshot_ok",
        snapshot_dir=snapshot_dir,
        manifest_path=manifest_path,
        files=manifest_files,
        created_at=created_at,
        write_performed=True,
        missing_inputs=missing_inputs,
        safety=safety,
    )
    write_report(report, report_path)
    return report


def run_restore_dry_run(
    *,
    backup_dir: str | Path | None = None,
    manifest: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_RESTORE_REPORT_PATH,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = ensure_utc(now or datetime.now(timezone.utc))
    safety = safety_payload(safety_overrides)
    blockers = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    manifest_path = Path(manifest) if manifest is not None else Path(backup_dir or "") / MANIFEST_NAME
    base_dir = Path(backup_dir) if backup_dir is not None else manifest_path.parent
    payload = load_manifest(manifest_path)
    missing_files: list[str] = []
    corrupted_files: list[str] = []
    would_restore_files: list[dict[str, Any]] = []
    if not manifest_path.exists():
        blockers.append("missing_manifest")
    elif payload.get("status") != "ok":
        blockers.append("invalid_manifest")
    else:
        for item in payload.get("files", []):
            relative = Path(str(item.get("relative_path", "")))
            snapshot_file = base_dir / relative
            if not snapshot_file.exists():
                missing_files.append(relative.as_posix())
                continue
            observed_hash = sha256_file(snapshot_file)
            if observed_hash != item.get("sha256"):
                corrupted_files.append(relative.as_posix())
            would_restore_files.append(
                {
                    "relative_path": relative.as_posix(),
                    "source_path": item.get("source_path"),
                    "target_path": item.get("source_path"),
                    "size_bytes": item.get("size_bytes", 0),
                }
            )
    if missing_files:
        blockers.append("missing_backup_files")
    if corrupted_files:
        blockers.append("corrupted_backup_files")
    if strict and not would_restore_files:
        blockers.append("empty_restore_plan")
    status = "blocked" if blockers else "ok"
    report = {
        "status": status,
        "reason": ";".join(sorted(set(blockers))) if blockers else "restore_dry_run_ok",
        "generated_at_utc": iso(generated_at),
        "dry_run": True,
        "write_performed": False,
        "backup_dir": str(base_dir),
        "manifest_path": str(manifest_path),
        "manifest_valid": status == "ok",
        "file_count": len(would_restore_files),
        "would_restore_files": would_restore_files,
        "missing_files": missing_files,
        "corrupted_files": corrupted_files,
        **safety,
    }
    write_report(report, report_path)
    return report


def collect_input_files(inputs: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        target = Path(item)
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(path for path in target.rglob("*") if path.is_file())
    return files


def forbidden_backup_reason(path: Path, *, allow_freqtrade_db: bool) -> str | None:
    lower = str(path).lower()
    name = path.name.lower()
    if name in {".env"} or any(token in lower for token in SENSITIVE_NAME_TOKENS) or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return f"sensitive_file_blocked:{path}"
    if not allow_freqtrade_db and path.suffix.lower() in {".sqlite", ".db"} and any(token in lower for token in FREQTRADE_DB_TOKENS):
        return f"freqtrade_db_blocked:{path}"
    return None


def backup_relative_path(source: Path, inputs: list[str | Path]) -> Path:
    for item in sorted((Path(value) for value in inputs), key=lambda path: len(str(path)), reverse=True):
        if item.is_dir():
            try:
                return Path(item.name) / source.relative_to(item)
            except ValueError:
                continue
        if item.is_file() and source == item:
            return Path(item.name)
    return Path(source.name)


def backup_report(
    *,
    status: str,
    reason: str,
    snapshot_dir: Path,
    manifest_path: Path | None,
    files: list[dict[str, Any]],
    created_at: datetime,
    write_performed: bool,
    missing_inputs: list[str],
    safety: dict[str, bool],
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "created_at_utc": iso(created_at),
        "backup_dir": str(snapshot_dir),
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "file_count": len(files),
        "total_size_bytes": sum(int(item.get("size_bytes", 0)) for item in files),
        "files": files,
        "missing_inputs": missing_inputs,
        "write_performed": write_performed,
        **safety,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        return {"status": "blocked", "error": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "blocked", "error": "invalid_manifest_payload"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
