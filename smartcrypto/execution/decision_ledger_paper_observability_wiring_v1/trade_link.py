"""Read-only Phase14 adapter for explicit decision-to-trade correlation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    RuntimeIdentityEvidenceV1,
    create_paper_runtime_writer,
    run_writer_preflight,
)
from smartcrypto.execution.decision_ledger_runtime_integration_v1 import (
    build_decision_index,
    preview_trade_link,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeDecisionProjectionV1,
)

from .config import load_observability_config
from .contracts import (
    DEFAULT_CONFIG_PATH,
    PaperObservabilityWiringConfigV1,
    TradeLinkAdapterReportV1,
)
from .sink import IdempotentDecisionLedgerRuntimeSink

_EVENT_TAG = re.compile(
    r"(?:^|\|)decision_event_id=([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})(?:\||$)"
)


def sync_phase14_trade_links_readonly(
    *,
    snapshot_db: str | Path,
    project_root: str | Path = ".",
    config_source: str | Path | Mapping[str, Any] | None = DEFAULT_CONFIG_PATH,
    identity: RuntimeIdentityEvidenceV1 | None = None,
) -> TradeLinkAdapterReportV1:
    """Read explicit correlations from a snapshot; never match by time alone."""

    config = load_observability_config(config_source)
    if not config.enabled or not config.trade_link_enabled:
        return TradeLinkAdapterReportV1(
            status="disabled",
            reason="phase14_trade_link_adapter_disabled_by_default",
            enabled=False,
            source_trade_count=0,
            correlated_trade_count=0,
            projected_trade_link_count=0,
            persisted_trade_link_count=0,
            duplicate_trade_link_count=0,
            writer_invoked=False,
            writes_runtime=False,
            safety_flags=config.safety_flags,
        )

    root = Path(project_root).expanduser().resolve(strict=False)
    preflight = run_writer_preflight(
        project_root=root,
        profile=config.writer_profile,
        identity=identity,
    )
    factory = create_paper_runtime_writer(
        profile=config.writer_profile,
        preflight=preflight,
    )
    if factory.writer is None:
        return _blocked(config, reason=factory.report.reason)

    index_path = _resolve_project_relative(root, config.index_path)
    sink = IdempotentDecisionLedgerRuntimeSink(
        writer=factory.writer,
        index_path=index_path,
        lock_timeout_seconds=config.writer_profile.durability.lock_timeout_seconds,
    )
    try:
        decision_index = _load_decision_index(sink.read_index())
        rows = _read_closed_trades_readonly(Path(snapshot_db))
    except (OSError, ValueError, sqlite3.Error) as exc:
        return _blocked(config, reason=f"trade_link_source_unavailable:{type(exc).__name__}")

    source_hash = _sha256_file(Path(snapshot_db))
    projections = []
    failures: list[dict[str, object]] = []
    correlated_count = 0
    for row in rows:
        decision_event_id = _decision_event_id(row.get("enter_tag"))
        if decision_event_id is None:
            continue
        correlated_count += 1
        try:
            execution_timestamp = _parse_utc(row["open_date"])
            pair = str(row["pair"])
            symbol = _symbol_from_pair(pair)
            side = "short" if bool(row["is_short"]) else "long"
            row_fingerprint = _row_sha256(row)
            preview = preview_trade_link(
                decision_index=decision_index,
                request={
                    "decision_event_id": decision_event_id,
                    "trade_observation": {
                        "trade_id": int(row["id"]),
                        "execution_timestamp": execution_timestamp,
                        "observed_pair": pair,
                        "observed_symbol": symbol,
                        "observed_side": side,
                        "source_database_sha256": source_hash,
                        "source_table": "trades",
                        "source_row_fingerprint": row_fingerprint,
                        "link_reason": "explicit_decision_event_id_in_enter_tag",
                    },
                },
            )
            if preview.status != "ok" or preview.projection is None:
                raise ValueError(preview.reason or "trade_link_projection_failed")
            projections.append(preview.projection)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(_failure(row, exc))

    receipts = []
    try:
        for projection in projections:
            receipts.append(sink.append(projection))
    except Exception as exc:  # noqa: BLE001 - fail-closed Phase14 adapter boundary.
        failures.append(_failure({}, exc))
        return TradeLinkAdapterReportV1(
            status="blocked",
            reason=f"trade_link_persistence_failed:{type(exc).__name__}",
            enabled=True,
            source_trade_count=len(rows),
            correlated_trade_count=correlated_count,
            projected_trade_link_count=len(projections),
            persisted_trade_link_count=sum(item.append_performed for item in receipts),
            duplicate_trade_link_count=sum(item.duplicate for item in receipts),
            writer_invoked=True,
            writes_runtime=any(item.append_performed for item in receipts),
            failures=tuple(failures),
            safety_flags=config.safety_flags,
        )

    status = "blocked" if failures else "ok"
    reason = "explicit_trade_correlation_failed" if failures else None
    return TradeLinkAdapterReportV1(
        status=status,
        reason=reason,
        enabled=True,
        source_trade_count=len(rows),
        correlated_trade_count=correlated_count,
        projected_trade_link_count=len(projections),
        persisted_trade_link_count=sum(item.append_performed for item in receipts),
        duplicate_trade_link_count=sum(item.duplicate for item in receipts),
        writer_invoked=bool(receipts),
        writes_runtime=any(item.append_performed for item in receipts),
        failures=tuple(failures),
        safety_flags=config.safety_flags,
    )


def _load_decision_index(payload: Mapping[str, Any]) -> dict[str, RuntimeDecisionProjectionV1]:
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("projection_index_entries_invalid")
    decisions = []
    for entry in entries.values():
        if not isinstance(entry, dict) or entry.get("record_type") != "decision":
            continue
        projection = entry.get("projection")
        decisions.append(RuntimeDecisionProjectionV1.model_validate(projection))
    return build_decision_index(decisions)


def _read_closed_trades_readonly(path: Path) -> list[dict[str, Any]]:
    candidate = path.expanduser().resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("phase14_snapshot_not_regular_file")
    uri = candidate.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(
            "SELECT id, pair, is_short, open_date, enter_tag "
            "FROM trades WHERE is_open = 0 ORDER BY id"
        )
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _decision_event_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _EVENT_TAG.search(value)
    return match.group(1) if match else None


def _parse_utc(value: object) -> datetime:
    text = str(value)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise ValueError("trade_execution_timestamp_must_be_utc")
    if offset.total_seconds() != 0:
        raise ValueError("trade_execution_timestamp_must_use_utc_offset_zero")
    return parsed


def _symbol_from_pair(pair: str) -> str:
    return (
        pair.replace("/", "")
        .replace(":USDT", "")
        .replace(":USD", "")
        .replace("-", "")
        .upper()
    )


def _row_sha256(row: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _failure(row: Mapping[str, Any], error: Exception) -> dict[str, object]:
    return {
        "trade_id": row.get("id"),
        "error_type": type(error).__name__,
        "error_message_sha256": hashlib.sha256(str(error).encode("utf-8")).hexdigest(),
    }


def _blocked(
    config: PaperObservabilityWiringConfigV1,
    *,
    reason: str,
) -> TradeLinkAdapterReportV1:
    return TradeLinkAdapterReportV1(
        status="blocked",
        reason=reason,
        enabled=True,
        source_trade_count=0,
        correlated_trade_count=0,
        projected_trade_link_count=0,
        persisted_trade_link_count=0,
        duplicate_trade_link_count=0,
        writer_invoked=False,
        writes_runtime=False,
        safety_flags=config.safety_flags,
    )


def _resolve_project_relative(root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("observability_index_path_unsafe")
    return (root / Path(*path.parts)).resolve(strict=False)
