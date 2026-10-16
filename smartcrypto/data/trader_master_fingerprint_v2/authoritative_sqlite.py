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
FORENSIC_RELATED_TABLES = ("trades", "orders", "trade_custom_data")
JOIN_DIAGNOSTIC_COLUMNS = (
    "source_trade_id",
    "order_id",
    "source_row_index",
    "candidate_source_row_index",
    "image_sha256",
)


def read_authoritative_closed_trades(
    *,
    project_root: Path,
    snapshot_path: Path,
    profile: FreqtradePaperSourceProfile,
    include_orders: bool = False,
    include_population_metadata: bool = False,
) -> dict[str, Any]:
    """Return closed trades without opening the source snapshot itself."""

    result: dict[str, Any] = {
        "status": "blocked",
        "reason": "authoritative_sqlite_not_evaluated",
        "rows": [],
        "snapshot_path": _display_path(snapshot_path, project_root),
        "snapshot_access_mode": profile.authoritative_sqlite.access_mode,
        "snapshot_temp_copy_used": False,
        "snapshot_query_only": False,
        "snapshot_source_hashes_before": {},
        "snapshot_source_hashes_after": {},
        "snapshot_source_hashes_preserved": False,
        "snapshot_schema_columns": [],
        "validation_errors": [],
    }
    try:
        errors = _validate_snapshot_path(
            project_root=project_root,
            snapshot_path=snapshot_path,
            profile=profile,
        )
        if errors:
            result.update(reason=errors[0], validation_errors=errors)
            return result
        before = snapshot_artifact_hashes(snapshot_path, project_root)
    except OSError as exc:
        result.update(
            reason="authoritative_sqlite_unreadable",
            validation_errors=[f"authoritative_sqlite_unreadable:{type(exc).__name__}"],
        )
        return result
    result["snapshot_source_hashes_before"] = before

    try:
        with tempfile.TemporaryDirectory(prefix="smart-futuros-paper-snapshot-") as temp_dir:
            copied_db = _copy_snapshot_artifacts(snapshot_path, Path(temp_dir))
            result["snapshot_temp_copy_used"] = True
            if include_population_metadata:
                copied_hashes = snapshot_artifact_hashes(copied_db, Path(temp_dir))
                for suffix in SQLITE_SIDECAR_SUFFIXES:
                    source_key = _display_path(Path(f"{snapshot_path}{suffix}"), project_root)
                    copied_key = _display_path(Path(f"{copied_db}{suffix}"), Path(temp_dir))
                    if before[source_key] != copied_hashes[copied_key]:
                        raise ValueError("population_copy_artifact_mismatch")
                result["snapshot_copy_hashes_verified"] = True
            rows, schema_columns, query_only = _query_closed_trades(
                copied_db,
                required_columns=profile.authoritative_sqlite.required_columns,
            )
            result["rows"] = rows
            result["snapshot_schema_columns"] = schema_columns
            result["snapshot_query_only"] = query_only
            if include_orders:
                result["orders"] = _query_closed_trade_orders(copied_db)
            if include_population_metadata:
                result["snapshot_population"] = _query_population_metadata(copied_db)
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.update(
            reason="authoritative_sqlite_unreadable",
            rows=[],
            orders=[],
            validation_errors=[f"authoritative_sqlite_unreadable:{type(exc).__name__}"],
        )
    else:
        result.update(status="ok", reason="authoritative_sqlite_closed_trades_loaded")
    finally:
        try:
            after = snapshot_artifact_hashes(snapshot_path, project_root)
        except OSError as exc:
            after = {}
            result["validation_errors"].append(f"snapshot_recheck_failed:{type(exc).__name__}")
        result["snapshot_source_hashes_after"] = after
        result["snapshot_source_hashes_preserved"] = before == after
        if before != after:
            result.update(
                status="blocked",
                reason="authoritative_sqlite_source_hash_changed",
                rows=[],
                orders=[],
                validation_errors=sorted(set([
                    *result["validation_errors"], "authoritative_sqlite_source_hash_changed",
                ])),
            )
    return result


def _query_population_metadata(copied_db: Path) -> dict[str, Any]:
    """Describe the whole source without conflating open and closed financial evidence."""
    connection = sqlite3.connect(f"{copied_db.as_uri()}?mode=ro", uri=True, timeout=2.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("sqlite_query_only_not_enabled")
        total, closed, opened, identities, valid_ids = connection.execute(
            "SELECT COUNT(*), "
            "COUNT(CASE WHEN typeof(is_open)='integer' AND is_open=0 THEN 1 END), "
            "COUNT(CASE WHEN typeof(is_open)='integer' AND is_open=1 THEN 1 END), "
            "COUNT(DISTINCT id), "
            "COUNT(CASE WHEN typeof(id)='integer' AND id>0 THEN 1 END) FROM trades"
        ).fetchone()
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(trades)")}
        scope_queries = {
            "source_id": "SELECT DISTINCT source_id FROM trades",
            "account_scope_hash": "SELECT DISTINCT account_scope_hash FROM trades",
            "paper_scope": "SELECT DISTINCT paper_scope FROM trades",
            "epoch_id": "SELECT DISTINCT epoch_id FROM trades",
            "cohort_id": "SELECT DISTINCT cohort_id FROM trades",
        }
        return {
            "total_trade_count": total,
            "closed_trade_count": closed,
            "open_trade_count": opened,
            "unclassified_trade_count": total - closed - opened,
            "distinct_trade_id_count": identities,
            "valid_trade_id_count": valid_ids,
            "scope_values": {
                field: [row[0] for row in connection.execute(query)]
                for field, query in scope_queries.items() if field in columns
            },
        }
    finally:
        connection.close()


def _query_closed_trade_orders(copied_db: Path) -> list[dict[str, Any]]:
    """Use the same stable temporary snapshot, never the operational database."""
    connection = sqlite3.connect(f"{copied_db.as_uri()}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("sqlite_query_only_not_enabled")
        required = {
            "id", "ft_trade_id", "order_id", "ft_pair", "ft_order_side", "side",
            "ft_is_open", "status", "amount", "filled", "remaining", "average",
            "order_date", "order_filled_date",
        }
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(orders)")}
        if not required <= columns:
            raise ValueError("financial_lifecycle_orders_schema_incomplete")
        if connection.execute(
            "SELECT 1 FROM orders LEFT JOIN trades ON orders.ft_trade_id = trades.id "
            "WHERE trades.id IS NULL LIMIT 1"
        ).fetchone() is not None:
            raise ValueError("financial_lifecycle_order_parent_identity_unresolved")
        return [dict(row) for row in connection.execute(
            "SELECT * FROM orders WHERE ft_trade_id IN "
            "(SELECT id FROM trades WHERE is_open = 0) ORDER BY id"
        )]
    finally:
        connection.close()


def read_authoritative_trade_evidence(
    *,
    project_root: Path,
    snapshot_path: Path,
    profile: FreqtradePaperSourceProfile,
    trade_ids: frozenset[int],
) -> dict[str, Any]:
    """Read fixed-table evidence for an explicit, in-memory trade ID set."""

    before = snapshot_artifact_hashes(snapshot_path, project_root)
    result: dict[str, Any] = {
        "status": "blocked",
        "reason": "authoritative_forensic_evidence_not_evaluated",
        "trades": [],
        "orders": [],
        "trade_custom_data": [],
        "snapshot_path": _display_path(snapshot_path, project_root),
        "snapshot_access_mode": profile.authoritative_sqlite.access_mode,
        "snapshot_temp_copy_used": False,
        "snapshot_query_only": False,
        "snapshot_source_hashes_before": before,
        "snapshot_source_hashes_after": {},
        "snapshot_source_hashes_preserved": False,
        "related_tables_inspected": [],
        "table_schemas": {},
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
        with tempfile.TemporaryDirectory(prefix="smart-futuros-paper-forensics-") as temp_dir:
            copied_db = _copy_snapshot_artifacts(snapshot_path, Path(temp_dir))
            result["snapshot_temp_copy_used"] = True
            evidence = _query_trade_evidence(copied_db, trade_ids=trade_ids)
            result.update(evidence)
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.update(
            reason="authoritative_forensic_evidence_unreadable",
            validation_errors=[
                f"authoritative_forensic_evidence_unreadable:{type(exc).__name__}"
            ],
        )
    else:
        result.update(status="ok", reason="authoritative_forensic_evidence_loaded")
    finally:
        after = snapshot_artifact_hashes(snapshot_path, project_root)
        result["snapshot_source_hashes_after"] = after
        result["snapshot_source_hashes_preserved"] = before == after
        if before != after:
            result.update(
                status="blocked",
                reason="authoritative_sqlite_source_hash_changed",
                trades=[],
                orders=[],
                trade_custom_data=[],
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


def inspect_sqlite_schema_readonly(
    *,
    project_root: Path,
    snapshot_path: Path,
) -> dict[str, Any]:
    """Inspect SQLite schema and exact-key uniqueness through a query-only copy."""

    before = snapshot_artifact_hashes(snapshot_path, project_root)
    result: dict[str, Any] = {
        "status": "blocked",
        "reason": "sqlite_schema_not_evaluated",
        "snapshot_path": _display_path(snapshot_path, project_root),
        "snapshot_temp_copy_used": False,
        "snapshot_query_only": False,
        "snapshot_source_hashes_before": before,
        "snapshot_source_hashes_after": {},
        "snapshot_source_hashes_preserved": False,
        "table_schemas": {},
        "table_row_counts": {},
        "join_column_diagnostics": {},
        "validation_errors": [],
    }
    errors = _validate_generic_sqlite_path(project_root, snapshot_path)
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
        with tempfile.TemporaryDirectory(prefix="smart-futuros-sqlite-inventory-") as temp_dir:
            copied_db = _copy_snapshot_artifacts(snapshot_path, Path(temp_dir))
            result["snapshot_temp_copy_used"] = True
            result.update(_query_sqlite_schema(copied_db))
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.update(
            reason="sqlite_schema_unreadable",
            validation_errors=[f"sqlite_schema_unreadable:{type(exc).__name__}"],
        )
    else:
        result.update(status="ok", reason="sqlite_schema_loaded_query_only")
    finally:
        after = snapshot_artifact_hashes(snapshot_path, project_root)
        result["snapshot_source_hashes_after"] = after
        result["snapshot_source_hashes_preserved"] = before == after
        if before != after:
            result.update(
                status="blocked",
                reason="sqlite_source_hash_changed",
                table_schemas={},
                table_row_counts={},
                join_column_diagnostics={},
                validation_errors=["sqlite_source_hash_changed"],
            )
    return result


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


def _validate_generic_sqlite_path(project_root: Path, snapshot_path: Path) -> list[str]:
    try:
        snapshot_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return ["sqlite_outside_project_root"]
    if snapshot_path.is_symlink():
        return ["sqlite_symlink_forbidden"]
    if not snapshot_path.exists() or not snapshot_path.is_file():
        return ["sqlite_missing"]
    if snapshot_path.suffix.casefold() not in {".sqlite", ".db"}:
        return ["sqlite_extension_invalid"]
    if any(Path(f"{snapshot_path}{suffix}").is_symlink() for suffix in SQLITE_SIDECAR_SUFFIXES):
        return ["sqlite_sidecar_symlink_forbidden"]
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


def _query_sqlite_schema(copied_db: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"{copied_db.as_uri()}?mode=ro", uri=True, timeout=2.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
        if not query_only:
            raise ValueError("sqlite_query_only_not_enabled")
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        schemas: dict[str, list[str]] = {}
        row_counts: dict[str, int] = {}
        join_diagnostics: dict[str, dict[str, dict[str, Any]]] = {}
        for table in tables:
            quoted_table = _quote_identifier(table)
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            ]
            schemas[table] = columns
            row_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {quoted_table}"  # nosec B608
                ).fetchone()[0]
            )
            row_counts[table] = row_count
            diagnostics: dict[str, dict[str, Any]] = {}
            for column in JOIN_DIAGNOSTIC_COLUMNS:
                if column not in columns:
                    continue
                quoted_column = _quote_identifier(column)
                present_count, distinct_count = connection.execute(
                    f"SELECT COUNT({quoted_column}), COUNT(DISTINCT {quoted_column}) "  # nosec B608
                    f"FROM {quoted_table}"
                ).fetchone()
                diagnostics[column] = {
                    "present_count": int(present_count),
                    "distinct_count": int(distinct_count),
                    "unique_when_present": int(present_count) == int(distinct_count),
                }
            if diagnostics:
                join_diagnostics[table] = diagnostics
        return {
            "snapshot_query_only": query_only,
            "table_schemas": schemas,
            "table_row_counts": row_counts,
            "join_column_diagnostics": join_diagnostics,
        }
    finally:
        connection.close()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _query_trade_evidence(
    copied_db: Path,
    *,
    trade_ids: frozenset[int],
) -> dict[str, Any]:
    connection = sqlite3.connect(f"{copied_db.as_uri()}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
        if not query_only:
            raise ValueError("sqlite_query_only_not_enabled")
        available_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = sorted(set(FORENSIC_RELATED_TABLES) - available_tables)
        if missing_tables:
            raise ValueError("forensic_related_tables_missing:" + ",".join(missing_tables))
        schemas = {
            table: [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            for table in FORENSIC_RELATED_TABLES
        }
        if "id" not in schemas["trades"]:
            raise ValueError("trades_relation_key_missing")
        for table in ("orders", "trade_custom_data"):
            if "ft_trade_id" not in schemas[table]:
                raise ValueError(f"{table}_relation_key_missing")
        all_trades = [dict(row) for row in connection.execute("SELECT * FROM trades ORDER BY id")]
        all_orders = [
            dict(row)
            for row in connection.execute("SELECT * FROM orders ORDER BY ft_trade_id, id")
        ]
        all_custom_data = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM trade_custom_data ORDER BY ft_trade_id, id"
            )
        ]
        return {
            "trades": [row for row in all_trades if int(row["id"]) in trade_ids],
            "orders": [
                row
                for row in all_orders
                if row.get("ft_trade_id") is not None
                and int(row["ft_trade_id"]) in trade_ids
            ],
            "trade_custom_data": [
                row
                for row in all_custom_data
                if row.get("ft_trade_id") is not None
                and int(row["ft_trade_id"]) in trade_ids
            ],
            "snapshot_query_only": query_only,
            "related_tables_inspected": list(FORENSIC_RELATED_TABLES),
            "table_schemas": schemas,
        }
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
