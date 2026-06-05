from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
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
    project_root: str | Path | None = None,
    allow_freqtrade_db: bool = False,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = ensure_utc(now or datetime.now(timezone.utc))
    snapshot_dir = Path(output_dir) if output_dir is not None else DEFAULT_BACKUP_ROOT / f"system_snapshot_{created_at.strftime('%Y%m%d_%H%M%S')}"
    root = resolved_path(project_root or Path.cwd())
    safety = safety_payload(safety_overrides)
    blockers = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    files = collect_input_files(inputs)
    relative_paths = {source: backup_relative_path(source, inputs, project_root=root) for source in files}
    duplicate_relative_paths = duplicate_values(relative.as_posix() for relative in relative_paths.values())
    if duplicate_relative_paths:
        blockers.append("duplicate_relative_paths")
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
            duplicate_relative_paths=duplicate_relative_paths,
            safety=safety,
        )
        write_report(report, report_path)
        return report

    manifest_files = []
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(files, key=lambda path: str(path).lower()):
        relative = relative_paths[source]
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
        "project_root": str(root),
        "relative_path_policy": "project_root_relative_else_external_hash",
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
        duplicate_relative_paths=[],
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
    duplicate_relative_paths: list[str] = []
    invalid_relative_paths: list[str] = []
    would_restore_files: list[dict[str, Any]] = []
    if not manifest_path.exists():
        blockers.append("missing_manifest")
    elif payload.get("status") != "ok":
        blockers.append("invalid_manifest")
    else:
        manifest_files = payload.get("files", [])
        if not isinstance(manifest_files, list):
            manifest_files = []
            blockers.append("invalid_manifest_files")
        duplicate_relative_paths = duplicate_values(str(item.get("relative_path", "")) for item in manifest_files if isinstance(item, Mapping))
        if duplicate_relative_paths:
            blockers.append("duplicate_relative_paths")
        for item in manifest_files:
            if not isinstance(item, Mapping):
                blockers.append("invalid_manifest_file_entry")
                continue
            relative_text = str(item.get("relative_path", ""))
            if not valid_manifest_relative_path(relative_text):
                invalid_relative_paths.append(relative_text)
                continue
            relative = Path(relative_text)
            snapshot_file = base_dir / relative
            if not snapshot_file.exists():
                missing_files.append(relative.as_posix())
                continue
            observed_hash = sha256_file(snapshot_file)
            observed_size = snapshot_file.stat().st_size
            expected_size = int_or_none(item.get("size_bytes"))
            if observed_hash != item.get("sha256"):
                corrupted_files.append(relative.as_posix())
            elif expected_size is None or observed_size != expected_size:
                corrupted_files.append(relative.as_posix())
            would_restore_files.append(
                {
                    "relative_path": relative.as_posix(),
                    "source_path": item.get("source_path"),
                    "target_path": item.get("source_path"),
                    "size_bytes": item.get("size_bytes", 0),
                }
            )
    if invalid_relative_paths:
        blockers.append("invalid_relative_paths")
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
        "duplicate_relative_paths": duplicate_relative_paths,
        "invalid_relative_paths": invalid_relative_paths,
        **safety,
    }
    write_report(report, report_path)
    return report


def collect_input_files(inputs: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        target = Path(item)
        if target.is_file():
            resolved = resolved_path(target)
            if resolved not in seen:
                seen.add(resolved)
                files.append(target)
        elif target.is_dir():
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                resolved = resolved_path(path)
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(path)
    return files


def forbidden_backup_reason(path: Path, *, allow_freqtrade_db: bool) -> str | None:
    lower = str(path).lower()
    name = path.name.lower()
    if name in {".env"} or any(token in lower for token in SENSITIVE_NAME_TOKENS) or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return f"sensitive_file_blocked:{path}"
    if not allow_freqtrade_db and path.suffix.lower() in {".sqlite", ".db"} and any(token in lower for token in FREQTRADE_DB_TOKENS):
        return f"freqtrade_db_blocked:{path}"
    return None


def backup_relative_path(source: Path, inputs: list[str | Path], *, project_root: Path | None = None) -> Path:
    source_resolved = resolved_path(source)
    root = resolved_path(project_root or Path.cwd())
    try:
        return source_resolved.relative_to(root)
    except ValueError:
        pass
    for item in sorted((Path(value) for value in inputs), key=lambda path: len(str(path)), reverse=True):
        item_resolved = resolved_path(item)
        if item.is_dir():
            try:
                suffix = source_resolved.relative_to(item_resolved)
            except ValueError:
                continue
            return Path("external") / external_path_id(item_resolved) / sanitized_path(item.name) / suffix
        if item.is_file() and source_resolved == item_resolved:
            return Path("external") / external_path_id(source_resolved) / sanitized_path(source.name)
    return Path("external") / external_path_id(source_resolved) / sanitized_path(source.name)


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
    duplicate_relative_paths: list[str],
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
        "duplicate_relative_paths": duplicate_relative_paths,
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


def duplicate_values(values: Any) -> list[str]:
    counts = Counter(str(value) for value in values)
    return sorted(value for value, count in counts.items() if value and count > 1)


def valid_manifest_relative_path(value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def resolved_path(path: str | Path) -> Path:
    return Path(path).resolve()


def external_path_id(path: Path) -> str:
    return hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()[:16]


def sanitized_path(value: str) -> Path:
    cleaned = "".join(char if char.isalnum() or char in {".", "_", "-"} else "_" for char in str(value).strip())
    return Path(cleaned or "unnamed")


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
