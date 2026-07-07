"""Freshness and watermark diagnostics for paper autotrain quarantine microbatches.

The diagnostic is research-only and read-only by default. It inspects previously
materialized quarantine microbatches, measures whether later runs introduce new
records, and emits a fail-closed report. It never trains, promotes, updates
runtime state, writes parquet, or writes any operational registry/signal file.
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

SCHEMA_VERSION = "paper_autotrain_microbatch_freshness_and_watermark_v1"

DEFAULT_QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
MICROBATCH_FILENAME = "incremental_training_microbatch.parquet"
DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

ACCUMULATION_REPORT = Path("data/reports/paper_autotrain_evidence_accumulation_window_v1.json")
ACTIVATION_REPORT = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
CANDIDATE_EVALUATION_REPORT = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json")
FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")
QUARANTINE_REGISTRY = Path("data/registries/quarantine/paper_autotrain_candidate_registry_v1.json")

DECISION_WAIT_MICROBATCHES = "AGUARDAR_MICROBATCHES_DE_QUARENTENA"
DECISION_FIX_WATERMARK = "CORRIGIR_WATERMARK_INCREMENTAL_ANTES_DE_NOVO_TREINO"
DECISION_CONTINUE_EVIDENCE = "CONTINUAR_ACUMULANDO_EVIDENCIA"
DECISION_CONTINUE_RESEARCH_PIPELINE = "CONTINUAR_PIPELINE_DE_ACUMULO_RESEARCH_ONLY"

RECORD_HASH_ALIASES = ("record_hash",)
ORDER_ID_ALIASES = ("order_id",)
TRADE_ID_ALIASES = ("trade_id",)
CLOSE_TIME_ALIASES = ("close_time_utc", "close_time", "horario_fechamento", "closed_at")
OPEN_TIME_ALIASES = ("open_time_utc", "open_time", "horario_abertura", "opened_at")
PNL_ALIASES = ("pnl_fechado", "net_pnl", "pnl_usdt", "realized_pnl")
SYMBOL_ALIASES = ("symbol", "moeda", "pair")
SIDE_ALIASES = ("side", "fechar_side")
TARGET_ALIASES = ("target_profitable", "target")

INTERNAL_RECORD_KEY = "__freshness_record_key__"
INTERNAL_CLOSE_TIME = "__freshness_close_time_utc__"
INTERNAL_RUN_ID = "__freshness_run_id__"
INTERNAL_SOURCE_FILE = "__freshness_source_file__"
INTERNAL_RECORD_HASH = "__freshness_record_hash__"


@dataclass(frozen=True)
class FreshnessPaths:
    quarantine_dir: Path
    output_json: Path
    output_markdown: Path


@dataclass(frozen=True)
class LoadedRun:
    run_id: str
    source_file: Path
    frame: pd.DataFrame
    row_count: int
    content_sha256: str
    load_warnings: tuple[str, ...]


def build_paper_autotrain_microbatch_freshness_and_watermark_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_stale: bool = False,
    fail_on_no_new_records: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic paper autotrain microbatch freshness report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path)
    output_paths = {"json": str(paths.output_json), "markdown": str(paths.output_markdown)}
    warnings: list[str] = []
    blockers: list[str] = []

    write_errors = []
    if write_report:
        write_errors.extend(validate_report_path(root, paths.output_json))
        write_errors.extend(validate_report_path(root, paths.output_markdown))

    optional_sources = load_optional_sources(root)
    warnings.extend(optional_sources["warnings"])

    source_paths = discover_microbatches(paths.quarantine_dir)
    if not source_paths:
        blockers.append("missing_quarantine_microbatch_sources")
        report = base_report(
            root=root,
            generated_at=generated_at,
            status="blocked",
            reason="missing_quarantine_microbatch_sources",
            decision=DECISION_WAIT_MICROBATCHES,
            output_paths=output_paths,
            write_report=write_report,
            write_performed=False,
            blockers=sorted_unique([*blockers, *write_errors]),
            warnings=sorted_unique(warnings),
            optional_sources=optional_sources,
        )
        return maybe_write_report(report, paths, write_report, write_errors)

    loaded_runs = load_runs(source_paths)
    for loaded in loaded_runs:
        warnings.extend(loaded.load_warnings)
    if write_errors:
        blockers.extend(write_errors)

    analysis = analyze_runs(loaded_runs)
    source_row_count = int(analysis["source_row_count"])
    unique_record_count = int(analysis["unique_record_count"])
    duplicate_record_count = max(source_row_count - unique_record_count, 0)
    duplicate_rate = round(duplicate_record_count / source_row_count, 10) if source_row_count else 0.0

    status, reason, decision, status_blockers, status_warnings = decide_status(
        run_count=int(analysis["run_count"]),
        runs_without_new_records_count=int(analysis["runs_without_new_records_count"]),
        all_runs_reobserve_same_records=bool(analysis["all_runs_reobserve_same_records"]),
        has_stale_runs=bool(analysis["staleness_summary"]["stale_run_count"]),
        fail_on_stale=fail_on_stale,
        fail_on_no_new_records=fail_on_no_new_records,
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
        "source_file_count": len(loaded_runs),
        "source_files": [str(loaded.source_file) for loaded in loaded_runs],
        "source_row_count": source_row_count,
        "unique_record_count": unique_record_count,
        "duplicate_record_count": duplicate_record_count,
        "duplicate_rate": duplicate_rate,
        "run_count": int(analysis["run_count"]),
        "runs_with_new_records_count": int(analysis["runs_with_new_records_count"]),
        "runs_without_new_records_count": int(analysis["runs_without_new_records_count"]),
        "all_runs_reobserve_same_records": bool(analysis["all_runs_reobserve_same_records"]),
        "first_run_id": analysis["first_run_id"],
        "last_run_id": analysis["last_run_id"],
        "first_close_time_utc": analysis["first_close_time_utc"],
        "last_close_time_utc": analysis["last_close_time_utc"],
        "watermark_close_time_utc": analysis["watermark_close_time_utc"],
        "watermark_order_id": analysis["watermark_order_id"],
        "watermark_record_hash": analysis["watermark_record_hash"],
        "observed_class_negative_count": int(analysis["observed_class_negative_count"]),
        "observed_class_positive_count": int(analysis["observed_class_positive_count"]),
        "observed_feature_count": int(analysis["observed_feature_count"]),
        "per_run_freshness": analysis["per_run_freshness"],
        "record_first_seen_summary": analysis["record_first_seen_summary"],
        "watermark_summary": analysis["watermark_summary"],
        "staleness_summary": analysis["staleness_summary"],
        "optional_source_status": optional_sources["source_status"],
        "feedback_source_summary": optional_sources["feedback_summary"],
        "fail_on_stale": bool(fail_on_stale),
        "fail_on_no_new_records": bool(fail_on_no_new_records),
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def build_paths(
    root: Path,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
) -> FreshnessPaths:
    return FreshnessPaths(
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def base_report(
    *,
    root: Path,
    generated_at: str,
    status: str,
    reason: str,
    decision: str,
    output_paths: Mapping[str, str],
    write_report: bool,
    write_performed: bool,
    blockers: Sequence[str],
    warnings: Sequence[str],
    optional_sources: Mapping[str, Any],
) -> dict[str, Any]:
    safety = safety_flags(write_report_requested=write_report, write_report_performed=write_performed)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": 0,
        "source_files": [],
        "source_row_count": 0,
        "unique_record_count": 0,
        "duplicate_record_count": 0,
        "duplicate_rate": 0.0,
        "run_count": 0,
        "runs_with_new_records_count": 0,
        "runs_without_new_records_count": 0,
        "all_runs_reobserve_same_records": False,
        "first_run_id": None,
        "last_run_id": None,
        "first_close_time_utc": None,
        "last_close_time_utc": None,
        "watermark_close_time_utc": None,
        "watermark_order_id": None,
        "watermark_record_hash": None,
        "observed_class_negative_count": 0,
        "observed_class_positive_count": 0,
        "observed_feature_count": 0,
        "per_run_freshness": [],
        "record_first_seen_summary": {},
        "watermark_summary": {},
        "staleness_summary": {},
        "optional_source_status": optional_sources["source_status"],
        "feedback_source_summary": optional_sources["feedback_summary"],
        "fail_on_stale": False,
        "fail_on_no_new_records": False,
        "blockers": list(blockers),
        "warnings": list(warnings),
        "output_paths": dict(output_paths),
        **safety,
        "safety_flags": safety,
    }


def discover_microbatches(quarantine_dir: Path) -> list[Path]:
    if not quarantine_dir.is_dir():
        return []
    return sorted(quarantine_dir.glob(f"*/{MICROBATCH_FILENAME}"), key=lambda path: path.as_posix())


def load_runs(paths: Sequence[Path]) -> list[LoadedRun]:
    loaded: list[LoadedRun] = []
    for path in paths:
        run_id = path.parent.name
        load_warnings: list[str] = []
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError, ImportError) as exc:
            load_warnings.append(f"microbatch_read_failed:{run_id}:{exc.__class__.__name__}")
            frame = pd.DataFrame()
        normalized = normalize_frame(frame, run_id, path)
        loaded.append(
            LoadedRun(
                run_id=run_id,
                source_file=path,
                frame=normalized,
                row_count=int(len(frame)),
                content_sha256=content_sha256(normalized),
                load_warnings=tuple(load_warnings),
            )
        )
    return loaded


def normalize_frame(frame: pd.DataFrame, run_id: str, source_file: Path) -> pd.DataFrame:
    working = frame.copy().reset_index(drop=True)
    close_column = first_present(working.columns, CLOSE_TIME_ALIASES)
    close_series = (
        pd.to_datetime(working[close_column], utc=True, errors="coerce")
        if close_column is not None
        else pd.Series([pd.NaT] * len(working), index=working.index)
    )
    working[INTERNAL_CLOSE_TIME] = close_series
    working[INTERNAL_RECORD_KEY] = compute_record_keys(working)
    working[INTERNAL_RECORD_HASH] = compute_row_hashes(working)
    working[INTERNAL_RUN_ID] = run_id
    working[INTERNAL_SOURCE_FILE] = str(source_file)
    return working


def compute_record_keys(frame: pd.DataFrame) -> pd.Series:
    record_hash_column = first_present(frame.columns, RECORD_HASH_ALIASES)
    if record_hash_column is not None:
        normalized_hash = frame[record_hash_column].map(normalize_id_value)
        valid = normalized_hash.map(bool)
        if bool(valid.any()):
            keys = pd.Series([""] * len(frame), index=frame.index)
            keys.loc[valid] = "record_hash:" + normalized_hash.loc[valid]
            keys.loc[~valid] = compute_fallback_record_keys(frame.loc[~valid])
            return keys
    return compute_fallback_record_keys(frame)


def compute_fallback_record_keys(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="object", index=frame.index)

    close_column = first_present(frame.columns, CLOSE_TIME_ALIASES)
    order_column = first_present(frame.columns, ORDER_ID_ALIASES)
    if order_column is not None and close_column is not None:
        order = frame[order_column].map(normalize_id_value)
        close = frame[close_column].map(normalize_time_value)
        valid = order.map(bool) & close.map(bool)
        if bool(valid.all()):
            return "order_close:" + order + "|" + close

    trade_column = first_present(frame.columns, TRADE_ID_ALIASES)
    if trade_column is not None and close_column is not None:
        trade = frame[trade_column].map(normalize_id_value)
        close = frame[close_column].map(normalize_time_value)
        valid = trade.map(bool) & close.map(bool)
        if bool(valid.all()):
            return "trade_close:" + trade + "|" + close

    symbol_column = first_present(frame.columns, SYMBOL_ALIASES)
    side_column = first_present(frame.columns, SIDE_ALIASES)
    open_column = first_present(frame.columns, OPEN_TIME_ALIASES)
    pnl_column = first_present(frame.columns, PNL_ALIASES)
    if symbol_column and side_column and open_column and close_column and pnl_column:
        parts = [
            frame[symbol_column].map(normalize_id_value),
            frame[side_column].map(normalize_id_value),
            frame[open_column].map(normalize_time_value),
            frame[close_column].map(normalize_time_value),
            frame[pnl_column].map(normalize_scalar),
        ]
        valid = parts[0].map(bool) & parts[1].map(bool) & parts[2].map(bool) & parts[3].map(bool) & parts[4].map(bool)
        if bool(valid.all()):
            return "composite:" + parts[0] + "|" + parts[1] + "|" + parts[2] + "|" + parts[3] + "|" + parts[4]

    return compute_row_hashes(frame).map(lambda value: f"row_hash:{value}")


def compute_row_hashes(frame: pd.DataFrame) -> pd.Series:
    content_columns = sorted(column for column in frame.columns if not str(column).startswith("__freshness_"))

    def hash_row(row: pd.Series) -> str:
        parts = [f"{column}={normalize_scalar(row[column])}" for column in content_columns]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    if frame.empty:
        return pd.Series(dtype="object", index=frame.index)
    return frame[content_columns].apply(hash_row, axis=1)


def content_sha256(frame: pd.DataFrame) -> str:
    keys = sorted(str(value) for value in frame.get(INTERNAL_RECORD_KEY, pd.Series(dtype="object")).tolist())
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def analyze_runs(loaded_runs: Sequence[LoadedRun]) -> dict[str, Any]:
    seen_records: set[str] = set()
    first_seen: dict[str, dict[str, Any]] = {}
    per_run: list[dict[str, Any]] = []
    all_records: list[str] = []
    all_close_times: list[pd.Timestamp] = []
    all_order_ids: list[str] = []
    all_record_hashes: list[str] = []
    previous_content_sha: str | None = None
    previous_watermark: pd.Timestamp | None = None
    temporal_advancing_count = 0

    class_positive = 0
    class_negative = 0
    feature_columns: set[str] = set()

    for index, loaded in enumerate(loaded_runs):
        frame = loaded.frame
        record_keys = frame[INTERNAL_RECORD_KEY].astype(str).tolist() if INTERNAL_RECORD_KEY in frame else []
        unique_keys = sorted(set(record_keys))
        duplicate_within_run_count = max(len(record_keys) - len(unique_keys), 0)
        new_keys = [key for key in unique_keys if key not in seen_records]
        reobserved_count = max(len(unique_keys) - len(new_keys), 0)
        close_times = extract_close_times(frame)
        run_first_close = min(close_times) if close_times else None
        run_last_close = max(close_times) if close_times else None
        if run_last_close is not None:
            if previous_watermark is None or run_last_close > previous_watermark:
                temporal_advancing_count += 1
            previous_watermark = max(previous_watermark, run_last_close) if previous_watermark is not None else run_last_close

        is_content_duplicate = previous_content_sha == loaded.content_sha256 if previous_content_sha is not None else False
        is_stale = index > 0 and len(new_keys) == 0
        run_blockers = ["stale_run_no_new_records"] if is_stale else []
        run_warnings = ["content_duplicate_of_previous_run"] if is_content_duplicate else []
        per_run.append(
            {
                "run_id": loaded.run_id,
                "source_file": str(loaded.source_file),
                "row_count": loaded.row_count,
                "unique_record_count_in_run": len(unique_keys),
                "new_unique_records_count": len(new_keys),
                "reobserved_records_count": reobserved_count,
                "new_record_ratio": round(len(new_keys) / len(unique_keys), 10) if unique_keys else 0.0,
                "duplicate_within_run_count": duplicate_within_run_count,
                "first_close_time_utc": timestamp_to_iso(run_first_close),
                "last_close_time_utc": timestamp_to_iso(run_last_close),
                "max_order_id": max_or_none(extract_order_ids(frame)),
                "min_order_id": min_or_none(extract_order_ids(frame)),
                "content_sha256": loaded.content_sha256,
                "is_content_duplicate_of_previous_run": is_content_duplicate,
                "is_stale_run": is_stale,
                "blockers": run_blockers,
                "warnings": run_warnings,
            }
        )
        for key in new_keys:
            first_seen[key] = {
                "first_seen_run_id": loaded.run_id,
                "first_seen_source_file": str(loaded.source_file),
                "first_seen_run_index": index,
            }
        first_seen_frame = frame[frame[INTERNAL_RECORD_KEY].astype(str).isin(new_keys)].drop_duplicates(
            subset=[INTERNAL_RECORD_KEY], keep="first"
        )
        seen_records.update(unique_keys)
        all_records.extend(record_keys)
        all_close_times.extend(close_times)
        all_order_ids.extend(extract_order_ids(frame))
        all_record_hashes.extend(extract_record_hashes(frame))
        positive, negative = class_counts(first_seen_frame)
        class_positive += positive
        class_negative += negative
        feature_columns.update(str(column) for column in frame.columns if str(column).startswith("feature_"))
        previous_content_sha = loaded.content_sha256

    run_count = len(loaded_runs)
    runs_with_new = sum(1 for run in per_run if int(run["new_unique_records_count"]) > 0)
    runs_without_new = run_count - runs_with_new
    stale_after_first = [run for run in per_run[1:] if int(run["new_unique_records_count"]) == 0]
    all_reobserve = run_count > 1 and len(stale_after_first) == run_count - 1
    unique_records = sorted(set(all_records))
    first_close = min(all_close_times) if all_close_times else None
    last_close = max(all_close_times) if all_close_times else None

    return {
        "source_row_count": len(all_records),
        "unique_record_count": len(unique_records),
        "run_count": run_count,
        "runs_with_new_records_count": runs_with_new,
        "runs_without_new_records_count": runs_without_new,
        "all_runs_reobserve_same_records": all_reobserve,
        "first_run_id": loaded_runs[0].run_id if loaded_runs else None,
        "last_run_id": loaded_runs[-1].run_id if loaded_runs else None,
        "first_close_time_utc": timestamp_to_iso(first_close),
        "last_close_time_utc": timestamp_to_iso(last_close),
        "watermark_close_time_utc": timestamp_to_iso(last_close),
        "watermark_order_id": max_or_none(all_order_ids),
        "watermark_record_hash": max_or_none(all_record_hashes),
        "observed_class_negative_count": class_negative,
        "observed_class_positive_count": class_positive,
        "observed_feature_count": len(feature_columns),
        "per_run_freshness": per_run,
        "record_first_seen_summary": build_first_seen_summary(first_seen, per_run),
        "watermark_summary": {
            "min_close_time_utc": timestamp_to_iso(first_close),
            "max_close_time_utc": timestamp_to_iso(last_close),
            "min_order_id": min_or_none(all_order_ids),
            "max_order_id": max_or_none(all_order_ids),
            "min_record_hash": min_or_none(all_record_hashes),
            "max_record_hash": max_or_none(all_record_hashes),
            "temporal_progress_detected": temporal_advancing_count > 1,
            "temporal_advancing_run_count": temporal_advancing_count,
        },
        "staleness_summary": {
            "stale_run_count": sum(1 for run in per_run if run["is_stale_run"]),
            "stale_run_ids": [str(run["run_id"]) for run in per_run if run["is_stale_run"]],
            "content_duplicate_run_count": sum(1 for run in per_run if run["is_content_duplicate_of_previous_run"]),
            "content_duplicate_run_ids": [
                str(run["run_id"]) for run in per_run if run["is_content_duplicate_of_previous_run"]
            ],
            "new_records_after_first_run": sum(int(run["new_unique_records_count"]) for run in per_run[1:]),
            "runs_without_new_records_after_first_run": sum(
                1 for run in per_run[1:] if int(run["new_unique_records_count"]) == 0
            ),
        },
    }


def build_first_seen_summary(first_seen: Mapping[str, Mapping[str, Any]], per_run: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    introduced_by_run: dict[str, int] = {}
    for record in first_seen.values():
        run_id = str(record["first_seen_run_id"])
        introduced_by_run[run_id] = introduced_by_run.get(run_id, 0) + 1
    sample = [
        {"record_key": key, **dict(value)}
        for key, value in sorted(first_seen.items(), key=lambda item: item[0])[:25]
    ]
    return {
        "unique_records_tracked": len(first_seen),
        "records_introduced_by_run": introduced_by_run,
        "record_first_seen_sample": sample,
        "sample_size": len(sample),
        "per_run_new_records": {str(run["run_id"]): int(run["new_unique_records_count"]) for run in per_run},
    }


def decide_status(
    *,
    run_count: int,
    runs_without_new_records_count: int,
    all_runs_reobserve_same_records: bool,
    has_stale_runs: bool,
    fail_on_stale: bool,
    fail_on_no_new_records: bool,
    write_errors: Sequence[str],
) -> tuple[str, str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", DECISION_FIX_WATERMARK, list(write_errors), []
    if run_count == 0:
        return "blocked", "missing_quarantine_microbatch_sources", DECISION_WAIT_MICROBATCHES, [
            "missing_quarantine_microbatch_sources"
        ], []
    if all_runs_reobserve_same_records:
        return "blocked", "microbatch_freshness_stalled", DECISION_FIX_WATERMARK, [
            "all_runs_after_first_reobserve_same_records"
        ], []
    if fail_on_no_new_records and runs_without_new_records_count > 0:
        return "blocked", "microbatch_runs_without_new_records", DECISION_FIX_WATERMARK, [
            "runs_without_new_records_detected"
        ], []
    if fail_on_stale and has_stale_runs:
        return "blocked", "microbatch_stale_runs_detected", DECISION_FIX_WATERMARK, ["stale_runs_detected"], []
    if run_count == 1:
        return "warning", "single_microbatch_freshness_baseline_only", DECISION_CONTINUE_EVIDENCE, [], [
            "single_microbatch_cannot_establish_incremental_freshness"
        ]
    if has_stale_runs:
        return "warning", "freshness_progress_detected_but_evidence_still_insufficient", DECISION_CONTINUE_EVIDENCE, [], [
            "some_runs_without_new_records"
        ]
    return "ok", "microbatch_freshness_progressing", DECISION_CONTINUE_RESEARCH_PIPELINE, [], []


def load_optional_sources(root: Path) -> dict[str, Any]:
    source_status: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name, relative_path in {
        "accumulation_report": ACCUMULATION_REPORT,
        "activation_report": ACTIVATION_REPORT,
        "candidate_evaluation_report": CANDIDATE_EVALUATION_REPORT,
        "quarantine_registry": QUARANTINE_REGISTRY,
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
        }

    feedback_path = root / FEEDBACK_EVENTS
    feedback_summary: dict[str, Any] = {"path": str(feedback_path), "status": "missing_optional", "event_count": 0}
    if feedback_path.exists():
        try:
            event_count = sum(1 for line in feedback_path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
            feedback_summary = {"path": str(feedback_path), "status": "ok", "event_count": event_count}
        except OSError as exc:
            feedback_summary = {"path": str(feedback_path), "status": "invalid_optional", "error": exc.__class__.__name__}
            warnings.append(f"optional_source_invalid:feedback_events:{exc.__class__.__name__}")
    else:
        warnings.append("optional_source_missing:feedback_events")
    source_status["feedback_events"] = feedback_summary
    return {"source_status": source_status, "feedback_summary": feedback_summary, "warnings": sorted_unique(warnings)}


def maybe_write_report(
    report: dict[str, Any],
    paths: FreshnessPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report
    write_json(paths.output_json, report)
    paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True
    write_json(paths.output_json, report)
    paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Autotrain Microbatch Freshness and Watermark V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Run count: `{report.get('run_count')}`",
        f"- Source row count: `{report.get('source_row_count')}`",
        f"- Unique record count: `{report.get('unique_record_count')}`",
        f"- Duplicate record count: `{report.get('duplicate_record_count')}`",
        f"- Duplicate rate: `{report.get('duplicate_rate')}`",
        f"- Watermark close time UTC: `{report.get('watermark_close_time_utc')}`",
        "",
        "## Per-run freshness",
        "",
        "| run_id | rows | unique | new | reobserved | stale | duplicate content |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report.get("per_run_freshness", []):
        lines.append(
            "| {run_id} | {row_count} | {unique_record_count_in_run} | {new_unique_records_count} | "
            "{reobserved_records_count} | {is_stale_run} | {is_content_duplicate_of_previous_run} |".format(**item)
        )
    if not report.get("per_run_freshness"):
        lines.append("| none | 0 | 0 | 0 | 0 | false | false |")
    lines.extend(
        [
            "",
            "## Conclusao operacional",
            "",
            "Este diagnostico nao possui autoridade operacional. Mesmo quando `status=ok`, ele nao autoriza treino,",
            "promocao, runtime, registry ativo, alteracao de risco, Freqtrade, ordens ou exchange privada.",
            "",
        ]
    )
    return "\n".join(lines)


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
        "promotes_model": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "runtime_allowed": False,
        "active_model_changed": False,
        "active_registry_changed": False,
        "writes_active_registry": False,
        "writes_active_model_artifact": False,
        "writes_quarantine_registry": False,
        "updates_qlib_runtime": False,
        "qlib_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
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
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_operational_parquet": False,
    }


def validate_report_path(root: Path, path: Path) -> list[str]:
    try:
        path.resolve().relative_to((root / ALLOWED_REPORT_ROOT).resolve())
    except ValueError:
        return ["write_path_outside_data_reports"]
    return []


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def first_present(columns: Sequence[Any], aliases: Sequence[str]) -> str | None:
    column_set = {str(column) for column in columns}
    for alias in aliases:
        if alias in column_set:
            return alias
    return None


def extract_close_times(frame: pd.DataFrame) -> list[pd.Timestamp]:
    if INTERNAL_CLOSE_TIME not in frame:
        return []
    return [value for value in frame[INTERNAL_CLOSE_TIME].dropna().tolist() if isinstance(value, pd.Timestamp)]


def extract_order_ids(frame: pd.DataFrame) -> list[str]:
    column = first_present(frame.columns, ORDER_ID_ALIASES)
    if column is None:
        return []
    return sorted(value for value in frame[column].map(normalize_id_value).tolist() if value)


def extract_record_hashes(frame: pd.DataFrame) -> list[str]:
    column = first_present(frame.columns, RECORD_HASH_ALIASES)
    if column is None:
        return sorted(value for value in frame.get(INTERNAL_RECORD_HASH, pd.Series(dtype="object")).astype(str).tolist() if value)
    return sorted(value for value in frame[column].map(normalize_id_value).tolist() if value)


def class_counts(frame: pd.DataFrame) -> tuple[int, int]:
    column = first_present(frame.columns, TARGET_ALIASES)
    if column is None:
        return 0, 0
    numeric = pd.to_numeric(frame[column], errors="coerce")
    return int((numeric == 1).sum()), int((numeric == 0).sum())


def normalize_id_value(value: Any) -> str:
    text = normalize_scalar(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def normalize_time_value(value: Any) -> str:
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return ""
    return timestamp.isoformat()


def normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        return repr(round(value, 10))
    return str(value)


def timestamp_to_iso(value: pd.Timestamp | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").isoformat()


def max_or_none(values: Sequence[str]) -> str | None:
    cleaned = [value for value in values if value]
    return max(cleaned) if cleaned else None


def min_or_none(values: Sequence[str]) -> str | None:
    cleaned = [value for value in values if value]
    return min(cleaned) if cleaned else None


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
