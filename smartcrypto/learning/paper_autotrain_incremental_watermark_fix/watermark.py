"""Research-only incremental watermark for paper autotrain quarantine.

This module prevents quarantine microbatch producers from recycling records
that have already been materialized as research evidence. It is no-write by
default and only writes the explicit report/watermark artifacts requested by
the caller.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autotrain_incremental_watermark_fix_v1"
WATERMARK_SCHEMA_VERSION = "paper_autotrain_incremental_watermark_state_v1"

DEFAULT_QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
DEFAULT_WATERMARK_PATH = Path("data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json")
DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.md")
MICROBATCH_FILENAME = "incremental_training_microbatch.parquet"

ALLOWED_REPORT_ROOT = Path("data/reports")
ALLOWED_WATERMARK_ROOT = Path("data/research/paper_autotrain_daily_quarantine_watermark")

DECISION_WAIT_MICROBATCHES = "AGUARDAR_MICROBATCHES_DE_QUARENTENA"
DECISION_BOOTSTRAP = "BOOTSTRAP_WATERMARK_RESEARCH_ONLY_ANTES_DE_NOVO_TREINO"
DECISION_WAIT_NEW_TRADES = "AGUARDAR_NOVOS_TRADES_PAPER"
DECISION_INCREMENTAL_ALLOWED = "MICROBATCH_INCREMENTAL_PERMITIDO_EM_QUARENTENA"
DECISION_FIX_CORRUPTION = "CORRIGIR_WATERMARK_CORROMPIDO"

RECORD_HASH_ALIASES = ("record_hash",)
ORDER_ID_ALIASES = ("order_id",)
TRADE_ID_ALIASES = ("trade_id",)
CLOSE_TIME_ALIASES = ("close_time_utc", "close_time", "horario_fechamento", "closed_at")
OPEN_TIME_ALIASES = ("open_time_utc", "open_time", "horario_abertura", "opened_at")
PNL_ALIASES = ("pnl_fechado", "net_pnl", "pnl_usdt", "realized_pnl")
SYMBOL_ALIASES = ("symbol", "moeda", "pair")
SIDE_ALIASES = ("side", "fechar_side")

INTERNAL_RECORD_KEY = "__watermark_record_key__"
INTERNAL_RECORD_STRATEGY = "__watermark_record_strategy__"
INTERNAL_CLOSE_TIME = "__watermark_close_time_utc__"
INTERNAL_RECORD_HASH = "__watermark_record_hash__"


@dataclass(frozen=True)
class WatermarkPaths:
    quarantine_dir: Path
    watermark_path: Path
    output_json: Path
    output_markdown: Path


@dataclass(frozen=True)
class WatermarkRead:
    exists: bool
    status: str
    state: dict[str, Any] | None
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class WatermarkGateResult:
    status: str
    reason: str
    decision: str
    filtered_frame: pd.DataFrame
    state: dict[str, Any]
    report_fields: dict[str, Any]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    blocks_quarantine_outputs: bool


def build_paper_autotrain_incremental_watermark_fix_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    write_watermark_state_requested: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    watermark_path: str | Path | None = None,
    fail_on_stale: bool = False,
    fail_on_watermark_corruption: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the incremental watermark diagnostic report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path, watermark_path)
    output_paths = {
        "json": str(paths.output_json),
        "markdown": str(paths.output_markdown),
        "watermark": str(paths.watermark_path),
    }
    write_errors = validate_write_requests(root, paths, write_report, write_watermark_state_requested)
    sources = load_existing_microbatches(paths.quarantine_dir)
    source_frame = sources["frame"]
    source_warnings = list(sources["warnings"])
    if source_frame.empty:
        report = build_base_report(
            root=root,
            generated_at=generated_at,
            status="blocked",
            reason="missing_quarantine_microbatch_sources",
            decision=DECISION_WAIT_MICROBATCHES,
            paths=paths,
            output_paths=output_paths,
            write_report=write_report,
            write_watermark_state_requested=write_watermark_state_requested,
            blockers=sorted_unique(["missing_quarantine_microbatch_sources", *write_errors]),
            warnings=sorted_unique(source_warnings),
        )
        return write_requested_outputs(report, paths, write_report, False, write_errors)

    normalized = normalize_records(source_frame)
    source_summary = summarize_normalized_records(normalized)
    watermark_read = read_watermark_state(paths.watermark_path)
    gate = evaluate_incremental_watermark_gate(
        frame=source_frame,
        watermark_state=watermark_read.state,
        watermark_exists=watermark_read.exists,
        watermark_status=watermark_read.status,
        bootstrap_sources_summary={
            "source_file_count": int(sources["source_file_count"]),
            "source_row_count": int(len(source_frame)),
            "unique_record_count": source_summary["unique_record_count"],
            "last_successful_run_id": sources["last_run_id"],
            "last_successful_source_file": sources["last_source_file"],
            "last_successful_content_sha256": sources["last_content_sha256"],
        },
        generated_at_utc=generated_at,
    )

    status = gate.status
    reason = gate.reason
    decision = gate.decision
    blockers = [*write_errors, *watermark_read.blockers, *gate.blockers]
    warnings = [*source_warnings, *watermark_read.warnings, *gate.warnings]
    if fail_on_watermark_corruption and watermark_read.status == "invalid":
        status = "blocked"
        reason = "watermark_state_invalid"
        decision = DECISION_FIX_CORRUPTION
        blockers.append("watermark_state_invalid")
    if fail_on_stale and int(gate.report_fields["new_unique_records_count"]) == 0:
        status = "blocked"
        reason = "no_new_incremental_records_after_watermark"
        decision = DECISION_WAIT_NEW_TRADES
        blockers.append("no_new_incremental_records_after_watermark")

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": int(sources["source_file_count"]),
        "source_row_count": int(len(source_frame)),
        "unique_record_count": int(source_summary["unique_record_count"]),
        "duplicate_record_count": int(source_summary["duplicate_record_count"]),
        "duplicate_rate": source_summary["duplicate_rate"],
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": watermark_read.exists,
        "watermark_status": watermark_read.status,
        "watermark_close_time_utc": gate.state.get("watermark_close_time_utc"),
        "watermark_order_id": gate.state.get("watermark_order_id"),
        "watermark_record_hash": gate.state.get("watermark_record_hash"),
        "seen_record_key_count": gate.state.get("seen_record_key_count"),
        "seen_record_keys_sha256": gate.state.get("seen_record_keys_sha256"),
        "bootstrap_required": gate.report_fields["bootstrap_required"],
        "would_initialize_watermark": gate.report_fields["would_initialize_watermark"],
        "bootstrap_source_file_count": int(sources["source_file_count"]),
        "bootstrap_source_row_count": int(len(source_frame)),
        "bootstrap_unique_record_count": int(source_summary["unique_record_count"]),
        "new_unique_records_count": gate.report_fields["new_unique_records_count"],
        "already_seen_record_count": gate.report_fields["already_seen_record_count"],
        "stale_duplicate_microbatch_prevented": gate.report_fields["stale_duplicate_microbatch_prevented"],
        "training_prevented_by_watermark": gate.report_fields["training_prevented_by_watermark"],
        "would_write_microbatch": gate.report_fields["would_write_microbatch"],
        "would_run_training": False,
        "record_key_strategy_counts": source_summary["record_key_strategy_counts"],
        "incremental_summary": gate.report_fields["incremental_summary"],
        "watermark_summary": summarize_watermark_state(gate.state),
        "fail_on_stale": bool(fail_on_stale),
        "fail_on_watermark_corruption": bool(fail_on_watermark_corruption),
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": output_paths,
        **safety_flags(
            write_report_requested=write_report,
            write_report_performed=False,
            write_watermark_requested=write_watermark_state_requested,
            write_watermark_performed=False,
            training_prevented_by_watermark=bool(gate.report_fields["training_prevented_by_watermark"]),
        ),
    }
    report["safety_flags"] = {
        key: value for key, value in report.items() if isinstance(value, bool) and key in safety_flag_keys()
    }
    return write_requested_outputs(report, paths, write_report, write_watermark_state_requested, write_errors)


def evaluate_incremental_watermark_gate(
    *,
    frame: pd.DataFrame,
    watermark_state: Mapping[str, Any] | None,
    watermark_exists: bool,
    watermark_status: str,
    bootstrap_sources_summary: Mapping[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> WatermarkGateResult:
    """Filter a candidate microbatch against the research/quarantine watermark."""

    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    normalized = normalize_records(frame)
    summary = summarize_normalized_records(normalized)
    if watermark_status == "invalid":
        state = dict(watermark_state or empty_watermark_state(generated_at))
        fields = report_fields_for_gate(0, 0, False, False, {})
        return WatermarkGateResult(
            status="blocked",
            reason="watermark_state_invalid",
            decision=DECISION_FIX_CORRUPTION,
            filtered_frame=pd.DataFrame(),
            state=state,
            report_fields=fields,
            blockers=("watermark_state_invalid",),
            warnings=(),
            blocks_quarantine_outputs=True,
        )

    seen_keys = set(load_seen_keys(watermark_state))
    bootstrap_required = not watermark_exists
    new_mask = ~normalized[INTERNAL_RECORD_KEY].isin(seen_keys) if seen_keys else pd.Series([True] * len(normalized))
    filtered = frame.loc[new_mask.to_numpy()].copy().reset_index(drop=True)
    unique_new_keys = sorted(set(normalized.loc[new_mask, INTERNAL_RECORD_KEY].astype(str).tolist()))
    already_seen_keys = sorted(set(normalized.loc[~new_mask, INTERNAL_RECORD_KEY].astype(str).tolist()))
    would_initialize = bootstrap_required and bool(unique_new_keys)
    stale_prevented = (not bootstrap_required) and not unique_new_keys and bool(seen_keys)
    training_prevented = stale_prevented
    would_write_microbatch = bool(unique_new_keys) and not bootstrap_required
    status, reason, decision, blockers = decide_gate_status(
        bootstrap_required=bootstrap_required,
        unique_new_count=len(unique_new_keys),
        stale_prevented=stale_prevented,
    )
    merged_keys = sorted(set(seen_keys).union(summary["unique_record_keys"]))
    state = build_watermark_state(
        generated_at_utc=generated_at,
        existing_state=watermark_state,
        normalized=normalized,
        seen_record_keys=merged_keys,
        bootstrap_sources_summary=bootstrap_sources_summary,
    )
    fields = report_fields_for_gate(
        len(unique_new_keys),
        len(already_seen_keys),
        bootstrap_required,
        would_initialize,
        {
            "new_record_key_sample": unique_new_keys[:25],
            "already_seen_record_key_sample": already_seen_keys[:25],
            "source_unique_record_count": summary["unique_record_count"],
            "source_duplicate_record_count": summary["duplicate_record_count"],
        },
        stale_prevented=stale_prevented,
        training_prevented=training_prevented,
        would_write_microbatch=would_write_microbatch,
    )
    return WatermarkGateResult(
        status=status,
        reason=reason,
        decision=decision,
        filtered_frame=filtered,
        state=state,
        report_fields=fields,
        blockers=tuple(blockers),
        warnings=(),
        blocks_quarantine_outputs=reason in {"no_new_incremental_records_after_watermark", "watermark_bootstrap_required"},
    )


def evaluate_activation_incremental_watermark_gate(
    *,
    root: Path,
    candidate_microbatch: pd.DataFrame,
    generated_at_utc: str,
    watermark_path: Path | None = None,
) -> WatermarkGateResult:
    """Evaluate the producer-side watermark gate before quarantine artifacts.

    If no watermark file exists, existing quarantine microbatches are used as an
    in-memory bootstrap state. A clean environment with no previous quarantine
    microbatches starts from an empty watermark and can produce its first
    incremental microbatch.
    """

    target_path = watermark_path or root / DEFAULT_WATERMARK_PATH
    watermark_read = read_watermark_state(target_path)
    if watermark_read.status == "invalid":
        return evaluate_incremental_watermark_gate(
            frame=candidate_microbatch,
            watermark_state=watermark_read.state,
            watermark_exists=True,
            watermark_status="invalid",
            generated_at_utc=generated_at_utc,
        )

    watermark_state = watermark_read.state
    if not watermark_read.exists:
        bootstrap = load_existing_microbatches(root / DEFAULT_QUARANTINE_DIR)
        bootstrap_frame = bootstrap["frame"]
        bootstrap_normalized = normalize_records(bootstrap_frame)
        bootstrap_summary = summarize_normalized_records(bootstrap_normalized)
        watermark_state = build_watermark_state(
            generated_at_utc=generated_at_utc,
            existing_state=None,
            normalized=bootstrap_normalized,
            seen_record_keys=bootstrap_summary["unique_record_keys"],
            bootstrap_sources_summary={
                "source_file_count": int(bootstrap["source_file_count"]),
                "source_row_count": int(len(bootstrap_frame)),
                "unique_record_count": bootstrap_summary["unique_record_count"],
                "last_successful_run_id": bootstrap["last_run_id"],
                "last_successful_source_file": bootstrap["last_source_file"],
                "last_successful_content_sha256": bootstrap["last_content_sha256"],
            },
        )
    return evaluate_incremental_watermark_gate(
        frame=candidate_microbatch,
        watermark_state=watermark_state,
        watermark_exists=True,
        watermark_status="ok",
        generated_at_utc=generated_at_utc,
    )


def decide_gate_status(
    *,
    bootstrap_required: bool,
    unique_new_count: int,
    stale_prevented: bool,
) -> tuple[str, str, str, list[str]]:
    if bootstrap_required:
        return "blocked", "watermark_bootstrap_required", DECISION_BOOTSTRAP, ["watermark_bootstrap_required"]
    if stale_prevented or unique_new_count == 0:
        return "blocked", "no_new_incremental_records_after_watermark", DECISION_WAIT_NEW_TRADES, [
            "no_new_incremental_records_after_watermark"
        ]
    return "ok", "incremental_records_available", DECISION_INCREMENTAL_ALLOWED, []


def report_fields_for_gate(
    new_count: int,
    seen_count: int,
    bootstrap_required: bool,
    would_initialize: bool,
    incremental_summary: Mapping[str, Any],
    *,
    stale_prevented: bool = False,
    training_prevented: bool = False,
    would_write_microbatch: bool = False,
) -> dict[str, Any]:
    return {
        "bootstrap_required": bool(bootstrap_required),
        "would_initialize_watermark": bool(would_initialize),
        "new_unique_records_count": int(new_count),
        "already_seen_record_count": int(seen_count),
        "stale_duplicate_microbatch_prevented": bool(stale_prevented),
        "training_prevented_by_watermark": bool(training_prevented),
        "would_write_microbatch": bool(would_write_microbatch),
        "incremental_summary": dict(incremental_summary),
    }


def build_watermark_state(
    *,
    generated_at_utc: str,
    existing_state: Mapping[str, Any] | None,
    normalized: pd.DataFrame,
    seen_record_keys: Sequence[str],
    bootstrap_sources_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    keys = sorted(set(str(key) for key in seen_record_keys if str(key).strip()))
    existing = dict(existing_state or {})
    close_times = extract_close_times(normalized)
    order_ids = extract_column_values(normalized, ORDER_ID_ALIASES)
    record_hashes = extract_column_values(normalized, RECORD_HASH_ALIASES)
    source_summary = dict(bootstrap_sources_summary or {})
    return {
        "schema_version": WATERMARK_SCHEMA_VERSION,
        "created_at_utc": existing.get("created_at_utc") or generated_at_utc,
        "updated_at_utc": generated_at_utc,
        "source": "paper_autotrain_daily_quarantine",
        "watermark_close_time_utc": timestamp_to_iso(max(close_times)) if close_times else existing.get("watermark_close_time_utc"),
        "watermark_order_id": max_or_none(order_ids) or existing.get("watermark_order_id"),
        "watermark_record_hash": max_or_none(record_hashes) or existing.get("watermark_record_hash"),
        "seen_record_key_count": len(keys),
        "seen_record_keys_sha256": sha256_lines(keys),
        "seen_record_keys": keys,
        "last_successful_run_id": source_summary.get("last_successful_run_id") or existing.get("last_successful_run_id"),
        "last_successful_source_file": source_summary.get("last_successful_source_file")
        or existing.get("last_successful_source_file"),
        "last_successful_content_sha256": source_summary.get("last_successful_content_sha256")
        or existing.get("last_successful_content_sha256"),
        "bootstrap_source_file_count": int(source_summary.get("source_file_count") or existing.get("bootstrap_source_file_count") or 0),
        "bootstrap_source_row_count": int(source_summary.get("source_row_count") or existing.get("bootstrap_source_row_count") or 0),
        "bootstrap_unique_record_count": int(
            source_summary.get("unique_record_count") or existing.get("bootstrap_unique_record_count") or len(keys)
        ),
        "safety_flags": safety_flags(
            write_report_requested=False,
            write_report_performed=False,
            write_watermark_requested=False,
            write_watermark_performed=False,
            training_prevented_by_watermark=False,
        ),
    }


def empty_watermark_state(generated_at_utc: str) -> dict[str, Any]:
    return build_watermark_state(
        generated_at_utc=generated_at_utc,
        existing_state=None,
        normalized=pd.DataFrame(),
        seen_record_keys=[],
        bootstrap_sources_summary={},
    )


def normalize_records(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy().reset_index(drop=True)
    close_column = first_present(working.columns, CLOSE_TIME_ALIASES)
    close_series = (
        pd.to_datetime(working[close_column], utc=True, errors="coerce")
        if close_column is not None
        else pd.Series([pd.NaT] * len(working), index=working.index)
    )
    working[INTERNAL_CLOSE_TIME] = close_series
    keys, strategies = compute_record_keys(working)
    working[INTERNAL_RECORD_KEY] = keys
    working[INTERNAL_RECORD_STRATEGY] = strategies
    working[INTERNAL_RECORD_HASH] = compute_row_hashes(working)
    return working


def compute_record_keys(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    record_hash_column = first_present(frame.columns, RECORD_HASH_ALIASES)
    if record_hash_column is not None:
        normalized_hash = frame[record_hash_column].map(normalize_id_value)
        valid = normalized_hash.map(bool)
        if bool(valid.any()):
            keys = pd.Series([""] * len(frame), index=frame.index)
            strategies = pd.Series([""] * len(frame), index=frame.index)
            keys.loc[valid] = "record_hash:" + normalized_hash.loc[valid]
            strategies.loc[valid] = "record_hash"
            fallback_keys, fallback_strategies = compute_fallback_record_keys(frame.loc[~valid])
            keys.loc[~valid] = fallback_keys
            strategies.loc[~valid] = fallback_strategies
            return keys, strategies
    return compute_fallback_record_keys(frame)


def compute_fallback_record_keys(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if frame.empty:
        return pd.Series(dtype="object", index=frame.index), pd.Series(dtype="object", index=frame.index)

    close_column = first_present(frame.columns, CLOSE_TIME_ALIASES)
    order_column = first_present(frame.columns, ORDER_ID_ALIASES)
    if order_column is not None and close_column is not None:
        order = frame[order_column].map(normalize_id_value)
        close = frame[close_column].map(normalize_time_value)
        valid = order.map(bool) & close.map(bool)
        if bool(valid.all()):
            return "order_close:" + order + "|" + close, pd.Series(["order_id_close_time"] * len(frame), index=frame.index)

    trade_column = first_present(frame.columns, TRADE_ID_ALIASES)
    if trade_column is not None and close_column is not None:
        trade = frame[trade_column].map(normalize_id_value)
        close = frame[close_column].map(normalize_time_value)
        valid = trade.map(bool) & close.map(bool)
        if bool(valid.all()):
            return "trade_close:" + trade + "|" + close, pd.Series(["trade_id_close_time"] * len(frame), index=frame.index)

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
            return (
                "composite:" + parts[0] + "|" + parts[1] + "|" + parts[2] + "|" + parts[3] + "|" + parts[4],
                pd.Series(["symbol_side_time_pnl"] * len(frame), index=frame.index),
            )

    return compute_row_hashes(frame).map(lambda value: f"row_hash:{value}"), pd.Series(
        ["normalized_row_hash"] * len(frame), index=frame.index
    )


def summarize_normalized_records(frame: pd.DataFrame) -> dict[str, Any]:
    keys = frame[INTERNAL_RECORD_KEY].astype(str).tolist() if INTERNAL_RECORD_KEY in frame else []
    unique_keys = sorted(set(keys))
    duplicate_count = max(len(keys) - len(unique_keys), 0)
    strategies = frame[INTERNAL_RECORD_STRATEGY].astype(str).tolist() if INTERNAL_RECORD_STRATEGY in frame else []
    return {
        "unique_record_keys": unique_keys,
        "unique_record_count": len(unique_keys),
        "duplicate_record_count": duplicate_count,
        "duplicate_rate": round(duplicate_count / len(keys), 10) if keys else 0.0,
        "record_key_strategy_counts": dict(sorted(Counter(strategies).items())),
    }


def load_existing_microbatches(quarantine_dir: Path) -> dict[str, Any]:
    paths = discover_microbatches(quarantine_dir)
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    content_hashes: list[str] = []
    for path in paths:
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError, ImportError) as exc:
            warnings.append(f"microbatch_read_failed:{path.parent.name}:{exc.__class__.__name__}")
            continue
        normalized = normalize_records(frame)
        content_hashes.append(sha256_lines(sorted(normalized[INTERNAL_RECORD_KEY].astype(str).tolist())))
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True, sort=True) if frames else pd.DataFrame()
    return {
        "paths": paths,
        "frame": combined,
        "warnings": warnings,
        "source_file_count": len(frames),
        "last_run_id": paths[-1].parent.name if paths else None,
        "last_source_file": str(paths[-1]) if paths else None,
        "last_content_sha256": content_hashes[-1] if content_hashes else None,
    }


def discover_microbatches(quarantine_dir: Path) -> list[Path]:
    if not quarantine_dir.is_dir():
        return []
    return sorted(quarantine_dir.glob(f"*/{MICROBATCH_FILENAME}"), key=lambda path: path.as_posix())


def read_watermark_state(path: Path) -> WatermarkRead:
    if not path.exists():
        return WatermarkRead(False, "missing", None, (), ())
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return WatermarkRead(True, "invalid", None, (), (f"watermark_state_invalid:{exc.__class__.__name__}",))
    errors = validate_watermark_state(payload)
    if errors:
        return WatermarkRead(True, "invalid", payload, (), tuple(errors))
    return WatermarkRead(True, "ok", payload, (), ())


def validate_watermark_state(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != WATERMARK_SCHEMA_VERSION:
        errors.append("watermark_schema_version_invalid")
    keys = payload.get("seen_record_keys")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        errors.append("watermark_seen_record_keys_invalid")
        return errors
    expected_count = int(payload.get("seen_record_key_count") or -1)
    if expected_count != len(keys):
        errors.append("watermark_seen_record_key_count_mismatch")
    expected_hash = payload.get("seen_record_keys_sha256")
    if expected_hash != sha256_lines(sorted(keys)):
        errors.append("watermark_seen_record_keys_hash_mismatch")
    return errors


def load_seen_keys(state: Mapping[str, Any] | None) -> list[str]:
    if not state:
        return []
    keys = state.get("seen_record_keys")
    if not isinstance(keys, list):
        return []
    return sorted(str(key) for key in keys if str(key).strip())


def write_requested_outputs(
    report: dict[str, Any],
    paths: WatermarkPaths,
    write_report_requested: bool,
    write_watermark_requested: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if write_errors:
        return report
    write_report_done = False
    write_watermark_done = False
    if write_watermark_requested and report["status"] != "blocked" or (
        write_watermark_requested and report["reason"] == "watermark_bootstrap_required"
    ):
        write_watermark_state(paths.watermark_path, report["watermark_summary"]["state"])
        write_watermark_done = True
    if write_report_requested:
        atomic_write_text(paths.output_json, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n")
        atomic_write_text(paths.output_markdown, render_markdown(report))
        write_report_done = True
    safety = safety_flags(
        write_report_requested=write_report_requested,
        write_report_performed=write_report_done,
        write_watermark_requested=write_watermark_requested,
        write_watermark_performed=write_watermark_done,
        training_prevented_by_watermark=bool(report.get("training_prevented_by_watermark")),
    )
    report.update(safety)
    report["safety_flags"] = safety
    if write_watermark_done:
        report["watermark_exists"] = True
        report["watermark_status"] = "ok"
    if write_report_done:
        atomic_write_text(paths.output_json, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n")
        atomic_write_text(paths.output_markdown, render_markdown(report))
    return report


def write_watermark_state(path: Path, state: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def build_base_report(
    *,
    root: Path,
    generated_at: str,
    status: str,
    reason: str,
    decision: str,
    paths: WatermarkPaths,
    output_paths: Mapping[str, str],
    write_report: bool,
    write_watermark_state_requested: bool,
    blockers: Sequence[str],
    warnings: Sequence[str],
) -> dict[str, Any]:
    safety = safety_flags(
        write_report_requested=write_report,
        write_report_performed=False,
        write_watermark_requested=write_watermark_state_requested,
        write_watermark_performed=False,
        training_prevented_by_watermark=False,
    )
    empty_state = empty_watermark_state(generated_at)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "source_file_count": 0,
        "source_row_count": 0,
        "unique_record_count": 0,
        "duplicate_record_count": 0,
        "duplicate_rate": 0.0,
        "watermark_path": str(paths.watermark_path),
        "watermark_exists": False,
        "watermark_status": "missing",
        "watermark_close_time_utc": None,
        "watermark_order_id": None,
        "watermark_record_hash": None,
        "seen_record_key_count": 0,
        "seen_record_keys_sha256": None,
        "bootstrap_required": False,
        "would_initialize_watermark": False,
        "bootstrap_source_file_count": 0,
        "bootstrap_source_row_count": 0,
        "bootstrap_unique_record_count": 0,
        "new_unique_records_count": 0,
        "already_seen_record_count": 0,
        "stale_duplicate_microbatch_prevented": False,
        "training_prevented_by_watermark": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "record_key_strategy_counts": {},
        "incremental_summary": {},
        "watermark_summary": {"state": empty_state},
        "blockers": list(blockers),
        "warnings": list(warnings),
        "output_paths": dict(output_paths),
        **safety,
        "safety_flags": safety,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Autotrain Incremental Watermark Fix V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Watermark status: `{report.get('watermark_status')}`",
            f"- Source rows: `{report.get('source_row_count')}`",
            f"- Unique rows: `{report.get('unique_record_count')}`",
            f"- Duplicate rate: `{report.get('duplicate_rate')}`",
            f"- New records: `{report.get('new_unique_records_count')}`",
            f"- Already seen records: `{report.get('already_seen_record_count')}`",
            "",
            "## Conclusao operacional",
            "",
            "Este relatorio nao tem autoridade operacional. Ele nao treina, nao promove, nao altera runtime,",
            "nao altera registry ativo, nao escreve sinais, nao toca Freqtrade/RiskManager e nao envia ordens.",
            "",
        ]
    )


def summarize_watermark_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": state.get("schema_version"),
        "watermark_close_time_utc": state.get("watermark_close_time_utc"),
        "watermark_order_id": state.get("watermark_order_id"),
        "watermark_record_hash": state.get("watermark_record_hash"),
        "seen_record_key_count": state.get("seen_record_key_count"),
        "seen_record_keys_sha256": state.get("seen_record_keys_sha256"),
        "state": dict(state),
    }


def build_paths(
    root: Path,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
    watermark_path: str | Path | None,
) -> WatermarkPaths:
    return WatermarkPaths(
        quarantine_dir=root / DEFAULT_QUARANTINE_DIR,
        watermark_path=resolve_path(root, watermark_path, DEFAULT_WATERMARK_PATH),
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def validate_write_requests(
    root: Path,
    paths: WatermarkPaths,
    write_report: bool,
    write_watermark_state_requested: bool,
) -> list[str]:
    errors: list[str] = []
    if write_report:
        errors.extend(validate_path_under(root, paths.output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
        errors.extend(validate_path_under(root, paths.output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    if write_watermark_state_requested:
        errors.extend(
            validate_path_under(root, paths.watermark_path, ALLOWED_WATERMARK_ROOT, "watermark_path_outside_research_root")
        )
    return sorted_unique(errors)


def validate_path_under(root: Path, path: Path, allowed: Path, reason: str) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed).resolve())
    except ValueError:
        return [reason]
    return []


def safety_flags(
    *,
    write_report_requested: bool,
    write_report_performed: bool,
    write_watermark_requested: bool,
    write_watermark_performed: bool,
    training_prevented_by_watermark: bool,
) -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "read_only": not (write_report_requested or write_watermark_requested),
        "write_performed": bool(write_report_performed or write_watermark_performed),
        "write_report_requested": bool(write_report_requested),
        "write_report_performed": bool(write_report_performed),
        "write_watermark_requested": bool(write_watermark_requested),
        "write_watermark_performed": bool(write_watermark_performed),
        "writes_research_watermark": bool(write_watermark_performed),
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
        "training_prevented_by_watermark": bool(training_prevented_by_watermark),
        "promotes_model": False,
        "promotion_allowed": False,
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


def safety_flag_keys() -> set[str]:
    return set(
        safety_flags(
            write_report_requested=False,
            write_report_performed=False,
            write_watermark_requested=False,
            write_watermark_performed=False,
            training_prevented_by_watermark=False,
        )
    )


def compute_row_hashes(frame: pd.DataFrame) -> pd.Series:
    content_columns = sorted(column for column in frame.columns if not str(column).startswith("__watermark_"))

    def hash_row(row: pd.Series) -> str:
        parts = [f"{column}={normalize_scalar(row[column])}" for column in content_columns]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    if frame.empty:
        return pd.Series(dtype="object", index=frame.index)
    return frame[content_columns].apply(hash_row, axis=1)


def extract_close_times(frame: pd.DataFrame) -> list[pd.Timestamp]:
    if INTERNAL_CLOSE_TIME not in frame:
        return []
    return [value for value in frame[INTERNAL_CLOSE_TIME].dropna().tolist() if isinstance(value, pd.Timestamp)]


def extract_column_values(frame: pd.DataFrame, aliases: Sequence[str]) -> list[str]:
    column = first_present(frame.columns, aliases)
    if column is None:
        return []
    return sorted(value for value in frame[column].map(normalize_id_value).tolist() if value)


def first_present(columns: Sequence[Any], aliases: Sequence[str]) -> str | None:
    column_set = {str(column) for column in columns}
    for alias in aliases:
        if alias in column_set:
            return alias
    return None


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


def sha256_lines(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


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
