from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_MASTER = ROOT / "data" / "trades" / "trades_master.parquet"
PROTECTED_SHA256 = "24e049b3ca7a72dbde071a056548035fed87651d48959cd0cf4c6c8b0dac7295"
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


def test_all_target_consumers_reference_institutional_readonly_adapter() -> None:
    for relative_path in TARGETS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "read_trader_master_readonly" in source
        assert "data/trades/" + "trades_master.parquet" not in source.replace("\\", "/")
        assert "read_master(" not in source


def test_real_legacy_master_read_preserves_hash_and_uses_temp_copy() -> None:
    before = sha256(PROTECTED_MASTER)
    bundle = read_trader_master_readonly(
        project_root=ROOT,
        trader_master_path=PROTECTED_MASTER,
    )

    assert before == PROTECTED_SHA256
    assert sha256(PROTECTED_MASTER) == before
    assert bundle.report["status"] == "ok"
    assert bundle.report["trader_master_temp_copy_used"] is True
    assert bundle.report["trader_master_hash_preserved"] is True
    assert bundle.report["operational_authority"] is False
    assert bundle.report["write_performed"] is False


def test_unverifiable_rows_remain_segregated_from_canonical_records() -> None:
    bundle = read_trader_master_readonly(
        project_root=ROOT,
        trader_master_path=PROTECTED_MASTER,
    )

    assert len(bundle.source_rows) == 3058
    assert len(bundle.canonical_records) == 0
    assert len(bundle.unverifiable_rows) == 3058
    assert bundle.report["master_unverifiable_row_count"] == 3058


def test_missing_legacy_master_blocks_without_creating_fallback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.parquet"
    bundle = read_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=missing,
    )

    assert bundle.report["status"] == "blocked"
    assert bundle.report["write_performed"] is False
    assert bundle.report["operational_authority"] is False
    assert list(tmp_path.iterdir()) == []


def test_adapter_preserves_invalid_source_rows_without_promoting_them(tmp_path: Path) -> None:
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
    assert sha256(source) == before
