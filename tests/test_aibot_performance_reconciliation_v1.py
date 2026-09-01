from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from smartcrypto.research.aibot_parity import (
    SOURCE_INVESTMENT_ID,
    build_aibot_benchmark,
    build_performance_reconciliation,
)


def _write_master(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "order_id": ["1", "2"],
            "moeda": ["BTCUSDT", "ETHUSDT"],
            "fechar_side": ["long", "short"],
            "horario_abertura": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
            "horario_fechamento": ["2026-01-01T01:00:00Z", "2026-01-02T01:00:00Z"],
            "pnl_fechado": [4.0, -1.0],
        }
    ).to_parquet(path, index=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_cashflow_blocks_only_account_level_reconstruction() -> None:
    report = build_performance_reconciliation(
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_batch_id="batch",
        behavior_fingerprint={"global": {"trade_count": 2, "net_pnl": 3.0}},
    )

    assert report["status"] == "ok"
    assert report["trade_level_performance_status"] == "AVAILABLE"
    assert report["account_level_reconciliation_status"] == (
        "INSUFFICIENT_ACCOUNT_CASHFLOW_DATA"
    )
    assert report["account_level_return_pct"] is None
    assert report["account_level_return_claimed"] is False


def test_benchmark_is_no_write_by_default_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "data" / "trades" / "trades_master.parquet"
    before = _write_master(source)

    report = build_aibot_benchmark(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["output_paths"] == {}
    assert report["benchmark_snapshot_status"] == "CURRENT_SNAPSHOT_NOT_FINAL"
    assert report["financial_closeout_status"] == "PENDING_TRADER_MASTER_REFRESH"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "data" / "reports" / "aibot_parity").exists()
    assert not (tmp_path / "data" / "research" / "aibot_parity").exists()


def test_explicit_write_is_restricted_to_research_and_reports(tmp_path: Path) -> None:
    source = tmp_path / "data" / "trades" / "trades_master.parquet"
    before = _write_master(source)

    report = build_aibot_benchmark(project_root=tmp_path, write_reports=True)

    assert report["status"] == "ok"
    assert report["write_performed"] is True
    assert set(report["output_paths"]) == {
        "source_registry",
        "trader_master_audit",
        "behavior_fingerprint",
        "rolling_behavior",
        "performance_reconciliation",
        "benchmark_summary",
    }
    assert all(
        path.startswith(("data/research/aibot_parity/", "data/reports/aibot_parity/"))
        for path in report["output_paths"].values()
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert report["safety_flags"]["sends_orders"] is False
    assert report["safety_flags"]["exchange_private_access"] is False
    assert report["safety_flags"]["changes_model"] is False
    assert report["safety_flags"]["changes_risk"] is False
