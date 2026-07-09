"""Research-only reconciliation for paper autotrain source keys.

This module reconciles paper DB `trade_close:*` identities against CSV/feedback
`order_close:*` identities. It is read-only by default, writes only optional
reports under data/reports, never creates microbatches, never trains, never
promotes, never writes runtime state, and has no authority over Freqtrade,
RiskManager, Qlib, IA Shadow runtime, signals, services, or orders.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_incremental_watermark_fix.watermark import (
    DEFAULT_WATERMARK_PATH,
    read_watermark_state,
    sorted_unique,
)
from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics.diagnostics import (
    CLOSED_TRADES_CSV,
    FEEDBACK_EVENTS,
    parse_datetime_utc,
    resolve_paper_db,
)

SCHEMA_VERSION = "paper_autotrain_source_key_reconciliation_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_source_key_reconciliation_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_source_key_reconciliation_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

DECISION_RECONCILE = "RECONCILIAR_CHAVES_FONTES_PAPER_RESEARCH_ONLY"
DECISION_RECONCILED_BLOCKED = "FONTES_RECONCILIADAS_SEM_AUTORIZACAO_DE_SYNC"
DECISION_PROVIDE_DB = "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
DECISION_INDETERMINATE = "MANTER_BLOQUEADO_ESTADO_INDETERMINADO"

SOURCE_PAPER_DB = "paper_db"
SOURCE_CSV = "closed_trades_csv"
SOURCE_FEEDBACK = "feedback_events"
SOURCE_NAMES = (SOURCE_PAPER_DB, SOURCE_CSV, SOURCE_FEEDBACK)

QUOTE_ASSETS = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB", "BRL")


@dataclass(frozen=True)
class NormalizedRecord:
    source: str
    native_key: str
    reconciliation_key: str
    key_strategy: str
    close_time_utc: str | None
    numeric_id: str | None
    order_id: str | None
    trade_id: str | None
    symbol: str | None
    side: str | None
    pnl: float | None
    record_hash: str | None
    raw_index: int
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class SourceLoad:
    source: str
    status: str
    path: str | None
    row_count: int
    normalized_record_count: int
    new_record_count: int
    reason: str | None
    records: tuple[NormalizedRecord, ...]
    warnings: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ReconciliationPaths:
    watermark_path: Path
    paper_db_path: Path | None
    closed_trades_csv_path: Path
    feedback_events_path: Path
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_source_key_reconciliation_v1(
    *,
    project_root: str | Path,
    paper_db_path: str | Path | None = None,
    allow_paper_db_read: bool = False,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_missing_paper_db: bool = False,
    fail_on_unreconciled_sources: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a read-only source-key reconciliation report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()

    output_json = resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON)
    output_markdown = resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN)
    write_errors = validate_write_request(root, output_json, output_markdown, write_report)

    watermark_path = root / DEFAULT_WATERMARK_PATH
    watermark_read = read_watermark_state(watermark_path)
    watermark_state = watermark_read.state or {}
    watermark_close_time = first_datetime_from_value(watermark_state.get("watermark_close_time_utc"))

    explicit_db = resolve_path(root, paper_db_path, Path("")) if paper_db_path else None
    resolution = resolve_paper_db(
        root=root,
        explicit_db=explicit_db,
        read_requested=allow_paper_db_read,
        watermark_close_time=watermark_close_time,
        closed_csv_new_record_count=0,
        feedback_new_record_count=0,
    )

    paper_db = load_paper_db_source(
        path=resolution.selected_path,
        read_requested=allow_paper_db_read,
        watermark_close_time=watermark_close_time,
        selected_source_kind=resolution.selected_source_kind,
    )
    closed_csv = load_csv_source(root / CLOSED_TRADES_CSV, watermark_close_time)
    feedback = load_feedback_source(root / FEEDBACK_EVENTS, watermark_close_time)

    source_loads = {
        SOURCE_PAPER_DB: paper_db,
        SOURCE_CSV: closed_csv,
        SOURCE_FEEDBACK: feedback,
    }
    groups = build_reconciliation_groups(source_loads)
    source_summary = summarize_sources(source_loads)
    reconciliation_summary = summarize_reconciliation(groups)
    classifications = classify_groups(groups)

    status, reason, decision, blockers, warnings = decide_status(
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        write_errors=write_errors,
        allow_paper_db_read=allow_paper_db_read,
        paper_db=paper_db,
        reconciliation_summary=reconciliation_summary,
        fail_on_missing_paper_db=fail_on_missing_paper_db,
        fail_on_unreconciled_sources=fail_on_unreconciled_sources,
    )

    paths = ReconciliationPaths(
        watermark_path=watermark_path,
        paper_db_path=resolution.selected_path,
        closed_trades_csv_path=root / CLOSED_TRADES_CSV,
        feedback_events_path=root / FEEDBACK_EVENTS,
        output_json=output_json,
        output_markdown=output_markdown,
    )

    all_warnings = sorted_unique(
        [
            *watermark_read.warnings,
            *resolution.warnings,
            *paper_db.warnings,
            *closed_csv.warnings,
            *feedback.warnings,
            *warnings,
        ]
    )
    all_blockers = sorted_unique([*write_errors, *watermark_read.blockers, *blockers])
    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "reconciliation_mode": "read_only_research",
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": bool(watermark_read.exists),
        "watermark_status": watermark_read.status,
        "watermark_close_time_utc": watermark_state.get("watermark_close_time_utc"),
        "watermark_order_id": watermark_state.get("watermark_order_id"),
        "watermark_record_hash": watermark_state.get("watermark_record_hash"),
        "paper_db_read_requested": bool(allow_paper_db_read),
        "paper_db_path": str(paths.paper_db_path) if paths.paper_db_path else None,
        "paper_db_status": paper_db.status,
        "paper_db_source_kind": paper_db.metadata.get("source_kind"),
        "paper_db_authority_status": resolution.authority_status,
        "paper_db_selected_reason": resolution.selected_reason,
        "paper_db_selected_source_kind": resolution.selected_source_kind,
        "paper_db_runtime_stale": resolution.runtime_stale,
        "paper_db_snapshot_fresh": resolution.snapshot_fresh,
        "paper_db_snapshot_aligned_with_exports": resolution.snapshot_aligned_with_exports,
        "source_status": {name: source_to_status(load) for name, load in source_loads.items()},
        "source_summary": source_summary,
        "reconciliation_summary": reconciliation_summary,
        "classification_counts": reconciliation_summary["classification_counts"],
        "coverage_by_source": reconciliation_summary["coverage_by_source"],
        "pairwise_reconciled_counts": reconciliation_summary["pairwise_reconciled_counts"],
        "group_samples_by_classification": classifications["samples"],
        "ambiguous_group_count": reconciliation_summary["classification_counts"]["ambiguous"],
        "conflicting_group_count": reconciliation_summary["classification_counts"]["conflicting"],
        "missing_in_csv_count": reconciliation_summary["classification_counts"]["missing_in_csv"],
        "missing_in_feedback_count": reconciliation_summary["classification_counts"]["missing_in_feedback"],
        "missing_in_db_count": reconciliation_summary["classification_counts"]["missing_in_db"],
        "reconciled_group_count": reconciliation_summary["classification_counts"]["reconciled"],
        "ready_for_microbatch_sync": False,
        "ready_for_sync_execution": False,
        "ready_for_accumulation_recheck": False,
        "ready_for_candidate_evaluation_recheck": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "fail_on_missing_paper_db": bool(fail_on_missing_paper_db),
        "fail_on_unreconciled_sources": bool(fail_on_unreconciled_sources),
        "blockers": all_blockers,
        "warnings": all_warnings,
        "output_paths": {
            "json": str(output_json),
            "markdown": str(output_markdown),
        },
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def load_paper_db_source(
    *,
    path: Path | None,
    read_requested: bool,
    watermark_close_time: datetime | None,
    selected_source_kind: str | None,
) -> SourceLoad:
    if not read_requested:
        return empty_source(SOURCE_PAPER_DB, "not_requested", None, "paper_db_read_not_requested")
    if path is None:
        return empty_source(SOURCE_PAPER_DB, "missing", None, "paper_db_path_not_resolved")
    if not path.exists():
        return empty_source(SOURCE_PAPER_DB, "missing", str(path), "paper_db_file_missing")

    try:
        with sqlite3.connect(path) as conn:
            rows = pd.read_sql_query("SELECT * FROM trades", conn)
    except (sqlite3.Error, pd.errors.DatabaseError, OSError) as exc:
        return empty_source(SOURCE_PAPER_DB, "unreadable", str(path), f"paper_db_unreadable:{type(exc).__name__}")

    records = normalize_frame(
        rows,
        source=SOURCE_PAPER_DB,
        watermark_close_time=watermark_close_time,
    )
    stat = path.stat()
    return SourceLoad(
        source=SOURCE_PAPER_DB,
        status="ok",
        path=str(path),
        row_count=int(len(rows)),
        normalized_record_count=len(records),
        new_record_count=len(records),
        reason=None,
        records=tuple(records),
        warnings=(),
        metadata={
            "source_kind": selected_source_kind,
            "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "max_close_time_utc": max_non_null(record.close_time_utc for record in records),
            "min_close_time_utc": min_non_null(record.close_time_utc for record in records),
        },
    )


def load_csv_source(path: Path, watermark_close_time: datetime | None) -> SourceLoad:
    if not path.exists():
        return empty_source(SOURCE_CSV, "missing", str(path), "closed_trades_csv_missing")
    try:
        rows = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        return empty_source(SOURCE_CSV, "unreadable", str(path), f"closed_trades_csv_unreadable:{type(exc).__name__}")

    records = normalize_frame(
        rows,
        source=SOURCE_CSV,
        watermark_close_time=watermark_close_time,
    )
    return SourceLoad(
        source=SOURCE_CSV,
        status="ok",
        path=str(path),
        row_count=int(len(rows)),
        normalized_record_count=len(records),
        new_record_count=len(records),
        reason=None,
        records=tuple(records),
        warnings=(),
        metadata={
            "source_kind": "csv_export",
            "max_close_time_utc": max_non_null(record.close_time_utc for record in records),
            "min_close_time_utc": min_non_null(record.close_time_utc for record in records),
        },
    )


def load_feedback_source(path: Path, watermark_close_time: datetime | None) -> SourceLoad:
    if not path.exists():
        return empty_source(SOURCE_FEEDBACK, "missing", str(path), "feedback_events_missing")
    raw_rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if isinstance(payload, dict):
                    raw_rows.append(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return empty_source(SOURCE_FEEDBACK, "unreadable", str(path), f"feedback_events_unreadable:{type(exc).__name__}")

    rows = pd.DataFrame(raw_rows)
    records = normalize_frame(
        rows,
        source=SOURCE_FEEDBACK,
        watermark_close_time=watermark_close_time,
    )
    return SourceLoad(
        source=SOURCE_FEEDBACK,
        status="ok",
        path=str(path),
        row_count=len(raw_rows),
        normalized_record_count=len(records),
        new_record_count=len(records),
        reason=None,
        records=tuple(records),
        warnings=(),
        metadata={
            "source_kind": "feedback_jsonl",
            "max_close_time_utc": max_non_null(record.close_time_utc for record in records),
            "min_close_time_utc": min_non_null(record.close_time_utc for record in records),
        },
    )


def normalize_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    watermark_close_time: datetime | None,
) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    if frame.empty:
        return records

    for index, row in frame.iterrows():
        payload = row.to_dict()
        close_time = first_datetime(
            payload,
            (
                "close_time_utc",
                "close_date",
                "close_time",
                "exit_time_utc",
                "horario_fechamento",
                "horario_transacao",
                "closed_at",
            ),
        )
        if close_time is None:
            continue
        if watermark_close_time is not None and close_time <= watermark_close_time:
            continue
        if is_open_trade(payload):
            continue

        order_id = first_text(
            payload,
            (
                "order_id",
                "orderid",
                "ft_order_id",
                "client_order_id",
                "clientOrderId",
            ),
        )
        trade_id = first_text(payload, ("trade_id", "tradeid", "id"))
        symbol = normalize_symbol(first_text(payload, ("symbol", "moeda", "pair", "asset", "base_currency")))
        side = normalize_side(
            first_text(
                payload,
                (
                    "side",
                    "fechar_side",
                    "trade_side",
                    "direction",
                    "direcao_liquidez",
                    "enter_tag",
                    "is_short",
                ),
            )
        )
        pnl = first_float(
            payload,
            (
                "pnl_fechado",
                "net_pnl",
                "pnl",
                "profit_abs",
                "close_profit_abs",
                "realized_profit",
                "closed_profit",
            ),
        )
        record_hash = first_text(payload, ("record_hash", "hash", "event_hash"))

        numeric_id = extract_numeric_id(order_id, trade_id)
        close_iso = close_time.isoformat()
        key_strategy, reconciliation_key = build_reconciliation_key(
            close_time_utc=close_iso,
            numeric_id=numeric_id,
            symbol=symbol,
            side=side,
            pnl=pnl,
        )
        native_key = build_native_key(source, order_id, trade_id, close_iso, int(index))

        records.append(
            NormalizedRecord(
                source=source,
                native_key=native_key,
                reconciliation_key=reconciliation_key,
                key_strategy=key_strategy,
                close_time_utc=close_iso,
                numeric_id=numeric_id,
                order_id=order_id,
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                pnl=pnl,
                record_hash=record_hash,
                raw_index=int(index),
                raw=sanitize_raw(payload),
            )
        )
    return records


def build_reconciliation_groups(source_loads: Mapping[str, SourceLoad]) -> dict[str, dict[str, list[NormalizedRecord]]]:
    groups: dict[str, dict[str, list[NormalizedRecord]]] = defaultdict(lambda: defaultdict(list))
    for source_name in SOURCE_NAMES:
        for record in source_loads[source_name].records:
            groups[record.reconciliation_key][source_name].append(record)
    return {key: dict(value) for key, value in groups.items()}


def classify_groups(groups: Mapping[str, Mapping[str, Sequence[NormalizedRecord]]]) -> dict[str, Any]:
    samples: dict[str, list[dict[str, Any]]] = {
        "reconciled": [],
        "missing_in_csv": [],
        "missing_in_feedback": [],
        "missing_in_db": [],
        "ambiguous": [],
        "conflicting": [],
    }

    for key, source_records in sorted(groups.items()):
        classification = classify_group(source_records)
        if len(samples[classification]) < 25:
            samples[classification].append(group_to_sample(key, source_records, classification))
    return {"samples": samples}


def summarize_reconciliation(groups: Mapping[str, Mapping[str, Sequence[NormalizedRecord]]]) -> dict[str, Any]:
    classification_counts = {
        "reconciled": 0,
        "missing_in_csv": 0,
        "missing_in_feedback": 0,
        "missing_in_db": 0,
        "ambiguous": 0,
        "conflicting": 0,
    }
    coverage_by_source = {name: 0 for name in SOURCE_NAMES}
    pairwise = {
        "paper_db_vs_closed_trades_csv": 0,
        "paper_db_vs_feedback_events": 0,
        "closed_trades_csv_vs_feedback_events": 0,
    }

    for source_records in groups.values():
        classification = classify_group(source_records)
        classification_counts[classification] += 1
        present = {source for source, records in source_records.items() if records}
        for source in present:
            coverage_by_source[source] += 1
        if SOURCE_PAPER_DB in present and SOURCE_CSV in present:
            pairwise["paper_db_vs_closed_trades_csv"] += 1
        if SOURCE_PAPER_DB in present and SOURCE_FEEDBACK in present:
            pairwise["paper_db_vs_feedback_events"] += 1
        if SOURCE_CSV in present and SOURCE_FEEDBACK in present:
            pairwise["closed_trades_csv_vs_feedback_events"] += 1

    total_groups = sum(classification_counts.values())
    unreconciled = total_groups - classification_counts["reconciled"]
    return {
        "total_group_count": total_groups,
        "unreconciled_group_count": unreconciled,
        "classification_counts": classification_counts,
        "coverage_by_source": coverage_by_source,
        "pairwise_reconciled_counts": pairwise,
        "all_sources_reconciled": unreconciled == 0 and total_groups > 0,
    }


def summarize_sources(source_loads: Mapping[str, SourceLoad]) -> dict[str, Any]:
    return {
        name: {
            "status": load.status,
            "row_count": load.row_count,
            "normalized_record_count": load.normalized_record_count,
            "new_record_count": load.new_record_count,
            "path": load.path,
            "reason": load.reason,
            "metadata": dict(load.metadata),
        }
        for name, load in source_loads.items()
    }


def classify_group(source_records: Mapping[str, Sequence[NormalizedRecord]]) -> str:
    present = {source for source, records in source_records.items() if records}
    if any(len(source_records.get(source, ())) > 1 for source in SOURCE_NAMES):
        return "ambiguous"
    if has_field_conflict(source_records):
        return "conflicting"
    if present == set(SOURCE_NAMES):
        return "reconciled"
    if SOURCE_PAPER_DB not in present:
        return "missing_in_db"
    if SOURCE_CSV not in present:
        return "missing_in_csv"
    if SOURCE_FEEDBACK not in present:
        return "missing_in_feedback"
    return "conflicting"


def has_field_conflict(source_records: Mapping[str, Sequence[NormalizedRecord]]) -> bool:
    records = [records[0] for records in source_records.values() if len(records) == 1]
    for field_name in ("symbol", "side"):
        values = {getattr(record, field_name) for record in records if getattr(record, field_name) not in (None, "")}
        if len(values) > 1:
            return True

    pnl_values = [record.pnl for record in records if record.pnl is not None]
    if len(pnl_values) > 1 and max(pnl_values) - min(pnl_values) > 1e-6:
        return True
    return False


def decide_status(
    *,
    watermark_exists: bool,
    watermark_status: str,
    write_errors: Sequence[str],
    allow_paper_db_read: bool,
    paper_db: SourceLoad,
    reconciliation_summary: Mapping[str, Any],
    fail_on_missing_paper_db: bool,
    fail_on_unreconciled_sources: bool,
) -> tuple[str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_INDETERMINATE, list(write_errors), []
    if not watermark_exists:
        return "blocked", "missing_watermark_state", DECISION_INDETERMINATE, ["missing_watermark_state"], []
    if watermark_status == "invalid":
        return "blocked", "watermark_state_invalid", DECISION_INDETERMINATE, ["watermark_state_invalid"], []
    if not allow_paper_db_read:
        return "blocked", "paper_db_read_not_requested", DECISION_PROVIDE_DB, ["paper_db_read_not_requested"], []
    if paper_db.status in {"missing", "unreadable", "invalid_schema"}:
        blockers = ["paper_db_source_missing_or_unreadable"]
        if fail_on_missing_paper_db:
            blockers.append("fail_on_missing_paper_db_triggered")
        return "blocked", "paper_db_source_missing_or_unreadable", DECISION_PROVIDE_DB, blockers, []

    if int(reconciliation_summary["unreconciled_group_count"]) > 0:
        blockers = ["source_key_reconciliation_required"]
        if fail_on_unreconciled_sources:
            blockers.append("fail_on_unreconciled_sources_triggered")
        return (
            "blocked",
            "source_key_reconciliation_required",
            DECISION_RECONCILE,
            blockers,
            ["read_only_reconciliation_available_no_execution_authority"],
        )

    return (
        "blocked",
        "sources_reconciled_no_sync_authority",
        DECISION_RECONCILED_BLOCKED,
        ["sources_reconciled_no_sync_authority"],
        ["read_only_reconciliation_available_no_execution_authority"],
    )


def group_to_sample(
    key: str,
    source_records: Mapping[str, Sequence[NormalizedRecord]],
    classification: str,
) -> dict[str, Any]:
    return {
        "reconciliation_key": key,
        "classification": classification,
        "sources_present": sorted(source for source, records in source_records.items() if records),
        "native_keys_by_source": {
            source: [record.native_key for record in records]
            for source, records in sorted(source_records.items())
        },
        "field_snapshot_by_source": {
            source: [record_to_compact_dict(record) for record in records]
            for source, records in sorted(source_records.items())
        },
    }


def record_to_compact_dict(record: NormalizedRecord) -> dict[str, Any]:
    return {
        "native_key": record.native_key,
        "key_strategy": record.key_strategy,
        "close_time_utc": record.close_time_utc,
        "numeric_id": record.numeric_id,
        "order_id": record.order_id,
        "trade_id": record.trade_id,
        "symbol": record.symbol,
        "side": record.side,
        "pnl": record.pnl,
        "record_hash": record.record_hash,
    }


def build_reconciliation_key(
    *,
    close_time_utc: str,
    numeric_id: str | None,
    symbol: str | None,
    side: str | None,
    pnl: float | None,
) -> tuple[str, str]:
    if numeric_id:
        return "close_time_numeric_id", f"close_id:{numeric_id}|{close_time_utc}"
    if symbol and side and pnl is not None:
        return "close_time_symbol_side_pnl", f"close_sym_side_pnl:{close_time_utc}|{symbol}|{side}|{pnl:.10f}"
    if symbol and side:
        return "close_time_symbol_side", f"close_sym_side:{close_time_utc}|{symbol}|{side}"
    return "close_time_only", f"close_only:{close_time_utc}"


def build_native_key(source: str, order_id: str | None, trade_id: str | None, close_iso: str, raw_index: int) -> str:
    if source == SOURCE_PAPER_DB:
        native_id = trade_id or order_id or f"row-{raw_index}"
        return f"trade_close:{native_id}|{close_iso}"
    native_id = order_id or trade_id or f"row-{raw_index}"
    return f"order_close:{native_id}|{close_iso}"


def first_datetime(payload: Mapping[str, Any], names: Sequence[str]) -> datetime | None:
    for name in names:
        parsed = first_datetime_from_value(get_case_insensitive(payload, name))
        if parsed is not None:
            return parsed
    return None


def first_datetime_from_value(value: Any) -> datetime | None:
    parsed = parse_datetime_utc(value)
    if parsed is not None:
        return parsed
    if value is None or is_null_scalar(value):
        return None
    try:
        timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    except (TypeError, ValueError, pd.errors.ParserError):
        return None
    if pd.isna(timestamp):
        return None
    if isinstance(timestamp, pd.Timestamp):
        return timestamp.to_pydatetime()
    return None


def first_text(payload: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        value = get_case_insensitive(payload, name)
        if value is None:
            continue
        if is_null_scalar(value):
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "nat"}:
            return text
    return None


def first_float(payload: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        value = get_case_insensitive(payload, name)
        if value is None or is_null_scalar(value):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def get_case_insensitive(payload: Mapping[str, Any], name: str) -> Any:
    if name in payload:
        return payload[name]
    lowered = name.lower()
    for key, value in payload.items():
        if str(key).lower() == lowered:
            return value
    return None


def extract_numeric_id(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        matches = re.findall(r"\d+", str(value))
        if matches:
            return str(int(matches[-1]))
    return None


def normalize_symbol(value: str | None) -> str | None:
    if value is None:
        return None

    text = value.upper().strip()
    if not text:
        return None

    if ":" in text:
        text = text.split(":", 1)[0]

    if "/" in text:
        parts = [part for part in text.split("/") if part]
        if len(parts) >= 2:
            text = f"{parts[0]}{parts[1]}"
        else:
            text = "".join(parts)

    text = (
        text.replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .replace("PERP", "")
        .replace(":", "")
        .replace("/", "")
    )

    for quote in QUOTE_ASSETS:
        duplicated_quote = f"{quote}{quote}"
        if text.endswith(duplicated_quote):
            text = text[: -len(quote)]
            break

    return text or None


def normalize_side(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "short", "sell"} or "short" in text:
        return "short"
    if text in {"false", "0", "long", "buy"} or "long" in text:
        return "long"
    return text or None


def is_open_trade(payload: Mapping[str, Any]) -> bool:
    value = get_case_insensitive(payload, "is_open")
    if value is None or is_null_scalar(value):
        return False
    text = str(value).strip().lower()
    return text in {"true", "1", "yes"}


def is_null_scalar(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def sanitize_raw(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): json_safe(value) for key, value in payload.items()}


def source_to_status(source: SourceLoad) -> dict[str, Any]:
    return {
        "status": source.status,
        "path": source.path,
        "row_count": source.row_count,
        "normalized_record_count": source.normalized_record_count,
        "new_record_count": source.new_record_count,
        "reason": source.reason,
        "metadata": dict(source.metadata),
    }


def empty_source(source: str, status: str, path: str | None, reason: str) -> SourceLoad:
    return SourceLoad(
        source=source,
        status=status,
        path=path,
        row_count=0,
        normalized_record_count=0,
        new_record_count=0,
        reason=reason,
        records=(),
        warnings=(),
        metadata={},
    )


def maybe_write_report(
    report: dict[str, Any],
    paths: ReconciliationPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report

    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True

    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = dict(report.get("classification_counts") or {})
    return "\n".join(
        [
            "# Paper Autotrain Source Key Reconciliation V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Paper DB source: `{report.get('paper_db_source_kind')}`",
            f"- Paper DB authority: `{report.get('paper_db_authority_status')}`",
            f"- Total groups: `{report.get('reconciliation_summary', {}).get('total_group_count')}`",
            f"- Reconciled: `{counts.get('reconciled')}`",
            f"- Missing in CSV: `{counts.get('missing_in_csv')}`",
            f"- Missing in feedback: `{counts.get('missing_in_feedback')}`",
            f"- Missing in DB: `{counts.get('missing_in_db')}`",
            f"- Ambiguous: `{counts.get('ambiguous')}`",
            f"- Conflicting: `{counts.get('conflicting')}`",
            "",
            "## Conclusao",
            "",
            "Este reconciliador apenas compara identidades entre fontes paper.",
            "Ele nao cria microbatch, nao treina, nao promove, nao escreve runtime,",
            "nao altera Freqtrade/RiskManager/Qlib/IA Shadow e nao envia ordens.",
            "",
        ]
    )


def validate_write_request(root: Path, output_json: Path, output_markdown: Path, write_report: bool) -> list[str]:
    if not write_report:
        return []
    errors: list[str] = []
    errors.extend(validate_path_under(root, output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    errors.extend(validate_path_under(root, output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    return sorted_unique(errors)


def validate_path_under(root: Path, path: Path, allowed: Path, reason: str) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed).resolve())
    except ValueError:
        return [reason]
    return []


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def safety_flags(*, write_report_requested: bool, write_report_performed: bool) -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "read_only": not write_report_requested,
        "write_report_requested": bool(write_report_requested),
        "write_report_performed": bool(write_report_performed),
        "write_performed": bool(write_report_performed),
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "runs_training": False,
        "trains_model": False,
        "training_allowed": False,
        "ready_for_training": False,
        "promotes_model": False,
        "promotion_allowed": False,
        "ready_for_promotion": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "runtime_allowed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_operational_parquet": False,
        "writes_active_registry": False,
        "writes_quarantine_registry": False,
        "writes_active_model_artifact": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "updates_freqtrade": False,
        "updates_freqtrade_config": False,
        "updates_freqtrade_strategy": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "qlib_runtime_updated": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "ai_shadow_runtime_updated": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
        "ready_for_microbatch_sync": False,
        "ready_for_sync_execution": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_promote_model": False,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def max_non_null(values: Iterable[str | None]) -> str | None:
    filtered = [value for value in values if value is not None]
    return max(filtered) if filtered else None


def min_non_null(values: Iterable[str | None]) -> str | None:
    filtered = [value for value in values if value is not None]
    return min(filtered) if filtered else None
