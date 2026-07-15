"""Read-only paper closed-trade snapshot construction."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite import (
    read_authoritative_closed_trades,
    read_authoritative_trade_evidence,
    snapshot_artifact_hashes,
)
from smartcrypto.data.trader_master_fingerprint_v2.source_profile import (
    FreqtradePaperSourceProfile,
)
from smartcrypto.research.profit_research.paper_analysis import normalize_snapshot_trades


def build_paper_trade_snapshot(
    *,
    project_root: Path,
    source_path: Path,
    profile: FreqtradePaperSourceProfile,
    authoritative_snapshot: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if authoritative_snapshot:
        raw, metadata = _read_authoritative(project_root, source_path, profile)
    else:
        raw, metadata = _read_runtime_copy(project_root, source_path, profile)
    if raw.empty:
        return pd.DataFrame(), metadata

    frame = normalize_snapshot_trades(raw, profile)
    source_hash = _primary_db_hash(metadata)
    frame["stable_trade_id"] = frame["trade_id"].map(
        lambda value: f"freqtrade-paper-{int(value)}" if pd.notna(value) else None
    )
    frame["source_origin"] = "paper_sqlite_snapshot" if authoritative_snapshot else "paper_sqlite_runtime_copy"
    frame["source_hash"] = source_hash
    frame["eligibility_status"] = frame["analysis_eligible"].map(
        {True: "eligible", False: "rejected"}
    )
    frame["rejection_reason"] = frame["analysis_block_reason"]
    duplicate_mask = frame["stable_trade_id"].duplicated(keep="first") | frame[
        "stable_trade_id"
    ].isna()
    duplicate_count = int(duplicate_mask.sum())
    frame.loc[duplicate_mask, "analysis_eligible"] = False
    frame.loc[duplicate_mask, "eligibility_status"] = "rejected"
    frame.loc[duplicate_mask, "rejection_reason"] = "duplicate_trade_identity"
    frame["financial_decomposition_status"] = frame["accounting_reconciled"].map(
        {True: "authoritative_reconciled", False: "accounting_unreconciled"}
    )
    frame = frame.sort_values(["open_time_utc", "stable_trade_id"], na_position="last")
    frame = frame.reset_index(drop=True)
    metadata.update(
        normalized_trade_count=int(len(frame)),
        duplicate_trade_count=duplicate_count,
        source_hash=source_hash,
    )
    return frame, metadata


def _read_authoritative(
    project_root: Path,
    source_path: Path,
    profile: FreqtradePaperSourceProfile,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    closed = read_authoritative_closed_trades(
        project_root=project_root,
        snapshot_path=source_path,
        profile=profile,
    )
    rows = closed.pop("rows", [])
    if closed.get("status") != "ok":
        return pd.DataFrame(), closed
    trade_ids = frozenset(int(row["id"]) for row in rows)
    evidence = read_authoritative_trade_evidence(
        project_root=project_root,
        snapshot_path=source_path,
        profile=profile,
        trade_ids=trade_ids,
    )
    trades = evidence.pop("trades", [])
    evidence.pop("orders", None)
    evidence.pop("trade_custom_data", None)
    metadata = {
        **closed,
        "full_evidence_status": evidence.get("status"),
        "full_evidence_reason": evidence.get("reason"),
        "full_trade_row_count": len(trades),
    }
    if evidence.get("status") != "ok" or len(trades) != len(trade_ids):
        metadata.update(status="blocked", reason="paper_snapshot_evidence_incomplete")
        return pd.DataFrame(), metadata
    return pd.DataFrame(trades), metadata


def _read_runtime_copy(
    project_root: Path,
    source_path: Path,
    profile: FreqtradePaperSourceProfile,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    before = snapshot_artifact_hashes(source_path, project_root)
    base: dict[str, Any] = {
        "status": "blocked",
        "reason": "paper_runtime_db_not_evaluated",
        "snapshot_temp_copy_used": False,
        "snapshot_query_only": False,
        "snapshot_source_hashes_before": before,
        "snapshot_source_hashes_after": {},
        "snapshot_source_hashes_preserved": False,
    }
    if source_path.is_symlink() or not source_path.is_file():
        base["reason"] = "paper_runtime_db_missing_or_unsafe"
        return pd.DataFrame(), base
    try:
        source_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        base["reason"] = "paper_runtime_db_outside_project_root"
        return pd.DataFrame(), base
    try:
        with tempfile.TemporaryDirectory(prefix="paper-research-runtime-copy-") as temporary:
            copied = Path(temporary) / source_path.name
            for suffix in ("", "-wal", "-shm"):
                source = Path(f"{source_path}{suffix}")
                if source.is_file():
                    shutil.copy2(source, Path(f"{copied}{suffix}"))
            base["snapshot_temp_copy_used"] = True
            connection = sqlite3.connect(f"{copied.as_uri()}?mode=ro", uri=True, timeout=2.0)
            try:
                connection.execute("PRAGMA query_only = ON")
                query_only = int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1
                columns = [
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(trades)").fetchall()
                ]
                missing = sorted(set(profile.authoritative_sqlite.required_columns) - set(columns))
                if missing:
                    raise ValueError("paper_runtime_db_missing_columns:" + ",".join(missing))
                cursor = connection.execute("SELECT * FROM trades WHERE is_open = 0 ORDER BY id")
                names = [str(item[0]) for item in cursor.description]
                rows = [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]
            finally:
                connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        base["reason"] = f"paper_runtime_db_unreadable:{type(exc).__name__}"
        return pd.DataFrame(), base
    after = snapshot_artifact_hashes(source_path, project_root)
    preserved = before == after
    base.update(
        status="ok" if preserved else "blocked",
        reason="paper_runtime_db_loaded_from_query_only_copy" if preserved else "paper_runtime_db_changed",
        snapshot_query_only=query_only,
        snapshot_source_hashes_after=after,
        snapshot_source_hashes_preserved=preserved,
    )
    return pd.DataFrame(rows) if preserved else pd.DataFrame(), base


def _primary_db_hash(metadata: dict[str, Any]) -> str | None:
    hashes = metadata.get("snapshot_source_hashes_before", {})
    if not isinstance(hashes, dict):
        return None
    for name, item in hashes.items():
        if str(name).endswith((".sqlite", ".db")) and isinstance(item, dict):
            value = item.get("sha256")
            return str(value) if value else None
    return None
