from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "scripts/audit_bitradex_dependency_boundary.py",
    "scripts/collect_phase5_summary.py",
    "scripts/import_trades_incremental.py",
    "scripts/inspect_phase22_outputs.py",
    "scripts/inspect_phase5_outputs.py",
    "scripts/large_trades_import_quality_gate.py",
    "scripts/phase17_preflight.py",
    "scripts/phase5_preflight.py",
    "scripts/rebuild_phase5_datasets.py",
    "scripts/run_bitradex_ocr_v11_single_command_ingestion.py",
    "scripts/run_paper_feedback_master_consolidation_v1.py",
    "scripts/sync_ocr_master_v11_phase5_sidecars.py",
    "smartcrypto/ops/paper_session.py",
    "smartcrypto/research/historical_validation_15s.py",
    "smartcrypto/research/ocr_v11_dataset.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_unverifiable_master(path: Path, *, row_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "moeda": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
            "pnl_fechado": float(index + 1),
        }
        for index in range(row_count)
    ]
    pd.DataFrame(records).to_parquet(path, index=False)


def test_all_target_consumers_reference_institutional_readonly_adapter() -> None:
    for relative_path in TARGETS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "read_trader_master_readonly" in source
        assert "data/trades/" + "trades_master.parquet" not in source.replace("\\", "/")
        assert "read_master(" not in source


def test_real_legacy_master_read_preserves_hash_and_uses_temp_copy(
    tmp_path: Path,
) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.parquet"
    write_unverifiable_master(master, row_count=2)
    before = sha256(master)

    bundle = read_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=master,
    )

    assert sha256(master) == before
    assert bundle.report["status"] == "ok"
    assert bundle.report["trader_master_temp_copy_used"] is True
    assert bundle.report["trader_master_hash_preserved"] is True
    assert bundle.report["trader_master_sha256_before"] == before
    assert bundle.report["trader_master_sha256_after"] == before
    assert bundle.report["operational_authority"] is False
    assert bundle.report["write_performed"] is False
    assert bundle.report["writes_trader_master"] is False


def test_unverifiable_rows_remain_segregated_from_canonical_records(
    tmp_path: Path,
) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.parquet"
    write_unverifiable_master(master, row_count=3)
    before = sha256(master)

    bundle = read_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=master,
    )

    assert len(bundle.source_rows) == 3
    assert len(bundle.canonical_records) == 0
    assert len(bundle.unverifiable_rows) == 3
    assert bundle.report["master_valid_fingerprint_row_count"] == 0
    assert bundle.report["master_unverifiable_row_count"] == 3
    assert bundle.report["trader_master_hash_preserved"] is True
    assert sha256(master) == before


def test_missing_legacy_master_blocks_without_creating_fallback(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.parquet"
    bundle = read_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=missing,
    )

    assert bundle.report["status"] == "blocked"
    assert bundle.report["reason"] == "trader_master_missing"
    assert bundle.report["write_performed"] is False
    assert bundle.report["operational_authority"] is False
    assert bundle.report["writes_trader_master"] is False
    assert list(tmp_path.iterdir()) == []


def test_adapter_preserves_invalid_source_rows_without_promoting_them(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.parquet"
    pd.DataFrame([{"moeda": "BTCUSDT", "pnl_fechado": 1.0}]).to_parquet(
        source,
        index=False,
    )
    before = sha256(source)

    bundle = read_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=source,
    )

    assert bundle.report["status"] == "ok"
    assert len(bundle.source_rows) == 1
    assert len(bundle.unverifiable_rows) == 1
    assert bundle.canonical_records == ()
    assert bundle.report["write_performed"] is False
    assert bundle.report["writes_trader_master"] is False
    assert sha256(source) == before
