"""Read a Freqtrade paper snapshot through a temporary query-only copy."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .source_profile import FreqtradePaperSourceProfile


SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def read_authoritative_closed_trades(
    *,
    project_root: Path,
    snapshot_path: Path,
    profile: FreqtradePaperSourceProfile,
) -> dict[str, Any]:
    """Return closed trades without opening the source snapshot itself."""

    before = snapshot_artifact_hashes(snapshot_path, project_root)
    result: dict[str, Any] = {
        "status": "blocked",
        "reason": "authoritative_sqlite_not_evaluated",
        "rows": [],
        "snapshot_path": _display_path(snapshot_path, project_root),
        "snapshot_access_mode": profile.authoritative_sqlite.access_mode,
        "snapshot_temp_copy_used": False,
        "snapshot_query_only": False,
        "snapshot_source_hashes_before": before,
        "snapshot_source_hashes_after": {},
        "snapshot_source_hashes_preserved": False,
        "snapshot_schema_columns": [],
        "validation_errors": [],
    }
    errors = _validate_snapshot_path(
        project_root=project_root,
        snapshot_path=snapshot_path,
        profile=profile,
    )
    if errors:
        result.update(reason=errors[0], validation_errors=errors)
        result["snapshot_source_hashes_after"] = snapshot_artifact_hashes(
            snapshot_path, project_root
        )
        result["snapshot_source_hashes_preserved"] = (
            result["snapshot_source_hashes_after"] == before
        )
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="smart-futuros-paper-snapshot-") as temp_dir:
            copied_db = _copy_snapshot_artifacts(snapshot_path, Path(temp_dir))
            result["snapshot_temp_copy_used"] = True
            rows, schema_columns, query_only = _query_closed_trades(
                copied_db,
                required_columns=profile.authoritative_sqlite.required_columns,
            )
            result["rows"] = rows
            result["snapshot_schema_columns"] = schema_columns
            result["snapshot_query_only"] = query_only
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.update(
            reason="authoritative_sqlite_unreadable",
            validation_errors=[f"authoritative_sqlite_unreadable:{type(exc).__name__}"],
        )
    else:
        result.update(status="ok", reason="authoritative_sqlite_closed_trades_loaded")
    finally:
        after = snapshot_artifact_hashes(snapshot_path, project_root)
        result["snapshot_source_hashes_after"] = after
        result["snapshot_source_hashes_preserved"] = before == after
        if before != after:
            result.update(
                status="blocked",
                reason="authoritative_sqlite_source_hash_changed",
                rows=[],
                validation_errors=["authoritative_sqlite_source_hash_changed"],
            )
    return result


def snapshot_artifact_hashes(snapshot_path: Path, project_root: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for suffix in SQLITE_SIDECAR_SUFFIXES:
        path = Path(f"{snapshot_path}{suffix}")
        exists = path.exists() and path.is_file()
        artifacts[_display_path(path, project_root)] = {
            "exists": exists,
            "size_bytes": path.stat().st_size if exists else None,
            "sha256": _sha256(path) if exists else None,
        }
    return artifacts


def _validate_snapshot_path(
    *,
    project_root: Path,
    snapshot_path: Path,
    profile: FreqtradePaperSourceProfile,
) -> list[str]:
    try:
        snapshot_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return ["authoritative_sqlite_outside_project_root"]
    if any(
        snapshot_path.resolve() == (project_root / item).resolve()
        for item in profile.authoritative_sqlite.explicitly_non_authoritative_paths
    ):
        return ["explicitly_non_authoritative_sqlite_forbidden"]
    if snapshot_path.is_symlink():
        return ["authoritative_sqlite_symlink_forbidden"]
    if not snapshot_path.exists() or not snapshot_path.is_file():
        return ["authoritative_sqlite_missing"]
    if snapshot_path.suffix.casefold() not in {".sqlite", ".db"}:
        return ["authoritative_sqlite_extension_invalid"]
    if any(Path(f"{snapshot_path}{suffix}").is_symlink() for suffix in SQLITE_SIDECAR_SUFFIXES):
        return ["authoritative_sqlite_sidecar_symlink_forbidden"]
    return []


def _copy_snapshot_artifacts(snapshot_path: Path, temp_dir: Path) -> Path:
    copied_db = temp_dir / snapshot_path.name
    for suffix in SQLITE_SIDECAR_SUFFIXES:
        source = Path(f"{snapshot_path}{suffix}")
        if source.exists() and source.is_file():
            destination = Path(f"{copied_db}{suffix}")
            shutil.copy2(source, destination)
    if not copied_db.exists():
        raise FileNotFoundError(snapshot_path)
    return copied_db


def _query_closed_trades(
    copied_db: Path,
    *,
    required_columns: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    connection = sqlite3.connect(f"{copied_db.as_uri()}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
        if not query_only:
            raise ValueError("sqlite_query_only_not_enabled")
        schema_columns = [
            str(row[1]) for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        ]
        missing = sorted(set(required_columns) - set(schema_columns))
        if missing:
            raise ValueError("authoritative_sqlite_missing_columns:" + ",".join(missing))
        rows = [
            {column: row[column] for column in required_columns}
            for row in connection.execute(
                'SELECT * FROM "trades" WHERE "is_open" = 0 ORDER BY "id"'
            ).fetchall()
        ]
        return rows, schema_columns, query_only
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
