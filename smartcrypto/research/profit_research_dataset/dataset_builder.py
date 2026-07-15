"""Orchestrator for deterministic paper trade and candle research evidence."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile

from .candle_alignment import align_trades_to_candles, load_candles
from .contracts import (
    DATASET_ID,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    DatasetBuildPaths,
    DatasetBuildResult,
    dataset_contract,
)
from .economic_segments import (
    add_segmentation_buckets,
    build_economic_segments,
    evaluate_btc_block_hypothesis,
    financial_metrics,
)
from .entry_features import attach_entry_features, leakage_violation_count
from .path_features import attach_path_features
from .report import render_markdown, stable_json
from .source_inventory import (
    file_sha256,
    inventory_frame,
    inventory_sqlite_snapshot,
    stable_frame_sha256,
)
from .trade_snapshot import build_paper_trade_snapshot


def build_profit_research_dataset(
    paths: DatasetBuildPaths,
    *,
    timeframe: str = "5m",
    allow_runtime_read: bool = False,
    write_report: bool = False,
    write_dataset: bool = False,
    generated_at_utc: str | None = None,
) -> DatasetBuildResult:
    report = _base_report(
        paths,
        timeframe=timeframe,
        allow_runtime_read=allow_runtime_read,
        write_report=write_report,
        write_dataset=write_dataset,
        generated_at_utc=generated_at_utc,
    )
    if not allow_runtime_read:
        report.update(status="blocked", reason="runtime_read_not_allowed")
        return DatasetBuildResult(pd.DataFrame(), (), report)
    try:
        profile = load_source_profile(paths.source_profile)
        source_path, authoritative = _select_paper_source(paths)
        trades, trade_metadata = build_paper_trade_snapshot(
            project_root=paths.project_root,
            source_path=source_path,
            profile=profile,
            authoritative_snapshot=authoritative,
        )
        report["paper_source_path"] = str(source_path)
        report["paper_source_metadata"] = trade_metadata
        if trades.empty or trade_metadata.get("status") != "ok":
            report.update(status="blocked", reason=str(trade_metadata.get("reason", "paper_source_empty")))
            return DatasetBuildResult(pd.DataFrame(), (), report)

        candle_load = load_candles(paths.candle_root, timeframe=timeframe)
        report["warnings"].extend(candle_load.warnings)
        if candle_load.frame.empty:
            report.update(status="blocked", reason="candle_source_unavailable")
            report["paper_row_count"] = int(len(trades))
            return DatasetBuildResult(trades, (), report)

        alignment = align_trades_to_candles(
            trades,
            candle_load.frame,
            timeframe=timeframe,
        )
        dataset = attach_entry_features(
            alignment.frame,
            candle_load.frame,
            timeframe=timeframe,
        )
        dataset = attach_path_features(dataset, alignment.paths_by_trade)
        dataset = add_segmentation_buckets(dataset)
        dataset = dataset.sort_values(["open_time_utc", "stable_trade_id"]).reset_index(drop=True)
        eligible = dataset.loc[dataset["analysis_eligible"]].copy()
        segments = build_economic_segments(eligible)
        metrics = financial_metrics(eligible)
        btc_hypothesis = evaluate_btc_block_hypothesis(eligible)
        source_inventory = [
            inventory_sqlite_snapshot(path=source_path, frame=trades, metadata=trade_metadata),
            inventory_frame(
                path=paths.candle_root,
                source_type="local_market_candles",
                frame=candle_load.frame,
                read_only_status="read_only_file",
            ),
        ]
        report.update(
            status="ok",
            reason="profit_research_dataset_built_readonly",
            source_inventory=source_inventory,
            paper_row_count=int(len(dataset)),
            eligible_trade_count=int(dataset["analysis_eligible"].sum()),
            rejected_trade_count=int((~dataset["analysis_eligible"]).sum()),
            candle_aligned_trade_count=int(dataset["candle_alignment_status"].eq("aligned").sum()),
            candle_unaligned_trade_count=int(dataset["candle_alignment_status"].eq("unaligned").sum()),
            candle_coverage_ratio=_coverage_ratio(dataset),
            entry_feature_complete_count=int(dataset["entry_feature_complete"].eq(True).sum()),
            path_feature_complete_count=int(dataset["path_feature_complete"].eq(True).sum()),
            leakage_violation_count=leakage_violation_count(dataset),
            imputed_candle_count=0,
            duplicate_trade_count=int(trade_metadata.get("duplicate_trade_count", 0)),
            net_pnl=metrics["net_pnl"],
            profit_factor=metrics["profit_factor"],
            max_drawdown=metrics["max_drawdown"],
            total_fees=metrics["fee_total"],
            winner_to_loser_count=int(dataset["winner_to_loser_conversion"].eq(True).sum()),
            financial_metrics=metrics,
            economic_segment_count=len(segments),
            economic_segments=list(segments),
            btc_block_hypothesis=btc_hypothesis,
            dataset_contract=dataset_contract(paths),
            dataset_in_memory_sha256=stable_frame_sha256(dataset),
            candle_duplicate_count=candle_load.duplicate_candle_count,
            candle_missing_reason_counts=_candle_missing_counts(dataset),
            rejection_reason_counts=_rejection_counts(dataset),
        )
        if report["leakage_violation_count"] != 0:
            report.update(status="blocked", reason="entry_feature_temporal_leakage_detected")
            return DatasetBuildResult(dataset, segments, report)
        if report["candle_aligned_trade_count"] == 0:
            report.update(
                status="warning",
                reason="no_candle_alignment_for_requested_timeframe",
            )
        if write_report or write_dataset:
            _materialize_outputs(
                paths,
                dataset=dataset,
                report=report,
                write_report=write_report,
                write_dataset=write_dataset,
            )
            report["write_performed"] = True
            report["outputs_written"] = _written_paths(
                paths,
                write_report=write_report,
                write_dataset=write_dataset,
            )
        return DatasetBuildResult(dataset, segments, report)
    except (OSError, ValueError, KeyError, TypeError, ImportError, pd.errors.ParserError) as exc:
        report.update(
            status="blocked",
            reason="profit_research_dataset_build_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return DatasetBuildResult(pd.DataFrame(), (), report)


def _base_report(
    paths: DatasetBuildPaths,
    *,
    timeframe: str,
    allow_runtime_read: bool,
    write_report: bool,
    write_dataset: bool,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "status": "blocked",
        "reason": "not_evaluated",
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe,
        "runtime_read_requested": allow_runtime_read,
        "write_report_requested": write_report,
        "write_dataset_requested": write_dataset,
        "write_performed": False,
        "outputs_written": [],
        "paper_row_count": 0,
        "eligible_trade_count": 0,
        "rejected_trade_count": 0,
        "candle_aligned_trade_count": 0,
        "candle_unaligned_trade_count": 0,
        "candle_coverage_ratio": 0.0,
        "entry_feature_complete_count": 0,
        "path_feature_complete_count": 0,
        "leakage_violation_count": 0,
        "imputed_candle_count": 0,
        "duplicate_trade_count": 0,
        "net_pnl": 0.0,
        "profit_factor": None,
        "max_drawdown": 0.0,
        "total_fees": None,
        "winner_to_loser_count": 0,
        "candle_missing_reason_counts": {},
        "source_inventory": [],
        "warnings": [],
        "validation_errors": [],
        **SAFETY_FLAGS,
    }


def _select_paper_source(paths: DatasetBuildPaths) -> tuple[Path, bool]:
    if paths.paper_snapshot_db.is_file() and not paths.paper_snapshot_db.is_symlink():
        return paths.paper_snapshot_db, True
    return paths.paper_db, False


def _coverage_ratio(frame: pd.DataFrame) -> float:
    eligible = frame.loc[frame["analysis_eligible"]]
    if eligible.empty:
        return 0.0
    return float(eligible["candle_alignment_status"].eq("aligned").mean())


def _rejection_counts(frame: pd.DataFrame) -> dict[str, int]:
    reasons = frame.loc[~frame["analysis_eligible"], "rejection_reason"].fillna("unknown")
    return {str(key): int(value) for key, value in reasons.value_counts().sort_index().items()}


def _candle_missing_counts(frame: pd.DataFrame) -> dict[str, int]:
    reasons = frame["candle_missing_reason"].dropna().astype(str)
    return {str(key): int(value) for key, value in reasons.value_counts().sort_index().items()}


def _materialize_outputs(
    paths: DatasetBuildPaths,
    *,
    dataset: pd.DataFrame,
    report: dict[str, Any],
    write_report: bool,
    write_dataset: bool,
) -> None:
    _validate_output_paths(paths)
    if write_dataset:
        _atomic_parquet(paths.dataset_parquet, dataset)
        artifact_hash = file_sha256(paths.dataset_parquet)
        contract = dataset_contract(paths)
        contract["artifact_hashes"] = {"dataset_parquet_sha256": artifact_hash}
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": DATASET_ID,
            "dataset_sha256": artifact_hash,
            "dataset_in_memory_sha256": report["dataset_in_memory_sha256"],
            "row_count": int(len(dataset)),
            "sort_order": ["open_time_utc", "stable_trade_id"],
            "source_inventory": report["source_inventory"],
        }
        _atomic_text(paths.dataset_manifest, stable_json(manifest))
        _atomic_text(paths.dataset_schema, stable_json(contract))
        _atomic_text(
            paths.coverage_sidecar,
            stable_json(
                {
                    "candle_aligned_trade_count": report["candle_aligned_trade_count"],
                    "candle_unaligned_trade_count": report["candle_unaligned_trade_count"],
                    "candle_coverage_ratio": report["candle_coverage_ratio"],
                    "entry_feature_complete_count": report["entry_feature_complete_count"],
                    "path_feature_complete_count": report["path_feature_complete_count"],
                }
            ),
        )
        _atomic_text(
            paths.rejection_sidecar,
            stable_json({"rejection_reason_counts": report["rejection_reason_counts"]}),
        )
    if write_report:
        materialized = {**report, "write_performed": True}
        _atomic_text(paths.report_json, stable_json(materialized))
        _atomic_text(paths.report_markdown, render_markdown(materialized))


def _validate_output_paths(paths: DatasetBuildPaths) -> None:
    data_root = (paths.project_root / "data").resolve()
    for path in (
        paths.report_json,
        paths.report_markdown,
        paths.dataset_parquet,
        paths.dataset_manifest,
        paths.dataset_schema,
        paths.coverage_sidecar,
        paths.rejection_sidecar,
    ):
        try:
            path.resolve().relative_to(data_root)
        except ValueError as exc:
            raise ValueError(f"output_path_outside_data:{path}") from exc


def _written_paths(
    paths: DatasetBuildPaths,
    *,
    write_report: bool,
    write_dataset: bool,
) -> list[str]:
    result: list[Path] = []
    if write_report:
        result.extend((paths.report_json, paths.report_markdown))
    if write_dataset:
        result.extend(
            (
                paths.dataset_parquet,
                paths.dataset_manifest,
                paths.dataset_schema,
                paths.coverage_sidecar,
                paths.rejection_sidecar,
            )
        )
    return [str(path) for path in result]


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".parquet", dir=path.parent
    )
    os.close(descriptor)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
