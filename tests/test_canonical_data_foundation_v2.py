from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.canonical_data_foundation_v2.candles import (
    CandleSourceSpec,
    PublicCandleRequestPolicy,
    fetch_public_candle_payload,
    recover_blocked_candles,
    sanitize_public_candle_url,
)
from smartcrypto.data.canonical_data_foundation_v2.contracts import (
    DATASET_CONTRACTS,
    SAFETY_FLAGS,
    DatasetBoundaryError,
    build_dataset_manifest,
    validate_dataset_write,
)
from smartcrypto.data.canonical_data_foundation_v2.lineage import (
    MANDATORY_FINANCIAL_FIELDS,
    build_trader_master_lineage,
)
from smartcrypto.data.canonical_data_foundation_v2.manifest import (
    ManifestValidationError,
    build_execution_manifest,
    sanitize_arguments,
    write_execution_manifest,
)
from smartcrypto.data.canonical_data_foundation_v2.pipeline import (
    build_canonical_data_foundation_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    MasterCanonicalRecord,
    MasterReadBundle,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_canonical_data_foundation_v2.py"
SHA = "a" * 64
COMMIT = "b" * 40


def source_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "venue": "binance",
        "market_type": "usdt_m_futures",
        "account_scope_hash": "c" * 64,
        "order_id_namespace": "fixture:paper:v1",
        "source_trade_id": "trade-1",
        "entry_order_id": "entry-1",
        "exit_order_id": "exit-1",
        "entry_price": "100",
        "exit_price": "110",
        "quantity": "1",
        "contract_size": "1",
        "gross_pnl": "10",
        "trading_fee": "1",
        "funding_fee": "1",
        "net_pnl": "8",
        "margin_mode": "isolated",
        "leverage": "2",
        "open_time": "2026-01-01T09:01:00+00:00",
        "close_time": "2026-01-01T09:11:00+00:00",
        "symbol": "BTCUSDT",
        "side": "long",
        "source_file": "fixture",
    }
    row.update(overrides)
    return row


def canonical_bundle(
    rows: list[dict[str, Any]],
    *,
    canonical_indices: set[int] | None = None,
    unverifiable_reasons: dict[int, list[str]] | None = None,
) -> MasterReadBundle:
    canonical_indices = canonical_indices or set()
    unverifiable_reasons = unverifiable_reasons or {}
    canonical: list[MasterCanonicalRecord] = []
    unverifiable: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index in canonical_indices:
            normalized = {
                "venue": row["venue"],
                "market_type": row["market_type"],
                "account_scope_hash": row["account_scope_hash"],
                "order_id_namespace": row["order_id_namespace"],
                "source_trade_id": row["source_trade_id"],
                "entry_price": row["entry_price"],
                "exit_price": row["exit_price"],
                "quantity": row["quantity"],
                "contract_size": row["contract_size"],
                "gross_pnl": row["gross_pnl"],
                "trading_fee": row["trading_fee"],
                "funding_fee": row["funding_fee"],
                "net_pnl": row["net_pnl"],
                "open_time": row["open_time"],
                "close_time": row["close_time"],
            }
            canonical.append(
                MasterCanonicalRecord(
                    row_index=index,
                    normalized=normalized,
                    canonical_json=json.dumps(normalized, sort_keys=True),
                    row_fingerprint="d" * 64,
                    primary_identity=None,
                    canonical_trade_id="canonical-1",
                    legacy_identity={},
                )
            )
        else:
            unverifiable.append(
                {
                    "row_index": index,
                    "reasons": unverifiable_reasons.get(
                        index, ["missing_required_identity_field:funding_fee"]
                    ),
                }
            )
    return MasterReadBundle(
        report={
            "status": "ok",
            "reason": "trader_master_readonly_copy_ok",
            "trader_master_path": "data/trades/trades_master.parquet",
            "trader_master_sha256_before": SHA,
            "trader_master_hash_preserved": True,
            "trader_master_row_count": len(rows),
        },
        canonical_records=tuple(canonical),
        unverifiable_rows=tuple(unverifiable),
        source_rows=tuple(rows),
    )


def bundle_reader(bundle: MasterReadBundle) -> Any:
    def reader(**_kwargs: Any) -> MasterReadBundle:
        return bundle

    return reader


def write_candles(
    root: Path,
    name: str,
    *,
    timestamps: list[str] | None = None,
    close_offset: float = 0.0,
    is_closed: bool | None = None,
) -> Path:
    timestamps = timestamps or [
        "2026-01-01T08:55:00+00:00",
        "2026-01-01T09:00:00+00:00",
        "2026-01-01T09:05:00+00:00",
    ]
    rows = []
    for index, timestamp in enumerate(timestamps):
        price = 100.0 + index
        row = {
            "timestamp": timestamp,
            "symbol": "BTCUSDT",
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price + close_offset,
            "volume": 10.0,
        }
        if is_closed is not None:
            row["is_closed"] = is_closed
        rows.append(row)
    path = root / "data" / "candles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def candle_source(
    path: Path,
    root: Path,
    *,
    source_id: str,
    source_type: str,
    priority: int = 1,
) -> CandleSourceSpec:
    return CandleSourceSpec(
        source_id=source_id,
        source_type=source_type,
        timeframe="5min",
        paths=(path.relative_to(root).as_posix(),),
        public_endpoint="https://fapi.binance.com/fapi/v1/klines",
        priority=priority,
    )


def blocked_trade(**overrides: Any) -> dict[str, Any]:
    row = {
        "trade_id": "trade-candle-1",
        "symbol": "BTCUSDT",
        "open_time": "2026-01-01T09:01:00+00:00",
        "close_time": "2026-01-01T09:11:00+00:00",
    }
    row.update(overrides)
    return row


def manifest(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "execution_id": "execution-1",
        "execution_type": "dataset_build",
        "execution_started_at_utc": "2026-01-01T00:00:00+00:00",
        "execution_completed_at_utc": "2026-01-01T00:00:01+00:00",
        "project": "SMART FUTUROS",
        "branch": "codex/test",
        "commit_sha": COMMIT,
        "dirty_worktree": False,
        "containerized": False,
        "container_digest": None,
        "runtime_environment": {"status": "test"},
        "python_version": "3.11.15",
        "dependency_lock_hash": SHA,
        "dataset_id": "fixture",
        "dataset_hash": SHA,
        "dataset_manifest_hash": SHA,
        "feature_contract_hash": SHA,
        "target_store_hash": SHA,
        "split_hash": SHA,
        "cost_model_hash": SHA,
        "config_hash": SHA,
        "schema_hash": SHA,
        "source_hashes": {"fixture": SHA},
        "seed": 7,
        "command": "fixture",
        "arguments": ["--fixture"],
        "row_count": 1,
        "status": "ok",
        "blockers": (),
        "warnings": (),
        "safety_flags": SAFETY_FLAGS,
    }
    values.update(overrides)
    return build_execution_manifest(**values)


def test_all_lineage_rows_end_terminal_and_are_deterministic(tmp_path: Path) -> None:
    rows = [source_row(), source_row(source_trade_id=None, funding_fee=None)]
    bundle = canonical_bundle(rows, canonical_indices={0})
    first = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(bundle),
        secondary_evidence_paths=(),
    )
    second = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(bundle),
        secondary_evidence_paths=(),
    )
    assert [record.verification_status for record in first.records] == [
        "VERIFIED",
        "PERMANENT_QUARANTINE",
    ]
    assert first.report["unresolved_rows"] == 0
    assert first.report["record_set_hash"] == second.report["record_set_hash"]


def test_missing_ids_fees_and_funding_are_never_invented(tmp_path: Path) -> None:
    row = source_row(
        source_trade_id=None,
        entry_order_id=None,
        exit_order_id=None,
        trading_fee=None,
        funding_fee=None,
    )
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(canonical_bundle([row])),
        secondary_evidence_paths=(),
    )
    fields = result.records[0].fields
    for name in ("trade_id", "entry_order_id", "exit_order_id", "trading_fee", "funding_fee"):
        assert fields[name].value is None
        assert fields[name].verification_status == "PERMANENT_QUARANTINE"
    assert result.report["fabricated_identity_count"] == 0
    assert result.report["fabricated_financial_component_count"] == 0


def test_financial_reconciliation_divergence_blocks(tmp_path: Path) -> None:
    row = source_row(net_pnl="9")
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(canonical_bundle([row], canonical_indices={0})),
        secondary_evidence_paths=(),
    )
    record = result.records[0]
    assert record.verification_status == "PERMANENT_QUARANTINE"
    assert "financial_reconciliation_failed" in record.terminal_reason_codes


def test_namespace_and_duplicate_identity_are_blocked(tmp_path: Path) -> None:
    rows = [
        source_row(order_id="order-1"),
        source_row(source_trade_id="trade-2", venue="other", order_id="order-1"),
    ]
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(
            canonical_bundle(rows, canonical_indices={0, 1})
        ),
        secondary_evidence_paths=(),
    )
    assert all(record.verification_status == "PERMANENT_QUARANTINE" for record in result.records)
    assert result.report["source_conflict_rows"] == 2


def test_original_lineage_rows_remain_immutable(tmp_path: Path) -> None:
    row = source_row(funding_fee=None)
    original = dict(row)
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(canonical_bundle([row])),
        secondary_evidence_paths=(),
    )
    assert row == original
    assert result.records[0].original_row_immutable is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"open_time": "2026-01-01T09:01:00"}, "open_time_timezone_missing_or_ambiguous"),
        ({"quantity": "0"}, "quantity_incompatible"),
        ({"leverage": "-1"}, "leverage_incompatible"),
        ({"side": "buy"}, "side_inconsistent"),
        ({"symbol": "?"}, "symbol_incompatible"),
        ({"quantity": "1", "volume_fechado": "2"}, "quantity_source_conflict"),
        (
            {"trading_fee": "1", "fee_total": "2"},
            "trading_fee_source_conflict",
        ),
        (
            {"funding_fee": "1", "funding_fees": "2"},
            "funding_fee_source_conflict",
        ),
    ],
)
def test_lineage_context_and_financial_source_conflicts_block(
    tmp_path: Path,
    overrides: dict[str, Any],
    reason: str,
) -> None:
    row = source_row(**overrides)
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(canonical_bundle([row], canonical_indices={0})),
        secondary_evidence_paths=(),
    )
    assert result.records[0].verification_status == "PERMANENT_QUARANTINE"
    assert reason in result.records[0].terminal_reason_codes


def test_field_contract_contains_every_required_financial_field(tmp_path: Path) -> None:
    result = build_trader_master_lineage(
        project_root=tmp_path,
        bundle_reader=bundle_reader(canonical_bundle([source_row()])),
        secondary_evidence_paths=(),
    )
    assert set(result.records[0].fields) == set(MANDATORY_FINANCIAL_FIELDS)
    for evidence in result.records[0].fields.values():
        assert {
            "value",
            "source_type",
            "source_reference",
            "source_hash",
            "confidence_class",
            "verification_status",
            "reason_code",
        } == set(evidence.to_dict())


def test_primary_candle_source_valid_is_recovered(tmp_path: Path) -> None:
    primary_path = write_candles(tmp_path, "primary.parquet")
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(
                primary_path, tmp_path, source_id="primary", source_type="primary"
            )
        ],
        secondary_sources=[],
    )
    record = result.records[0]
    assert record.terminal_status == "RECOVERED_VERIFIED"
    assert record.entry_feature_timestamp_utc == "2026-01-01T08:55:00+00:00"
    assert record.point_in_time_valid is True


def test_secondary_fallback_only_after_structured_primary_failure(tmp_path: Path) -> None:
    secondary_path = write_candles(tmp_path, "secondary.parquet")
    missing = tmp_path / "data/candles/missing.parquet"
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(missing, tmp_path, source_id="primary", source_type="primary")
        ],
        secondary_sources=[
            candle_source(
                secondary_path, tmp_path, source_id="secondary", source_type="secondary"
            )
        ],
    )
    record = result.records[0]
    assert record.terminal_status == "RECOVERED_VERIFIED"
    assert record.selected_source_id == "secondary"
    assert record.source_attempts[0]["status"] == "blocked"


def test_primary_secondary_divergence_is_terminally_blocked(tmp_path: Path) -> None:
    primary_path = write_candles(tmp_path, "primary.parquet")
    secondary_path = write_candles(
        tmp_path, "secondary.parquet", close_offset=0.5
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(
                primary_path, tmp_path, source_id="primary", source_type="primary"
            )
        ],
        secondary_sources=[
            candle_source(
                secondary_path, tmp_path, source_id="secondary", source_type="secondary"
            )
        ],
    )
    assert result.records[0].terminal_status == "PERMANENT_QUARANTINE"
    assert (
        "primary_secondary_source_divergence"
        in result.records[0].terminal_reason_codes
    )


def test_gap_is_preserved_without_forward_fill(tmp_path: Path) -> None:
    primary_path = write_candles(
        tmp_path,
        "primary.parquet",
        timestamps=[
            "2026-01-01T08:55:00+00:00",
            "2026-01-01T09:05:00+00:00",
        ],
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(
                primary_path, tmp_path, source_id="primary", source_type="primary"
            )
        ],
        secondary_sources=[],
    )
    record = result.records[0]
    assert record.terminal_status == "PERMANENT_QUARANTINE"
    assert record.gap_detected is True
    assert record.missing_interval_count == 1
    assert record.forward_fill_used is False


@pytest.mark.parametrize(
    ("timestamps", "is_closed", "reason"),
    [
        (
            [
                "2026-01-01 08:55:00",
                "2026-01-01 09:00:00",
                "2026-01-01 09:05:00",
            ],
            None,
            "source_structural_validation_failed",
        ),
        (
            [
                "2026-01-01T08:55:00+00:00",
                "2026-01-01T09:00:00+00:00",
                "2026-01-01T09:05:00+00:00",
            ],
            False,
            "source_structural_validation_failed",
        ),
    ],
)
def test_ambiguous_timezone_or_incomplete_candle_is_blocked(
    tmp_path: Path,
    timestamps: list[str],
    is_closed: bool | None,
    reason: str,
) -> None:
    path = write_candles(
        tmp_path,
        "source.parquet",
        timestamps=timestamps,
        is_closed=is_closed,
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(path, tmp_path, source_id="primary", source_type="primary")
        ],
        secondary_sources=[],
    )
    assert result.records[0].terminal_status == "PERMANENT_QUARANTINE"
    assert result.records[0].source_attempts[0]["reason"] == reason


def test_future_candle_cannot_be_entry_feature(tmp_path: Path) -> None:
    path = write_candles(
        tmp_path,
        "source.parquet",
        timestamps=[
            "2026-01-01T09:05:00+00:00",
            "2026-01-01T09:10:00+00:00",
        ],
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade(open_time="2026-01-01T09:01:00+00:00")],
        primary_sources=[
            candle_source(path, tmp_path, source_id="primary", source_type="primary")
        ],
        secondary_sources=[],
    )
    assert result.records[0].terminal_status == "PERMANENT_QUARANTINE"
    assert (
        result.records[0].source_attempts[0]["reason"]
        == "entry_candle_not_available_point_in_time"
    )


def test_stale_archive_cannot_be_mistaken_for_trade_coverage(tmp_path: Path) -> None:
    path = write_candles(
        tmp_path,
        "source.parquet",
        timestamps=[
            "2025-12-01T08:55:00+00:00",
            "2025-12-01T09:00:00+00:00",
            "2025-12-01T09:05:00+00:00",
        ],
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[
            candle_source(path, tmp_path, source_id="primary", source_type="primary")
        ],
        secondary_sources=[],
    )
    assert result.records[0].terminal_status == "PERMANENT_QUARANTINE"
    assert (
        result.records[0].source_attempts[0]["reason"]
        == "entry_candle_not_available_point_in_time"
    )


def test_candle_recovery_hash_is_stable_and_idempotent(tmp_path: Path) -> None:
    path = write_candles(tmp_path, "source.parquet")
    source = candle_source(path, tmp_path, source_id="primary", source_type="primary")
    first = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[source],
        secondary_sources=[],
    )
    second = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[source],
        secondary_sources=[],
    )
    assert first.report["record_set_hash"] == second.report["record_set_hash"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first.records[0].selected_source_hashes[0]


def test_public_fetch_policy_is_bounded_sanitized_and_offline() -> None:
    calls: list[tuple[str, float, Mapping[str, str]]] = []

    def transport(
        url: str, timeout: float, headers: Mapping[str, str]
    ) -> bytes:
        calls.append((url, timeout, headers))
        return b"fixture"

    result = fetch_public_candle_payload(
        url=(
            "https://fapi.binance.com/fapi/v1/klines?"
            "symbol=BTCUSDT&interval=1m&api_key=must_not_appear"
        ),
        transport=transport,
        policy=PublicCandleRequestPolicy(
            timeout_seconds=1,
            max_attempts=2,
            backoff_seconds=0,
            minimum_request_interval_seconds=0,
        ),
    )
    assert result["status"] == "ok"
    assert "api_key" not in result["source_url_sanitized"]
    assert calls[0][1] == 1
    assert calls[0][2]["User-Agent"].startswith("SMART-FUTUROS")


def test_non_public_candle_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="public_candle_url_not_allowlisted"):
        sanitize_public_candle_url("https://example.com/private/candles")


def test_candle_source_outside_project_or_symlink_is_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-candles.parquet"
    pd.DataFrame(
        [
            {
                "timestamp": "2026-01-01T08:55:00+00:00",
                "symbol": "BTCUSDT",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
    ).to_parquet(outside, index=False)
    source = CandleSourceSpec(
        source_id="outside",
        source_type="primary",
        timeframe="5min",
        paths=(str(outside),),
        public_endpoint="https://fapi.binance.com/fapi/v1/klines",
        priority=1,
    )
    result = recover_blocked_candles(
        project_root=tmp_path,
        blocked_trades=[blocked_trade()],
        primary_sources=[source],
        secondary_sources=[],
    )
    assert result.records[0].terminal_status == "PERMANENT_QUARANTINE"
    outside.unlink()


def test_dataset_contracts_have_independent_authorities_writers_readers_and_paths() -> None:
    contracts = list(DATASET_CONTRACTS.values())
    assert len({item.authority for item in contracts}) == 3
    assert len({item.writer_id for item in contracts}) == 3
    assert len({item.reader_id for item in contracts}) == 3
    assert len({item.root_path for item in contracts}) == 3


@pytest.mark.parametrize(
    "column",
    ["target_profitable", "future_ret_5m", "net_pnl", "exit_price", "label_win"],
)
def test_operational_feature_dataset_rejects_leakage(column: str) -> None:
    contract = DATASET_CONTRACTS["OperationalFeatureDataset"]
    with pytest.raises(DatasetBoundaryError):
        validate_dataset_write(
            contract=contract,
            writer_id=contract.writer_id,
            target_path=f"{contract.root_path}/x.json",
            columns=["feature_rsi", column],
        )


def test_paper_outcome_rejects_open_or_unreconciled_trade() -> None:
    contract = DATASET_CONTRACTS["PaperOutcomeDataset"]
    with pytest.raises(DatasetBoundaryError, match="open_trade"):
        validate_dataset_write(
            contract=contract,
            writer_id=contract.writer_id,
            target_path=f"{contract.root_path}/x.json",
            columns=["is_closed", "reconciliation_status"],
            rows=[{"is_closed": False, "reconciliation_status": "VERIFIED"}],
        )
    with pytest.raises(DatasetBoundaryError, match="verified_reconciliation"):
        validate_dataset_write(
            contract=contract,
            writer_id=contract.writer_id,
            target_path=f"{contract.root_path}/x.json",
            columns=["is_closed", "reconciliation_status"],
            rows=[{"is_closed": True, "reconciliation_status": "PENDING"}],
        )


def test_cross_writer_path_authority_and_active_signal_are_blocked() -> None:
    historical = DATASET_CONTRACTS["HistoricalResearchDataset"]
    paper = DATASET_CONTRACTS["PaperOutcomeDataset"]
    with pytest.raises(DatasetBoundaryError, match="writer"):
        validate_dataset_write(
            contract=historical,
            writer_id=paper.writer_id,
            target_path=f"{historical.root_path}/x.json",
            columns=["x"],
        )
    with pytest.raises(DatasetBoundaryError, match="outside"):
        validate_dataset_write(
            contract=historical,
            writer_id=historical.writer_id,
            target_path=f"{paper.root_path}/x.json",
            columns=["x"],
        )
    with pytest.raises(DatasetBoundaryError, match="active_signal"):
        validate_dataset_write(
            contract=historical,
            writer_id=historical.writer_id,
            target_path=f"{historical.root_path}/active_signals.json",
            columns=["x"],
            publishes_active_signal=True,
        )


def test_dataset_manifest_hash_is_deterministic_and_content_sensitive() -> None:
    contract = DATASET_CONTRACTS["HistoricalResearchDataset"]
    first = build_dataset_manifest(
        contract=contract,
        columns=["b", "a"],
        row_count=1,
        source_manifest={"hash": SHA},
        git_commit_sha=COMMIT,
        created_at_utc="2026-01-01T00:00:00+00:00",
    )
    second = build_dataset_manifest(
        contract=contract,
        columns=["a", "b"],
        row_count=1,
        source_manifest={"hash": SHA},
        git_commit_sha=COMMIT,
        created_at_utc="2099-01-01T00:00:00+00:00",
    )
    changed = build_dataset_manifest(
        contract=contract,
        columns=["a", "b"],
        row_count=2,
        source_manifest={"hash": SHA},
        git_commit_sha=COMMIT,
        created_at_utc="2026-01-01T00:00:00+00:00",
    )
    assert first["immutable_content_hash"] == second["immutable_content_hash"]
    assert first["immutable_content_hash"] != changed["immutable_content_hash"]


def test_execution_manifest_hash_excludes_volatile_envelope() -> None:
    first = manifest()
    second = manifest(
        execution_id="execution-2",
        execution_started_at_utc="2099-01-01T00:00:00+00:00",
        execution_completed_at_utc="2099-01-01T00:00:01+00:00",
    )
    assert first.content_hash == second.content_hash


@pytest.mark.parametrize(
    "changed",
    [
        {"dataset_hash": "c" * 64},
        {"feature_contract_hash": "c" * 64},
        {"target_store_hash": "c" * 64},
        {"split_hash": "c" * 64},
        {"cost_model_hash": "c" * 64},
    ],
)
def test_execution_manifest_hash_changes_for_material_inputs(
    changed: dict[str, Any],
) -> None:
    assert manifest().content_hash != manifest(**changed).content_hash


def test_dirty_worktree_and_missing_commit_block_release() -> None:
    dirty = manifest(dirty_worktree=True)
    missing = manifest(commit_sha=None)
    assert dirty.canonical_payload["release_eligible"] is False
    assert "dirty_worktree_blocks_release" in dirty.canonical_payload["blockers"]
    assert missing.canonical_payload["release_eligible"] is False
    assert "commit_sha_unresolved" in missing.canonical_payload["blockers"]


def test_container_digest_is_never_fabricated() -> None:
    local = manifest()
    assert local.canonical_payload["container"] == {
        "status": "not_containerized",
        "digest": None,
    }
    with pytest.raises(
        ManifestValidationError,
        match="local_execution_cannot_claim_container_digest",
    ):
        manifest(
            containerized=False,
            container_digest=f"sha256:{'c' * 64}",
        )


def test_containerized_manifest_without_digest_is_blocked_not_fabricated() -> None:
    value = manifest(containerized=True, container_digest=None)
    assert value.canonical_payload["container"]["digest"] is None
    assert "container_digest_required" in value.canonical_payload["blockers"]
    assert value.canonical_payload["release_eligible"] is False


def test_manifest_rejects_nan() -> None:
    with pytest.raises(ValueError, match="non_finite"):
        manifest(runtime_environment={"metric": float("nan")})


def test_manifest_uses_b01_atomic_writer_and_never_overwrites(tmp_path: Path) -> None:
    value = manifest()
    first = write_execution_manifest(
        manifest=value,
        output_root="data/reports/manifests",
        project_root=tmp_path,
    )
    assert first["write_performed"] is True
    assert first["atomic_writer"] == "integrity_traceability_v2.atomic_writer"
    with pytest.raises(ManifestValidationError, match="already_exists"):
        write_execution_manifest(
            manifest=value,
            output_root="data/reports/manifests",
            project_root=tmp_path,
        )


def test_manifest_path_traversal_and_sensitive_arguments_are_blocked_or_redacted(
    tmp_path: Path,
) -> None:
    with pytest.raises(ManifestValidationError, match="outside_research_reports"):
        write_execution_manifest(
            manifest=manifest(),
            output_root="../outside",
            project_root=tmp_path,
        )
    assert sanitize_arguments(
        ["--token", "synthetic-secret", "--api-key=synthetic-key", "--safe", "value"]
    ) == [
        "--token",
        "[REDACTED]",
        "--api-key=[REDACTED]",
        "--safe",
        "value",
    ]


def test_pipeline_default_no_write_and_terminal_gate(tmp_path: Path) -> None:
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "moeda": "BTCUSDT",
                "fechar_side": "long",
                "pnl_fechado": "1",
                "source_file": "fixture",
            }
        ]
    ).to_parquet(master, index=False)
    research = tmp_path / "data/research/ocr_v11_trade_research_dataset.parquet"
    research.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "trade_id": "fixture",
                "symbol": "BTCUSDT",
                "open_time": None,
                "close_time": None,
                "is_research_eligible": False,
            }
        ]
    ).to_parquet(research, index=False)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    report = build_canonical_data_foundation_report(
        project_root=tmp_path,
        generated_at_utc="2026-01-01T00:00:00+00:00",
        execution_id="fixture-execution",
    )
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert report["gate_b02"] == "PASS"
    assert report["trader_master_lineage"]["unresolved_rows"] == 0
    assert report["candle_recovery"]["candle_unresolved_rows"] == 0
    assert report["write_performed"] is False


def test_write_report_is_restricted_to_data_reports(tmp_path: Path) -> None:
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True)
    pd.DataFrame([{"moeda": "BTCUSDT"}]).to_parquet(master, index=False)
    research = tmp_path / "data/research/ocr_v11_trade_research_dataset.parquet"
    research.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "trade_id": "fixture",
                "symbol": "BTCUSDT",
                "open_time": None,
                "close_time": None,
                "is_research_eligible": False,
            }
        ]
    ).to_parquet(research, index=False)
    report = build_canonical_data_foundation_report(
        project_root=tmp_path,
        write_report=True,
        generated_at_utc="2026-01-01T00:00:00+00:00",
        execution_id="fixture-write",
    )
    assert report["write_performed"] is True
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    new_outputs = [
        path
        for path in files
        if path not in {master, research}
    ]
    assert new_outputs
    assert all(
        "data/reports" in path.relative_to(tmp_path).as_posix()
        for path in new_outputs
    )


def test_cli_json_executes_without_runtime_write(tmp_path: Path) -> None:
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True)
    pd.DataFrame([{"moeda": "BTCUSDT"}]).to_parquet(master, index=False)
    research = tmp_path / "data/research/ocr_v11_trade_research_dataset.parquet"
    research.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "trade_id": "fixture",
                "symbol": "BTCUSDT",
                "open_time": None,
                "close_time": None,
                "is_research_eligible": False,
            }
        ]
    ).to_parquet(research, index=False)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert not (tmp_path / "data/reports").exists()


def test_safety_flags_and_static_boundaries() -> None:
    assert SAFETY_FLAGS == {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "automatic_promotion_allowed": False,
        "operational_authority": False,
    }
    package = ROOT / "smartcrypto/data/canonical_data_foundation_v2"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    lowered = source.lower()
    assert "shell=true" not in lowered
    assert "ccxt" not in lowered
    assert "submit_order" not in lowered
    assert "create_order" not in lowered
    assert "riskmanager" not in lowered
    assert re.search(r"""["'](?:[^"']*/)?\.env["']""", lowered) is None


def test_project_data_is_not_touched_by_focused_tests() -> None:
    assert os.environ.get("ORDER_SUBMISSION_ENABLED", "").lower() not in {"1", "true"}
