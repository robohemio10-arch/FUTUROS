"""No-write-by-default orchestration for Canonical Data Foundation V2."""

from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import pandas as pd

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWritePolicy,
    atomic_write_json,
    atomic_write_text,
)

from .candles import CandleSourceSpec, recover_blocked_candles
from .contracts import (
    CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION,
    DATASET_CONTRACTS,
    SAFETY_FLAGS,
    DatasetBoundaryError,
    build_dataset_manifest,
    stable_hash,
    validate_dataset_write,
)
from .lineage import build_trader_master_lineage
from .manifest import (
    EXECUTION_MANIFEST_SCHEMA_VERSION,
    ExecutionManifest,
    build_execution_manifest,
    hash_file,
    write_execution_manifest,
)
from .reporting import render_foundation_markdown

DEFAULT_PRIMARY_CANDLE_SOURCES = (
    CandleSourceSpec(
        source_id="binance_usdt_m_futures_1m_canonical_archive",
        source_type="primary_public_archive",
        timeframe="1min",
        paths=(
            "data/raw/binance_futures_klines/BTCUSDT_1m_20251230_20261208.parquet",
            "data/raw/binance_futures_klines/ETHUSDT_1m_20251230_20261208.parquet",
        ),
        public_endpoint="https://fapi.binance.com/fapi/v1/klines",
        priority=1,
    ),
)
DEFAULT_SECONDARY_CANDLE_SOURCES = (
    CandleSourceSpec(
        source_id="bitradex_public_futures_5m_secondary_archive",
        source_type="secondary_public_archive",
        timeframe="5min",
        paths=(
            "data/raw/bitradex_candles/bitradex_btc_usdt_futures_5m.parquet",
            "data/raw/bitradex_candles/bitradex_eth_usdt_futures_5m.parquet",
        ),
        public_endpoint="https://www.bitradex.ai/v1/future-u/market/public/q/kline",
        priority=1,
    ),
)


def build_canonical_data_foundation_report(
    *,
    project_root: str | Path,
    trader_master_path: str | Path = "data/trades/trades_master.parquet",
    blocked_trades_path: str | Path = (
        "data/research/ocr_v11_trade_research_dataset.parquet"
    ),
    primary_candle_sources: Sequence[CandleSourceSpec] = DEFAULT_PRIMARY_CANDLE_SOURCES,
    secondary_candle_sources: Sequence[CandleSourceSpec] = (
        DEFAULT_SECONDARY_CANDLE_SOURCES
    ),
    write_report: bool = False,
    output_json: str | Path = "data/reports/canonical_data_foundation_v2.json",
    output_markdown: str | Path = "data/reports/canonical_data_foundation_v2.md",
    manifest_output_root: str | Path = (
        "data/reports/canonical_data_foundation_v2/manifests"
    ),
    generated_at_utc: str | None = None,
    execution_id: str | None = None,
    command: str = "build_canonical_data_foundation_v2",
    arguments: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the complete B02 evidence pack without mutating canonical data."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    resolved_execution_id = execution_id or (
        f"b02-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    )
    source_inventory = _source_inventory(
        root,
        trader_master_path=trader_master_path,
        blocked_trades_path=blocked_trades_path,
        primary_candle_sources=primary_candle_sources,
        secondary_candle_sources=secondary_candle_sources,
    )
    lineage = build_trader_master_lineage(
        project_root=root,
        trader_master_path=trader_master_path,
    )
    blocked_frame, blocked_error = _read_blocked_trades(root, blocked_trades_path)
    if blocked_error is None:
        candle_result = recover_blocked_candles(
            project_root=root,
            blocked_trades=blocked_frame,
            primary_sources=primary_candle_sources,
            secondary_sources=secondary_candle_sources,
        )
        candle_report = dict(candle_result.report)
    else:
        candle_report = {
            "schema_version": "canonical_candle_recovery_v2",
            "status": "blocked",
            "reason": blocked_error,
            "candle_blocked_input_rows": 0,
            "candle_recovered_verified_rows": 0,
            "candle_permanent_quarantine_rows": 0,
            "candle_unresolved_rows": 0,
            "forward_fill_used": False,
            "gaps_preserved": True,
            "record_set_hash": stable_hash([]),
        }

    git_state = _git_state(root)
    dataset_foundation = _build_dataset_foundation(
        git_commit_sha=git_state["commit_sha"] or "unresolved",
        created_at_utc=generated_at,
        source_inventory=source_inventory,
        lineage_report=lineage.report,
        candle_report=candle_report,
    )
    manifest_contract, execution_manifest = _build_manifest_contract(
        root=root,
        generated_at_utc=generated_at,
        execution_id=resolved_execution_id,
        command=command,
        arguments=arguments,
        git_state=git_state,
        source_inventory=source_inventory,
        lineage_report=lineage.report,
        candle_report=candle_report,
        dataset_foundation=dataset_foundation,
    )
    all_lineage_terminal = bool(lineage.report.get("all_rows_terminal"))
    all_candles_terminal = (
        int(candle_report.get("candle_blocked_input_rows") or 0)
        == int(candle_report.get("candle_recovered_verified_rows") or 0)
        + int(candle_report.get("candle_permanent_quarantine_rows") or 0)
    )
    blockers: list[str] = []
    if lineage.report.get("status") != "ok" or not all_lineage_terminal:
        blockers.append("trader_master_lineage_not_terminal")
    if candle_report.get("status") != "ok" or not all_candles_terminal:
        blockers.append("blocked_candle_rows_not_terminal")
    if dataset_foundation.get("status") != "ok":
        blockers.append("dataset_boundaries_not_certified")
    if not manifest_contract.get("hash_reproducible"):
        blockers.append("execution_manifest_hash_not_reproducible")

    report: dict[str, Any] = {
        "schema_version": CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": "ok" if not blockers else "blocked",
        "reason": "canonical_data_foundation_v2_pass" if not blockers else blockers[0],
        "decision": (
            "CANONICAL_DATA_FOUNDATION_V2_PASS"
            if not blockers
            else "CANONICAL_DATA_FOUNDATION_V2_BLOCKED"
        ),
        "gate_b02": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "warnings": [
            "legacy_master_values_without_authoritative_decomposition_are_quarantined",
            "current_operational_feature_materialization_not_used_as_candle_authority",
        ],
        "source_map": source_inventory,
        "trader_master_lineage": dict(lineage.report),
        "candle_recovery": candle_report,
        "dataset_foundation": dataset_foundation,
        "manifest_contract": manifest_contract,
        "write_requested": bool(write_report),
        "write_performed": bool(write_report),
        "output_json": _display(_resolve(root, output_json), root),
        "output_markdown": _display(_resolve(root, output_markdown), root),
        "versioned_runtime_artifacts": 0,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    write_results: dict[str, Any] = {}
    if write_report:
        policy = AtomicWritePolicy.restricted(
            [(root / "data" / "reports").resolve(strict=False)],
            working_directory=root,
        )
        json_result = atomic_write_json(
            _resolve(root, output_json),
            report,
            policy=policy,
            allow_nan=False,
        )
        markdown_result = atomic_write_text(
            _resolve(root, output_markdown),
            render_foundation_markdown(report),
            policy=policy,
        )
        execution_write = write_execution_manifest(
            manifest=execution_manifest,
            output_root=manifest_output_root,
            project_root=root,
        )
        write_results = {
            "report_json_written": json_result.write_performed,
            "report_markdown_written": markdown_result.write_performed,
            "execution_manifest": execution_write,
        }
    report["write_results"] = write_results
    return report


def _build_dataset_foundation(
    *,
    git_commit_sha: str,
    created_at_utc: str,
    source_inventory: Mapping[str, Any],
    lineage_report: Mapping[str, Any],
    candle_report: Mapping[str, Any],
) -> dict[str, Any]:
    contracts = [contract.to_dict() for contract in DATASET_CONTRACTS.values()]
    writers = {contract.writer_id for contract in DATASET_CONTRACTS.values()}
    readers = {contract.reader_id for contract in DATASET_CONTRACTS.values()}
    authorities = {contract.authority for contract in DATASET_CONTRACTS.values()}
    roots = {contract.root_path for contract in DATASET_CONTRACTS.values()}
    guard_checks: list[dict[str, Any]] = []
    for contract in DATASET_CONTRACTS.values():
        columns: list[str]
        rows: list[dict[str, Any]]
        if contract.dataset_class == "PaperOutcomeDataset":
            columns = ["paper_trade_id", "close_time_utc", "is_closed", "reconciliation_status"]
            rows = [{"is_closed": True, "reconciliation_status": "VERIFIED"}]
        elif contract.dataset_class == "OperationalFeatureDataset":
            columns = ["symbol", "timeframe", "feature_timestamp_utc", "feature_rsi"]
            rows = []
        else:
            columns = ["source_record_reference", "verification_status"]
            rows = []
        guard_checks.append(
            validate_dataset_write(
                contract=contract,
                writer_id=contract.writer_id,
                target_path=f"{contract.root_path}/candidate.json",
                columns=columns,
                rows=rows,
                source_dataset_class=contract.dataset_class,
            )
        )
    blocked_guard_reasons = _negative_guard_probes()
    source_hashes = {
        key: value
        for key, value in _flatten_source_hashes(source_inventory).items()
        if value
    }
    manifests = {}
    for contract in DATASET_CONTRACTS.values():
        manifests[contract.dataset_class] = build_dataset_manifest(
            contract=contract,
            columns=(
                ["source_record_reference", "verification_status"]
                if contract.dataset_class == "HistoricalResearchDataset"
                else ["paper_trade_id", "close_time_utc", "is_closed"]
                if contract.dataset_class == "PaperOutcomeDataset"
                else ["symbol", "timeframe", "feature_timestamp_utc", "feature_rsi"]
            ),
            row_count=(
                int(lineage_report.get("total_rows") or 0)
                if contract.dataset_class == "HistoricalResearchDataset"
                else 0
            ),
            source_manifest={
                "source_hashes": source_hashes,
                "lineage_record_set_hash": lineage_report.get("record_set_hash"),
                "candle_record_set_hash": candle_report.get("record_set_hash"),
            },
            git_commit_sha=git_commit_sha,
            created_at_utc=created_at_utc,
        )
    independent = len(contracts) == len(writers) == len(readers) == len(authorities) == len(roots)
    return {
        "status": "ok" if independent and len(blocked_guard_reasons) == 6 else "blocked",
        "reason": (
            "canonical_dataset_boundaries_certified"
            if independent and len(blocked_guard_reasons) == 6
            else "canonical_dataset_boundary_certification_failed"
        ),
        "dataset_contract_count": len(contracts),
        "contracts": contracts,
        "dataset_manifests": manifests,
        "writers_independent": len(writers) == len(contracts),
        "readers_independent": len(readers) == len(contracts),
        "authorities_independent": len(authorities) == len(contracts),
        "paths_independent": len(roots) == len(contracts),
        "cross_write_guards_active": len(blocked_guard_reasons) == 6,
        "positive_guard_checks": guard_checks,
        "negative_guard_reasons": blocked_guard_reasons,
        "write_performed": False,
    }


def _negative_guard_probes() -> list[str]:
    probes = [
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["HistoricalResearchDataset"],
            writer_id=DATASET_CONTRACTS["PaperOutcomeDataset"].writer_id,
            target_path="data/research/canonical/historical/x.json",
            columns=["x"],
        ),
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["HistoricalResearchDataset"],
            writer_id=DATASET_CONTRACTS["HistoricalResearchDataset"].writer_id,
            target_path="data/feedback/canonical/paper_outcomes/x.json",
            columns=["x"],
        ),
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["OperationalFeatureDataset"],
            writer_id=DATASET_CONTRACTS["OperationalFeatureDataset"].writer_id,
            target_path="data/features/canonical/operational/x.json",
            columns=["feature_rsi", "target_profitable"],
        ),
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["OperationalFeatureDataset"],
            writer_id=DATASET_CONTRACTS["OperationalFeatureDataset"].writer_id,
            target_path="data/features/canonical/operational/x.json",
            columns=["future_ret_5m"],
        ),
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["PaperOutcomeDataset"],
            writer_id=DATASET_CONTRACTS["PaperOutcomeDataset"].writer_id,
            target_path="data/feedback/canonical/paper_outcomes/x.json",
            columns=["is_closed", "reconciliation_status"],
            rows=[{"is_closed": False, "reconciliation_status": "VERIFIED"}],
        ),
        lambda: validate_dataset_write(
            contract=DATASET_CONTRACTS["HistoricalResearchDataset"],
            writer_id=DATASET_CONTRACTS["HistoricalResearchDataset"].writer_id,
            target_path="data/research/canonical/historical/active_signals.json",
            columns=["signal"],
            publishes_active_signal=True,
        ),
    ]
    reasons: list[str] = []
    for probe in probes:
        try:
            probe()
        except DatasetBoundaryError as exc:
            reasons.append(exc.reason)
    return reasons


def _build_manifest_contract(
    *,
    root: Path,
    generated_at_utc: str,
    execution_id: str,
    command: str,
    arguments: Sequence[str],
    git_state: Mapping[str, Any],
    source_inventory: Mapping[str, Any],
    lineage_report: Mapping[str, Any],
    candle_report: Mapping[str, Any],
    dataset_foundation: Mapping[str, Any],
) -> tuple[dict[str, Any], Any]:
    source_hashes = _flatten_source_hashes(source_inventory)
    if not source_hashes:
        source_hashes = {"empty_source_inventory": stable_hash({})}
    lock_path = root / "requirements-dev.lock"
    dependency_hash = hash_file(lock_path) if lock_path.exists() else None
    dataset_hash = stable_hash(
        {
            "lineage": lineage_report.get("record_set_hash"),
            "candles": candle_report.get("record_set_hash"),
        }
    )
    dataset_manifest_hash = stable_hash(dataset_foundation.get("dataset_manifests", {}))
    config_hash = stable_hash(
        {
            "contracts": dataset_foundation.get("contracts"),
            "source_ids": sorted(source_hashes),
        }
    )
    schema_hash = stable_hash(
        {
            "foundation": CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION,
            "manifest": EXECUTION_MANIFEST_SCHEMA_VERSION,
        }
    )
    def make_manifest(
        manifest_execution_id: str,
        started_at: str,
        completed_at: str,
    ) -> ExecutionManifest:
        return build_execution_manifest(
            execution_id=manifest_execution_id,
            execution_type="dataset_build",
            execution_started_at_utc=started_at,
            execution_completed_at_utc=completed_at,
            project="SMART FUTUROS",
            branch=str(git_state.get("branch") or "unresolved"),
            commit_sha=(
                str(git_state["commit_sha"]) if git_state.get("commit_sha") else None
            ),
            dirty_worktree=bool(git_state.get("dirty_worktree")),
            containerized=False,
            container_digest=None,
            runtime_environment={
                "platform": platform.platform(),
                "status": "local_research_environment",
            },
            python_version=platform.python_version(),
            dependency_lock_hash=dependency_hash,
            dataset_id="canonical_data_foundation_v2",
            dataset_hash=dataset_hash,
            dataset_manifest_hash=dataset_manifest_hash,
            feature_contract_hash=None,
            target_store_hash=None,
            split_hash=None,
            cost_model_hash=None,
            config_hash=config_hash,
            schema_hash=schema_hash,
            source_hashes=source_hashes,
            seed=0,
            command=command,
            arguments=arguments,
            row_count=int(lineage_report.get("total_rows") or 0),
            status="ok",
            blockers=(),
            warnings=("research_only_no_promotion_authority",),
            safety_flags=SAFETY_FLAGS,
        )

    first = make_manifest(execution_id, generated_at_utc, generated_at_utc)
    second = make_manifest(
        f"{execution_id}-repro",
        "2099-01-01T00:00:00+00:00",
        "2099-01-01T00:00:01+00:00",
    )
    return (
        {
            "status": "ok" if first.content_hash == second.content_hash else "blocked",
            "schema_version": EXECUTION_MANIFEST_SCHEMA_VERSION,
            "hash_reproducible": first.content_hash == second.content_hash,
            "content_hash": first.content_hash,
            "atomic_writer": "integrity_traceability_v2.atomic_writer",
            "content_addressed": True,
            "append_only": True,
            "previous_manifest_overwritten": False,
            "release_eligible": bool(
                first.canonical_payload.get("release_eligible", False)
            ),
            "release_blockers": first.canonical_payload.get("blockers", []),
            "container_status": first.canonical_payload.get("container", {}).get(
                "status"
            ),
        },
        first,
    )


def _source_inventory(
    root: Path,
    *,
    trader_master_path: str | Path,
    blocked_trades_path: str | Path,
    primary_candle_sources: Sequence[CandleSourceSpec],
    secondary_candle_sources: Sequence[CandleSourceSpec],
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for source_type, value in (
        ("trader_master", trader_master_path),
        ("blocked_trade_research_dataset", blocked_trades_path),
    ):
        path = _resolve(root, value)
        artifacts.append(_artifact_inventory(root, path, source_type))
    for source in (*primary_candle_sources, *secondary_candle_sources):
        for value in source.paths:
            artifacts.append(
                _artifact_inventory(
                    root,
                    _resolve(root, value),
                    source.source_type,
                    source_id=source.source_id,
                )
            )
    operational = root / "data/features/market_features_60d.parquet"
    operational_item = _artifact_inventory(
        root,
        operational,
        "operational_feature_materialization",
    )
    operational_item.update(
        accepted_as_candle_authority=False,
        rejection_reason="row_level_public_source_provenance_not_present",
    )
    artifacts.append(operational_item)
    return {
        "source_to_contract_map": [
            {
                "source": "immutable_trader_master",
                "contract": "trader_master_financial_lineage_v2",
                "reader": "read_trader_master_readonly",
                "writer": None,
                "dataset": "HistoricalResearchDataset",
                "manifest": "canonical_execution_manifest_v2",
            },
            {
                "source": "closed_reconciled_paper_trades",
                "contract": "paper_outcome_dataset_v2",
                "reader": "paper_outcome_reader_v2",
                "writer": "paper_outcome_writer_v2",
                "dataset": "PaperOutcomeDataset",
                "manifest": "canonical_execution_manifest_v2",
            },
            {
                "source": "validated_public_market_data",
                "contract": "operational_feature_dataset_v2",
                "reader": "operational_feature_reader_v2",
                "writer": "operational_feature_writer_v2",
                "dataset": "OperationalFeatureDataset",
                "manifest": "canonical_execution_manifest_v2",
            },
        ],
        "artifacts": artifacts,
    }


def _read_blocked_trades(
    root: Path,
    path_value: str | Path,
) -> tuple[pd.DataFrame, str | None]:
    path = _resolve(root, path_value)
    if not _safe_read_path(path, root):
        return pd.DataFrame(), "blocked_trade_dataset_missing_or_unsafe"
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError):
        return pd.DataFrame(), "blocked_trade_dataset_unreadable"
    if "is_research_eligible" not in frame.columns:
        return pd.DataFrame(), "blocked_trade_dataset_contract_missing"
    return frame[~frame["is_research_eligible"].fillna(False)].copy(), None


def _git_state(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip()

    commit = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    status = run("status", "--porcelain")
    return {
        "commit_sha": commit,
        "branch": branch,
        "dirty_worktree": status is None or bool(status),
        "status_resolved": status is not None,
    }


def _artifact_inventory(
    root: Path,
    path: Path,
    source_type: str,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    safe = _safe_read_path(path, root)
    return {
        "source_type": source_type,
        "source_id": source_id,
        "path": _display(path, root),
        "exists": safe,
        "sha256": hash_file(path) if safe else None,
        "size_bytes": path.stat().st_size if safe else None,
        "read_only_inventory": True,
    }


def _flatten_source_hashes(source_inventory: Mapping[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    artifacts = source_inventory.get("artifacts", [])
    if not isinstance(artifacts, list):
        return hashes
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping) or not artifact.get("sha256"):
            continue
        key = str(artifact.get("source_id") or artifact.get("source_type") or index)
        if key in hashes:
            key = f"{key}_{index}"
        hashes[key] = str(artifact["sha256"])
    return dict(sorted(hashes.items()))


def _safe_read_path(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        return resolved.is_file() and not path.is_symlink()
    except (FileNotFoundError, OSError, ValueError):
        return False


def _resolve(root: Path, value: str | Path) -> Path:
    requested = Path(value)
    return requested if requested.is_absolute() else root / requested


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()
