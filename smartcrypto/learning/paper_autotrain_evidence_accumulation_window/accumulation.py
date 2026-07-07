"""Deterministic, research-only evidence accumulation for paper auto-training quarantine.

This module discovers historical quarantine microbatches, accumulates and
deterministically deduplicates their rows, and computes whether enough
statistical evidence has accumulated to justify a future, separate branch
re-evaluating quarantine candidates. It never trains a model, never promotes
a candidate, never updates Qlib/IA Shadow/Freqtrade/RiskManager runtime, and
never writes outside the two explicitly allowed output roots.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autotrain_evidence_accumulation_window_v1"

DECISION_WAIT_FOR_EVIDENCE = "AGUARDAR_MAIS_EVIDENCIA"
DECISION_RECHECK_ALLOWED = "REAVALIACAO_DE_CANDIDATOS_PERMITIDA_EM_BRANCH_SEPARADA"

DEFAULT_RESEARCH_QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
MICROBATCH_FILENAME = "incremental_training_microbatch.parquet"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_evidence_accumulation_window_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_evidence_accumulation_window_v1.md")
DEFAULT_OUTPUT_DATASET = Path(
    "data/research/paper_autotrain_evidence_accumulation_window/accumulated_microbatch.parquet"
)
DATASET_MANIFEST_FILENAME = "accumulated_microbatch_manifest.json"

ALLOWED_REPORT_ROOT = Path("data/reports")
ALLOWED_DATASET_ROOT = Path("data/research/paper_autotrain_evidence_accumulation_window")

MIN_ACCUMULATED_ROWS = 100
MIN_CLASS_POSITIVE_COUNT = 20
MIN_CLASS_NEGATIVE_COUNT = 20
MIN_FEATURE_COUNT = 5
MIN_DISTINCT_RUN_IDS = 1
MAX_DUPLICATE_RATE = 0.05

EVENT_ID_COLUMN = "event_id"
TRADE_OR_ORDER_ID_ALIASES = ("trade_id", "order_id")
CLOSE_TIME_ALIASES = ("close_time_utc", "close_time")
OPEN_TIME_ALIASES = ("open_time_utc", "open_time")
PNL_ALIASES = ("net_pnl", "pnl_fechado")
SYMBOL_COLUMN = "symbol"
SIDE_COLUMN = "side"
TARGET_COLUMN_ALIASES = ("target_profitable", "target")

INTERNAL_SOURCE_COLUMN = "__accumulator_source_file__"
INTERNAL_RUN_ID_COLUMN = "__accumulator_run_id__"
INTERNAL_DEDUP_KEY_COLUMN = "__accumulator_dedup_key__"
INTERNAL_COLUMN_PREFIX = "__accumulator_"


@dataclass(frozen=True)
class AccumulationPaths:
    research_dir: Path
    output_json: Path
    output_markdown: Path
    output_dataset: Path
    output_dataset_manifest: Path


def build_paper_autotrain_evidence_accumulation_window_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    write_accumulated_dataset: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    output_dataset_path: str | Path | None = None,
    fail_on_operational_write: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the paper auto-training quarantine evidence accumulation report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path, output_dataset_path)
    output_paths = {
        "json": str(paths.output_json),
        "markdown": str(paths.output_markdown),
        "dataset": str(paths.output_dataset),
        "dataset_manifest": str(paths.output_dataset_manifest),
    }

    write_errors: list[str] = []
    if write_report or fail_on_operational_write:
        write_errors.extend(validate_write_path(root, paths.output_json, ALLOWED_REPORT_ROOT))
        write_errors.extend(validate_write_path(root, paths.output_markdown, ALLOWED_REPORT_ROOT))
    if write_accumulated_dataset or fail_on_operational_write:
        write_errors.extend(validate_write_path(root, paths.output_dataset, ALLOWED_DATASET_ROOT))
        write_errors.extend(validate_write_path(root, paths.output_dataset_manifest, ALLOWED_DATASET_ROOT))
    write_errors = sorted_unique(write_errors)

    source_paths = discover_microbatch_sources(paths.research_dir)
    combined, source_records, load_errors = load_and_accumulate_sources(source_paths)
    has_sources = bool(source_records)

    accumulated_frame = pd.DataFrame()
    dedup_key_strategy_used = "not_applicable"
    duplicate_rows_removed = 0
    duplicate_rate: float | None = None
    accumulated_row_count = 0
    positive_count = 0
    negative_count = 0
    feature_count = 0
    distinct_run_count = len({record["run_id"] for record in source_records})
    source_row_count = int(len(combined))

    if has_sources and not combined.empty:
        dedup_key_strategy_used, key_series = compute_dedup_keys(combined)
        deduped = combined.assign(**{INTERNAL_DEDUP_KEY_COLUMN: key_series}).drop_duplicates(
            subset=[INTERNAL_DEDUP_KEY_COLUMN], keep="first"
        )
        accumulated_frame = deduped.drop(columns=[INTERNAL_DEDUP_KEY_COLUMN])
        accumulated_row_count = int(len(accumulated_frame))
        duplicate_rows_removed = source_row_count - accumulated_row_count
        duplicate_rate = round(duplicate_rows_removed / source_row_count, 10) if source_row_count > 0 else 0.0
        positive_count, negative_count = compute_class_counts(accumulated_frame)
        feature_count = count_feature_columns(accumulated_frame)

    class_balance = {"0": negative_count, "1": positive_count}

    status, reason, decision, blockers = decide_status(
        write_errors=write_errors,
        has_sources=has_sources,
        load_errors=load_errors,
        accumulated_row_count=accumulated_row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        feature_count=feature_count,
        distinct_run_count=distinct_run_count,
        duplicate_rate=duplicate_rate,
    )
    warnings = sorted_unique(load_errors) if has_sources else []

    accumulation_ready = status == "ok"
    safety = safety_flags(
        write_report_requested=write_report,
        write_report_performed=False,
        write_dataset_requested=write_accumulated_dataset,
        write_dataset_performed=False,
        candidate_recheck_allowed=accumulation_ready,
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": len(source_records),
        "source_files": sorted(record["path"] for record in source_records),
        "source_row_count": source_row_count,
        "duplicate_rows_removed": duplicate_rows_removed,
        "duplicate_rate": duplicate_rate,
        "max_duplicate_rate": MAX_DUPLICATE_RATE,
        "dedup_key_strategy_used": dedup_key_strategy_used,
        "accumulated_row_count": accumulated_row_count,
        "min_accumulated_rows": MIN_ACCUMULATED_ROWS,
        "observed_class_positive_count": positive_count,
        "observed_class_negative_count": negative_count,
        "min_class_positive_count": MIN_CLASS_POSITIVE_COUNT,
        "min_class_negative_count": MIN_CLASS_NEGATIVE_COUNT,
        "observed_feature_count": feature_count,
        "min_feature_count": MIN_FEATURE_COUNT,
        "observed_distinct_run_count": distinct_run_count,
        "min_distinct_run_ids": MIN_DISTINCT_RUN_IDS,
        "class_balance": class_balance,
        "accumulation_ready_for_candidate_recheck": accumulation_ready,
        "candidate_recheck_allowed": accumulation_ready,
        "blockers": blockers,
        "warnings": warnings,
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }

    if write_report and not write_errors:
        write_json(paths.output_json, report)
        paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
        safety = safety_flags(
            write_report_requested=True,
            write_report_performed=True,
            write_dataset_requested=write_accumulated_dataset,
            write_dataset_performed=False,
            candidate_recheck_allowed=accumulation_ready,
        )
        report.update(safety)
        report["safety_flags"] = safety

    if write_accumulated_dataset and not write_errors:
        dataset_hash = write_accumulated_dataset_files(paths, accumulated_frame, report)
        safety = safety_flags(
            write_report_requested=write_report,
            write_report_performed=bool(report.get("write_report_performed")),
            write_dataset_requested=True,
            write_dataset_performed=True,
            candidate_recheck_allowed=accumulation_ready,
        )
        report.update(safety)
        report["safety_flags"] = safety
        report["accumulated_dataset_sha256"] = dataset_hash
        if write_report and not write_errors:
            write_json(paths.output_json, report)
            paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")

    return report


def build_paths(
    root: Path,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
    output_dataset_path: str | Path | None,
) -> AccumulationPaths:
    output_dataset = resolve_path(root, output_dataset_path, DEFAULT_OUTPUT_DATASET)
    return AccumulationPaths(
        research_dir=root / DEFAULT_RESEARCH_QUARANTINE_DIR,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
        output_dataset=output_dataset,
        output_dataset_manifest=output_dataset.parent / DATASET_MANIFEST_FILENAME,
    )


def validate_write_path(root: Path, path: Path, allowed_root: Path) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed_root).resolve())
    except ValueError:
        return [f"write_path_outside_allowed_root:{allowed_root.as_posix()}"]
    return []


def discover_microbatch_sources(research_dir: Path) -> list[Path]:
    if not research_dir.is_dir():
        return []
    return sorted(research_dir.glob(f"*/{MICROBATCH_FILENAME}"))


def load_and_accumulate_sources(paths: Sequence[Path]) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    frames: list[pd.DataFrame] = []
    source_records: list[dict[str, Any]] = []
    load_errors: list[str] = []
    for path in paths:
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError, ImportError) as exc:
            load_errors.append(f"source_read_failed:{path.name}:{exc.__class__.__name__}")
            continue
        working = frame.copy()
        working[INTERNAL_SOURCE_COLUMN] = str(path)
        working[INTERNAL_RUN_ID_COLUMN] = path.parent.name
        frames.append(working)
        source_records.append({"path": str(path), "row_count": int(len(frame)), "run_id": path.parent.name})
    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=True)
    else:
        combined = pd.DataFrame()
    return combined, source_records, load_errors


def select_dedup_strategy(columns: set[str]) -> tuple[str, list[str]]:
    if EVENT_ID_COLUMN in columns:
        return "event_id", [EVENT_ID_COLUMN]
    id_column = first_present(columns, TRADE_OR_ORDER_ID_ALIASES)
    close_column = first_present(columns, CLOSE_TIME_ALIASES)
    if id_column and close_column:
        return "trade_or_order_id_close_time", [id_column, close_column]
    symbol_ok = SYMBOL_COLUMN in columns
    side_ok = SIDE_COLUMN in columns
    open_column = first_present(columns, OPEN_TIME_ALIASES)
    pnl_column = first_present(columns, PNL_ALIASES)
    if symbol_ok and side_ok and open_column and close_column and pnl_column:
        return "symbol_side_open_time_close_time_net_pnl", [
            SYMBOL_COLUMN,
            SIDE_COLUMN,
            open_column,
            close_column,
            pnl_column,
        ]
    return "normalized_row_hash", []


def compute_dedup_keys(frame: pd.DataFrame) -> tuple[str, pd.Series]:
    working = frame.reset_index(drop=True)
    strategy, key_columns = select_dedup_strategy(set(working.columns))
    if strategy == "normalized_row_hash":
        return strategy, normalized_row_hash_series(working)
    null_mask = working[key_columns].isna().any(axis=1)
    joined = working[key_columns].astype(str).agg("|".join, axis=1)
    fallback = pd.Series([f"__incomplete_key_row__:{i}" for i in working.index], index=working.index)
    return strategy, joined.where(~null_mask, fallback)


def normalized_row_hash_series(frame: pd.DataFrame) -> pd.Series:
    content_columns = sorted(
        column for column in frame.columns if not str(column).startswith(INTERNAL_COLUMN_PREFIX)
    )
    subset = frame[content_columns]

    def hash_row(row: pd.Series) -> str:
        parts = [f"{column}={normalize_scalar(row[column])}" for column in content_columns]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    return subset.apply(hash_row, axis=1)


def normalize_scalar(value: Any) -> str:
    if value is None:
        return "null"
    try:
        if pd.isna(value):
            return "null"
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        return repr(round(value, 10))
    return str(value)


def compute_class_counts(frame: pd.DataFrame) -> tuple[int, int]:
    target_column = first_present(set(frame.columns), TARGET_COLUMN_ALIASES)
    if target_column is None:
        return 0, 0
    numeric = pd.to_numeric(frame[target_column], errors="coerce")
    positive = int((numeric == 1).sum())
    negative = int((numeric == 0).sum())
    return positive, negative


def count_feature_columns(frame: pd.DataFrame) -> int:
    return len([column for column in frame.columns if str(column).startswith("feature_")])


def decide_status(
    *,
    write_errors: Sequence[str],
    has_sources: bool,
    load_errors: Sequence[str],
    accumulated_row_count: int,
    positive_count: int,
    negative_count: int,
    feature_count: int,
    distinct_run_count: int,
    duplicate_rate: float | None,
) -> tuple[str, str, str, list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_WAIT_FOR_EVIDENCE, sorted_unique(write_errors)

    if not has_sources:
        blockers = sorted_unique(["missing_quarantine_microbatch_sources", *load_errors])
        return "blocked", "missing_quarantine_microbatch_sources", DECISION_WAIT_FOR_EVIDENCE, blockers

    blockers: list[str] = list(load_errors)
    if accumulated_row_count < MIN_ACCUMULATED_ROWS:
        blockers.append("min_accumulated_rows_not_met")
    if positive_count < MIN_CLASS_POSITIVE_COUNT:
        blockers.append("min_class_positive_count_not_met")
    if negative_count < MIN_CLASS_NEGATIVE_COUNT:
        blockers.append("min_class_negative_count_not_met")
    if feature_count < MIN_FEATURE_COUNT:
        blockers.append("min_feature_count_not_met")
    if distinct_run_count < MIN_DISTINCT_RUN_IDS:
        blockers.append("min_distinct_run_ids_not_met")
    if duplicate_rate is not None and duplicate_rate > MAX_DUPLICATE_RATE:
        blockers.append("max_duplicate_rate_exceeded")

    unique_blockers = sorted_unique(blockers)
    if unique_blockers:
        return "blocked", "insufficient_accumulated_evidence", DECISION_WAIT_FOR_EVIDENCE, unique_blockers
    return "ok", "accumulated_evidence_ready_for_candidate_recheck", DECISION_RECHECK_ALLOWED, unique_blockers


def write_accumulated_dataset_files(
    paths: AccumulationPaths,
    accumulated_frame: pd.DataFrame,
    report: Mapping[str, Any],
) -> str:
    output_columns = [
        column for column in accumulated_frame.columns if not str(column).startswith(INTERNAL_COLUMN_PREFIX)
    ]
    clean_frame = accumulated_frame[output_columns].reset_index(drop=True)
    paths.output_dataset.parent.mkdir(parents=True, exist_ok=True)
    clean_frame.to_parquet(paths.output_dataset, index=False)
    dataset_hash = file_sha256(paths.output_dataset)
    manifest = {
        "schema_version": "paper_autotrain_evidence_accumulation_window_dataset_manifest_v1",
        "generated_at_utc": report.get("generated_at_utc"),
        "row_count": int(len(clean_frame)),
        "column_count": len(output_columns),
        "columns": sorted(str(column) for column in output_columns),
        "source_file_count": report.get("source_file_count"),
        "source_files": report.get("source_files"),
        "dedup_key_strategy_used": report.get("dedup_key_strategy_used"),
        "duplicate_rows_removed": report.get("duplicate_rows_removed"),
        "duplicate_rate": report.get("duplicate_rate"),
        "dataset_path": str(paths.output_dataset),
        "dataset_sha256": dataset_hash,
        "research_only": True,
        "paper_only": True,
        "trains_model": False,
        "promotes_model": False,
    }
    write_json(paths.output_dataset_manifest, manifest)
    return dataset_hash


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Autotrain Evidence Accumulation Window V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Source file count: `{report.get('source_file_count')}`",
        f"- Source row count: `{report.get('source_row_count')}`",
        f"- Duplicate rows removed: `{report.get('duplicate_rows_removed')}`",
        f"- Duplicate rate: `{report.get('duplicate_rate')}` (max allowed `{report.get('max_duplicate_rate')}`)",
        f"- Dedup key strategy used: `{report.get('dedup_key_strategy_used')}`",
        f"- Accumulated row count: `{report.get('accumulated_row_count')}` (minimum `{report.get('min_accumulated_rows')}`)",
        f"- Observed positive class: `{report.get('observed_class_positive_count')}` (minimum `{report.get('min_class_positive_count')}`)",
        f"- Observed negative class: `{report.get('observed_class_negative_count')}` (minimum `{report.get('min_class_negative_count')}`)",
        f"- Observed feature count: `{report.get('observed_feature_count')}` (minimum `{report.get('min_feature_count')}`)",
        f"- Observed distinct run count: `{report.get('observed_distinct_run_count')}` (minimum `{report.get('min_distinct_run_ids')}`)",
        f"- Accumulation ready for candidate recheck: `{report.get('accumulation_ready_for_candidate_recheck')}`",
        f"- Candidate recheck allowed: `{report.get('candidate_recheck_allowed')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    lines.extend(f"- `{blocker}`" for blocker in blockers)
    if not blockers:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "This accumulator is research-only and read-only by default. It never trains a model, never promotes a",
            "candidate, never updates Qlib/IA Shadow/Freqtrade/RiskManager runtime, never writes an active registry or",
            "active model artifact, and never writes operational signals. Even when status is `ok`, only",
            "`candidate_recheck_allowed` becomes true; `training_allowed`, `promotion_allowed`, and `runtime_allowed`",
            "remain `false`.",
            "",
        ]
    )
    return "\n".join(lines)


def safety_flags(
    *,
    write_report_requested: bool,
    write_report_performed: bool,
    write_dataset_requested: bool,
    write_dataset_performed: bool,
    candidate_recheck_allowed: bool,
) -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "read_only": not (write_report_requested or write_dataset_requested),
        "write_report_requested": bool(write_report_requested),
        "write_report_performed": bool(write_report_performed),
        "write_dataset_requested": bool(write_dataset_requested),
        "write_dataset_performed": bool(write_dataset_performed),
        "write_performed": bool(write_report_performed or write_dataset_performed),
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "trains_model": False,
        "runs_training": False,
        "training_allowed": False,
        "promotes_model": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "active_registry_changed": False,
        "writes_active_registry": False,
        "writes_active_model_artifact": False,
        "writes_quarantine_registry": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_freqtrade_config": False,
        "updates_freqtrade_strategy": False,
        "updates_risk_manager": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "active_signal_file_written": False,
        "paper_selector_runtime_enabled": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
        "runtime_allowed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_operational_parquet": False,
        "writes_parquet": bool(write_dataset_performed),
        "candidate_recheck_allowed": bool(candidate_recheck_allowed),
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_present(columns: set[str], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        if alias in columns:
            return alias
    return None


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
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
