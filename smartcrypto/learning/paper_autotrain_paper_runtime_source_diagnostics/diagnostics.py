"""Research-only diagnostics for paper runtime sources after autotrain watermark.

The diagnostic compares local paper/research sources against the incremental
watermark and classifies whether new closed paper trades exist, whether exports
or feedback are lagging, or whether the authoritative paper DB is missing or
unreadable. It never writes runtime state, creates microbatches, trains,
promotes, sends orders, or touches Freqtrade/RiskManager/Qlib/IA Shadow runtime.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_incremental_watermark_fix.watermark import (
    DEFAULT_QUARANTINE_DIR,
    DEFAULT_WATERMARK_PATH,
    load_existing_microbatches,
    load_seen_keys,
    normalize_records,
    read_watermark_state,
    sorted_unique,
    summarize_normalized_records,
)

SCHEMA_VERSION = "paper_autotrain_paper_runtime_source_diagnostics_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

NEW_TRADES_GATE_REPORT = Path("data/reports/paper_autotrain_new_trades_readiness_gate_v1.json")
ACCUMULATION_RECHECK_REPORT = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json")
WATERMARK_FIX_REPORT = Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.json")
FRESHNESS_REPORT = Path("data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json")
ACTIVATION_REPORT = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
CANDIDATE_EVALUATION_REPORT = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json")
FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")
CLOSED_TRADES_CSV = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")

PAPER_DB_CANDIDATES = (
    Path("user_data/tradesv3.sqlite"),
    Path("user_data/tradesv3.dryrun.sqlite"),
    Path("freqtrade/user_data/tradesv3.sqlite"),
    Path("freqtrade/user_data/tradesv3.dryrun.sqlite"),
    Path("data/runtime/freqtrade/tradesv3.sqlite"),
    Path("data/runtime/freqtrade/tradesv3.dryrun.sqlite"),
    Path("data/freqtrade/tradesv3.sqlite"),
    Path("data/freqtrade/tradesv3.dryrun.sqlite"),
)

DECISION_WAIT_NEW_TRADES = "AGUARDAR_NOVOS_TRADES_PAPER"
DECISION_SYNC_EXPORTS = "SINCRONIZAR_EXPORTS_FEEDBACK_PAPER"
DECISION_SYNC_MICROBATCH = "SINCRONIZAR_MICROBATCHES_PAPER"
DECISION_FIX_DIVERGENCE = "INVESTIGAR_DIVERGENCIA_FONTES_PAPER"
DECISION_PROVIDE_DB = "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
DECISION_INDETERMINATE = "MANTER_BLOQUEADO_ESTADO_INDETERMINADO"


@dataclass(frozen=True)
class DiagnosticPaths:
    watermark_path: Path
    quarantine_dir: Path
    feedback_events_path: Path
    closed_trades_csv_path: Path
    paper_db_path: Path | None
    output_json: Path
    output_markdown: Path


@dataclass(frozen=True)
class SourceRecords:
    name: str
    status: str
    path: str | None
    row_count: int
    unique_record_count: int
    new_record_count: int
    already_seen_record_count: int
    record_keys: set[str]
    new_record_keys: set[str]
    warnings: tuple[str, ...] = ()
    reason: str | None = None
    source_file_count: int = 0


def build_paper_autotrain_paper_runtime_source_diagnostics_v1(
    *,
    project_root: str | Path,
    paper_db_path: str | Path | None = None,
    allow_paper_db_read: bool = False,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_missing_paper_db: bool = False,
    fail_on_new_db_trades: bool = False,
    fail_on_source_divergence: bool = False,
    fail_on_missing_watermark: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the runtime source diagnostic report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, paper_db_path, output_json_path, output_markdown_path, allow_paper_db_read)
    output_paths = {"json": str(paths.output_json), "markdown": str(paths.output_markdown)}
    write_errors = validate_write_request(root, paths, write_report)
    optional_sources = load_optional_reports(root)
    watermark_read = read_watermark_state(paths.watermark_path)
    seen_keys = set(load_seen_keys(watermark_read.state))

    paper_db = load_paper_db_records(paths.paper_db_path, seen_keys, allow_paper_db_read)
    closed_csv = load_csv_records("closed_trades_csv", paths.closed_trades_csv_path, seen_keys)
    feedback = load_feedback_records(paths.feedback_events_path, seen_keys)
    microbatch = load_microbatch_records(paths.quarantine_dir, seen_keys)
    sources = {
        "paper_db": paper_db,
        "closed_trades_csv": closed_csv,
        "feedback_events": feedback,
        "microbatch": microbatch,
    }

    warnings = [
        *optional_sources["warnings"],
        *watermark_read.warnings,
        *paper_db.warnings,
        *closed_csv.warnings,
        *feedback.warnings,
        *microbatch.warnings,
    ]
    blockers = [*write_errors, *watermark_read.blockers]
    status, reason, decision, source_diagnosis, status_blockers, status_warnings = decide_status(
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        paper_db=paper_db,
        closed_csv=closed_csv,
        feedback=feedback,
        microbatch=microbatch,
        fail_on_missing_paper_db=fail_on_missing_paper_db,
        fail_on_new_db_trades=fail_on_new_db_trades,
        fail_on_source_divergence=fail_on_source_divergence,
        fail_on_missing_watermark=fail_on_missing_watermark,
        write_errors=write_errors,
    )
    blockers.extend(status_blockers)
    warnings.extend(status_warnings)

    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_diagnosis": source_diagnosis,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": bool(watermark_read.exists),
        "watermark_status": watermark_read.status,
        "watermark_seen_record_count": len(seen_keys),
        "watermark_seen_record_keys_sha256": (watermark_read.state or {}).get("seen_record_keys_sha256"),
        "watermark_close_time_utc": (watermark_read.state or {}).get("watermark_close_time_utc"),
        "watermark_order_id": (watermark_read.state or {}).get("watermark_order_id"),
        "watermark_record_hash": (watermark_read.state or {}).get("watermark_record_hash"),
        "paper_db_read_requested": bool(allow_paper_db_read),
        "paper_db_path": str(paths.paper_db_path) if paths.paper_db_path else None,
        "paper_db_exists": bool(paths.paper_db_path.exists()) if paths.paper_db_path is not None else False,
        "paper_db_error": paper_db.reason if paper_db.status in {"missing", "unreadable", "invalid_schema"} else None,
        "paper_db_status": paper_db.status,
        "paper_db_row_count": paper_db.row_count,
        "paper_db_unique_record_count": paper_db.unique_record_count,
        "paper_db_new_record_count": paper_db.new_record_count,
        "paper_db_new_after_watermark_count": paper_db.new_record_count,
        "closed_trades_csv_status": closed_csv.status,
        "closed_trades_csv_row_count": closed_csv.row_count,
        "closed_trades_csv_new_record_count": closed_csv.new_record_count,
        "feedback_status": feedback.status,
        "feedback_event_count": feedback.row_count,
        "feedback_new_record_count": feedback.new_record_count,
        "microbatch_status": microbatch.status,
        "microbatch_source_file_count": microbatch.source_file_count,
        "microbatch_row_count": microbatch.row_count,
        "microbatch_unique_record_count": microbatch.unique_record_count,
        "microbatch_new_record_count": microbatch.new_record_count,
        "new_records_by_source": {
            name: source.new_record_count for name, source in sources.items()
        },
        "source_rows_by_source": {
            name: source.row_count for name, source in sources.items()
        },
        "source_status": {
            name: source_to_status(source) for name, source in sources.items()
        },
        "divergence_summary": build_divergence_summary(paper_db, closed_csv, feedback, microbatch),
        "ready_for_accumulation_recheck": False,
        "ready_for_candidate_evaluation_recheck": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "would_create_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "optional_source_status": optional_sources["source_status"],
        "fail_on_missing_paper_db": bool(fail_on_missing_paper_db),
        "fail_on_new_db_trades": bool(fail_on_new_db_trades),
        "fail_on_source_divergence": bool(fail_on_source_divergence),
        "fail_on_missing_watermark": bool(fail_on_missing_watermark),
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def build_paths(
    root: Path,
    paper_db_path: str | Path | None,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
    allow_paper_db_read: bool,
) -> DiagnosticPaths:
    explicit_db = resolve_path(root, paper_db_path, Path("")) if paper_db_path else None
    return DiagnosticPaths(
        watermark_path=root / DEFAULT_WATERMARK_PATH,
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        feedback_events_path=root / FEEDBACK_EVENTS,
        closed_trades_csv_path=root / CLOSED_TRADES_CSV,
        paper_db_path=resolve_paper_db(root, explicit_db) if allow_paper_db_read else None,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def resolve_paper_db(root: Path, explicit_db: Path | None) -> Path | None:
    if explicit_db is not None:
        return explicit_db
    for candidate in PAPER_DB_CANDIDATES:
        path = root / candidate
        if path.exists():
            return path
    return None


def decide_status(
    *,
    watermark_exists: bool,
    watermark_status: str,
    paper_db: SourceRecords,
    closed_csv: SourceRecords,
    feedback: SourceRecords,
    microbatch: SourceRecords,
    fail_on_missing_paper_db: bool,
    fail_on_new_db_trades: bool,
    fail_on_source_divergence: bool,
    fail_on_missing_watermark: bool,
    write_errors: Sequence[str],
) -> tuple[str, str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_INDETERMINATE, "write_boundary_invalid", list(write_errors), []
    if not watermark_exists:
        blockers = ["missing_watermark_state"]
        if fail_on_missing_watermark:
            blockers.append("fail_on_missing_watermark_triggered")
        return "blocked", "missing_watermark_state", DECISION_INDETERMINATE, "missing_watermark", blockers, []
    if watermark_status == "invalid":
        return "blocked", "watermark_state_invalid", DECISION_INDETERMINATE, "invalid_watermark", ["watermark_state_invalid"], []
    if paper_db.status in {"missing", "unreadable", "invalid_schema"}:
        blockers = ["paper_db_source_missing_or_unreadable"]
        if fail_on_missing_paper_db:
            blockers.append("fail_on_missing_paper_db_triggered")
        return "blocked", "paper_db_source_missing_or_unreadable", DECISION_PROVIDE_DB, "paper_db_absent_or_unreadable", blockers, []
    if paper_db.new_record_count > 0 and max(closed_csv.new_record_count, feedback.new_record_count, microbatch.new_record_count) == 0:
        blockers = ["paper_db_new_trades_not_exported"]
        if fail_on_new_db_trades:
            blockers.append("fail_on_new_db_trades_triggered")
        return (
            "blocked",
            "paper_db_new_trades_not_exported",
            DECISION_SYNC_EXPORTS,
            "paper_db_ahead_of_exports",
            blockers,
            [],
        )
    if max(closed_csv.new_record_count, feedback.new_record_count) > 0 and microbatch.new_record_count == 0:
        return (
            "blocked",
            "exports_feedback_new_trades_not_microbatched",
            DECISION_SYNC_MICROBATCH,
            "exports_ahead_of_microbatch",
            ["exports_feedback_new_trades_not_microbatched"],
            [],
        )
    if sources_diverge(paper_db, closed_csv, feedback, microbatch):
        blockers = ["paper_source_divergence_detected"]
        if fail_on_source_divergence:
            blockers.append("fail_on_source_divergence_triggered")
        return "blocked", "paper_source_divergence_detected", DECISION_FIX_DIVERGENCE, "source_divergence", [
            *blockers
        ], []
    if max(paper_db.new_record_count, closed_csv.new_record_count, feedback.new_record_count, microbatch.new_record_count) == 0:
        return (
            "blocked",
            "no_new_closed_paper_trades_after_watermark",
            DECISION_WAIT_NEW_TRADES,
            "no_new_closed_paper_trades_after_watermark",
            ["no_new_closed_paper_trades_after_watermark"],
            [],
        )
    return "blocked", "paper_source_state_indeterminate", DECISION_INDETERMINATE, "indeterminate", [
        "paper_source_state_indeterminate"
    ], []


def load_paper_db_records(path: Path | None, seen_keys: set[str], read_requested: bool) -> SourceRecords:
    if not read_requested:
        return empty_source("paper_db", "not_requested", None, "paper_db_read_not_requested")
    if path is None:
        return empty_source("paper_db", "missing", None, "paper_db_not_found")
    if not path.exists():
        return empty_source("paper_db", "missing", str(path), "paper_db_not_found")
    try:
        with sqlite3.connect(readonly_sqlite_uri(path), uri=True, timeout=10) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
                ).fetchall()
            ]
            table = pick_trade_table(tables)
            if table is None:
                return empty_source("paper_db", "invalid_schema", str(path), "trades_table_missing")
            frame = pd.read_sql_query(f'SELECT * FROM "{table}"', connection)
    except (sqlite3.Error, OSError, pd.errors.DatabaseError) as exc:
        return empty_source("paper_db", "unreadable", str(path), f"paper_db_read_failed:{exc.__class__.__name__}")
    normalized = normalize_source_frame(frame)
    schema_errors = validate_trade_source_schema(normalized)
    if schema_errors:
        return empty_source("paper_db", "invalid_schema", str(path), "paper_db_invalid_schema")
    return source_records_from_frame("paper_db", str(path), normalized, seen_keys)


def validate_trade_source_schema(frame: pd.DataFrame) -> list[str]:
    columns = set(str(column) for column in frame.columns)
    errors: list[str] = []
    if not columns.intersection({"record_hash", "order_id", "trade_id"}):
        errors.append("missing_record_identity")
    if "close_time_utc" not in columns:
        errors.append("missing_close_time_utc")
    if "symbol" not in columns:
        errors.append("missing_symbol")
    return errors


def pick_trade_table(tables: Sequence[str]) -> str | None:
    preferred = ("trades", "Trade", "orders")
    for name in preferred:
        if name in tables:
            return name
    return tables[0] if tables else None


def readonly_sqlite_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def load_csv_records(name: str, path: Path, seen_keys: set[str]) -> SourceRecords:
    if not path.exists():
        return empty_source(name, "missing", str(path), f"{name}_missing")
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return empty_source(name, "unreadable", str(path), f"{name}_read_failed:{exc.__class__.__name__}")
    normalized = normalize_source_frame(frame)
    return source_records_from_frame(name, str(path), normalized, seen_keys)


def load_feedback_records(path: Path, seen_keys: set[str]) -> SourceRecords:
    if not path.exists():
        return empty_source("feedback_events", "missing", str(path), "feedback_events_missing")
    rows: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return empty_source("feedback_events", "unreadable", str(path), f"feedback_events_read_failed:{exc.__class__.__name__}")
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"feedback_event_invalid_json:line_{index}")
            continue
        if isinstance(payload, Mapping):
            rows.append(payload)
    frame = pd.DataFrame([dict(row) for row in rows])
    source = source_records_from_frame("feedback_events", str(path), normalize_source_frame(frame), seen_keys)
    return SourceRecords(
        name=source.name,
        status=source.status,
        path=source.path,
        row_count=source.row_count,
        unique_record_count=source.unique_record_count,
        new_record_count=source.new_record_count,
        already_seen_record_count=source.already_seen_record_count,
        record_keys=source.record_keys,
        new_record_keys=source.new_record_keys,
        warnings=tuple(sorted_unique([*source.warnings, *warnings])),
        reason=source.reason,
    )


def load_microbatch_records(quarantine_dir: Path, seen_keys: set[str]) -> SourceRecords:
    loaded = load_existing_microbatches(quarantine_dir)
    if loaded["frame"].empty:
        return empty_source("microbatch", "missing", str(quarantine_dir), "microbatch_sources_missing")
    source = source_records_from_frame("microbatch", str(quarantine_dir), normalize_source_frame(loaded["frame"]), seen_keys)
    return SourceRecords(
        name=source.name,
        status=source.status,
        path=source.path,
        row_count=source.row_count,
        unique_record_count=source.unique_record_count,
        new_record_count=source.new_record_count,
        already_seen_record_count=source.already_seen_record_count,
        record_keys=source.record_keys,
        new_record_keys=source.new_record_keys,
        warnings=source.warnings,
        reason=source.reason,
        source_file_count=int(loaded["source_file_count"]),
    )


def normalize_source_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    normalized = frame.copy()
    rename_map = {
        "pair": "symbol",
        "moeda": "symbol",
        "id": "trade_id",
        "is_short": "side",
        "open_date": "open_time_utc",
        "close_date": "close_time_utc",
        "close_timestamp": "close_time_utc",
        "profit_abs": "pnl_fechado",
    }
    for source, target in rename_map.items():
        if source in normalized.columns and target not in normalized.columns:
            normalized[target] = normalized[source]
    if "side" in normalized.columns:
        normalized["side"] = normalized["side"].map(normalize_side)
    return normalized


def normalize_side(value: Any) -> str:
    if isinstance(value, bool):
        return "short" if value else "long"
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return "short"
    if text in {"false", "0"}:
        return "long"
    return text


def source_records_from_frame(
    name: str,
    path: str | None,
    frame: pd.DataFrame,
    seen_keys: set[str] | None = None,
) -> SourceRecords:
    if frame.empty:
        return empty_source(name, "empty", path, f"{name}_empty")
    normalized = normalize_records(frame)
    summary = summarize_normalized_records(normalized)
    record_keys = set(str(key) for key in summary["unique_record_keys"])
    known = seen_keys or set()
    new_keys = record_keys.difference(known)
    already_seen = record_keys.intersection(known)
    return SourceRecords(
        name=name,
        status="ok",
        path=path,
        row_count=int(len(frame)),
        unique_record_count=int(summary["unique_record_count"]),
        new_record_count=len(new_keys),
        already_seen_record_count=len(already_seen),
        record_keys=record_keys,
        new_record_keys=new_keys,
    )


def empty_source(name: str, status: str, path: str | None, reason: str) -> SourceRecords:
    return SourceRecords(
        name=name,
        status=status,
        path=path,
        row_count=0,
        unique_record_count=0,
        new_record_count=0,
        already_seen_record_count=0,
        record_keys=set(),
        new_record_keys=set(),
        warnings=(reason,),
        reason=reason,
    )


def sources_diverge(*sources: SourceRecords) -> bool:
    ok_sources = [source for source in sources if source.status == "ok"]
    if len(ok_sources) < 2:
        return False
    key_sets = [source.record_keys for source in ok_sources if source.record_keys]
    if len(key_sets) < 2:
        return False
    first = key_sets[0]
    return any(keys != first for keys in key_sets[1:])


def build_divergence_summary(*sources: SourceRecords) -> dict[str, Any]:
    by_name = {source.name: source for source in sources}
    paper_db = by_name.get("paper_db")
    microbatch = by_name.get("microbatch")
    closed_csv = by_name.get("closed_trades_csv")
    feedback = by_name.get("feedback_events")
    return {
        "paper_db_vs_microbatch_missing_in_microbatch": sorted((paper_db.record_keys - microbatch.record_keys) if paper_db and microbatch else [])[:25],
        "csv_vs_microbatch_missing_in_microbatch": sorted((closed_csv.record_keys - microbatch.record_keys) if closed_csv and microbatch else [])[:25],
        "feedback_vs_microbatch_missing_in_microbatch": sorted((feedback.record_keys - microbatch.record_keys) if feedback and microbatch else [])[:25],
        "divergence_detected": sources_diverge(*sources),
    }


def source_to_status(source: SourceRecords) -> dict[str, Any]:
    return {
        "status": source.status,
        "path": source.path,
        "row_count": source.row_count,
        "unique_record_count": source.unique_record_count,
        "new_record_count": source.new_record_count,
        "already_seen_record_count": source.already_seen_record_count,
        "reason": source.reason,
    }


def load_optional_reports(root: Path) -> dict[str, Any]:
    source_status: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name, relative_path in {
        "new_trades_readiness_gate": NEW_TRADES_GATE_REPORT,
        "accumulation_recheck": ACCUMULATION_RECHECK_REPORT,
        "watermark_fix": WATERMARK_FIX_REPORT,
        "freshness": FRESHNESS_REPORT,
        "activation": ACTIVATION_REPORT,
        "candidate_evaluation": CANDIDATE_EVALUATION_REPORT,
    }.items():
        path = root / relative_path
        if not path.exists():
            source_status[name] = {"path": str(path), "status": "missing_optional"}
            warnings.append(f"optional_source_missing:{name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            source_status[name] = {"path": str(path), "status": "invalid_optional", "error": exc.__class__.__name__}
            warnings.append(f"optional_source_invalid:{name}:{exc.__class__.__name__}")
            continue
        source_status[name] = {
            "path": str(path),
            "status": "ok",
            "schema_version": payload.get("schema_version"),
            "report_status": payload.get("status"),
            "reason": payload.get("reason"),
            "decision": payload.get("decision"),
        }
    return {"source_status": source_status, "warnings": sorted_unique(warnings)}


def maybe_write_report(
    report: dict[str, Any],
    paths: DiagnosticPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report
    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True
    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Autotrain Paper Runtime Source Diagnostics V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Source diagnosis: `{report.get('source_diagnosis')}`",
            f"- Paper DB status: `{report.get('paper_db_status')}`",
            f"- Paper DB new records: `{report.get('paper_db_new_record_count')}`",
            f"- CSV new records: `{report.get('closed_trades_csv_new_record_count')}`",
            f"- Feedback new records: `{report.get('feedback_new_record_count')}`",
            f"- Microbatch new records: `{report.get('microbatch_new_record_count')}`",
            f"- Watermark status: `{report.get('watermark_status')}`",
            f"- Watermark seen records: `{report.get('watermark_seen_record_count')}`",
            "",
            "## Conclusao",
            "",
            "Este diagnostico apenas identifica a fonte do gargalo de evidencia paper.",
            "Ele nao cria microbatch, nao treina, nao avalia candidato, nao promove e nao altera runtime.",
            "",
        ]
    )


def validate_write_request(root: Path, paths: DiagnosticPaths, write_report: bool) -> list[str]:
    if not write_report:
        return []
    errors: list[str] = []
    errors.extend(validate_path_under(root, paths.output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    errors.extend(validate_path_under(root, paths.output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
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
        "updates_ai_shadow_thresholds": False,
        "ai_shadow_runtime_updated": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
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
