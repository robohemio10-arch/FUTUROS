from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from scripts.build_trade_enriched import (
    add_feature_snapshots,
    build_institutional_trade_ids,
    coerce_float,
    normalize_symbol,
    normalize_trades,
)
from scripts.rebuild_phase5_datasets import validate_phase5_source_alignment
from scripts.sync_ocr_master_v11_phase5_sidecars import (
    PHASE5_COLUMNS,
    build_phase5_compatibility_frame,
    sync_ocr_master_v11_phase5_sidecars,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 22, 12, 30, tzinfo=timezone.utc)


def ocr_master(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "11_moeda": ["BTCUSDT", "ETHUSDT", "BTCUSDT"][:rows],
            "12_fechar_long_short": ["Fechar Long", "Fechar Short", "Fechar Long"][:rows],
            "10_numero_do_pedido": ["order-1", pd.NA, "order-3"][:rows],
            "1_pnl_fechado": ["+1.25 USDT", "-0.50 USDT", "+2.00 USDT"][:rows],
            "2_taxa_lucros_perdas_fechados": ["1.2%", "-0.5%", "2.0%"][:rows],
            "3_preco_de_abertura": ["100", "200", "300"][:rows],
            "4_preco_de_fechamento": ["101", "199", "306"][:rows],
            "5_volume_de_posicao": ["0.1", "0.2", "0.3"][:rows],
            "6_volume_fechado": ["0.1", "0.2", "0.3"][:rows],
            "7_horario_de_abertura": [
                "2026-01-01 10:00:00",
                "2026-01-01 11:00:00",
                "2026-01-01 12:00:00",
            ][:rows],
            "8_horario_de_fechamento": [
                "2026-01-01 10:05:00",
                "2026-01-01 11:05:00",
                "2026-01-01 12:05:00",
            ][:rows],
            "9_taxa": ["0.01", "0.02", "0.03"][:rows],
            "fingerprint_operacional": ["fp-1", "fp-2", "fp-3"][:rows],
            "candidate_source": ["candidate-v1.1"] * rows,
            "candidate_generated_at_utc": ["2026-06-20T12:00:00Z"] * rows,
        }
    )


def prepare_project(tmp_path: Path, rows: int = 3) -> tuple[Path, str, bytes]:
    trades = tmp_path / "data" / "trades"
    trades.mkdir(parents=True)
    master_path = trades / "trades_master.xlsx"
    ocr_master(rows).to_excel(master_path, index=False)
    master_bytes = master_path.read_bytes()
    expected_hash = hashlib.sha256(master_bytes).hexdigest()
    stale = pd.DataFrame({column: ["stale"] * (rows + 1) for column in PHASE5_COLUMNS})
    stale.to_excel(trades / "trades_excel.xlsx", index=False)
    stale.to_parquet(trades / "trades_master.parquet", index=False)
    return master_path, expected_hash, master_bytes


def test_conversion_maps_ocr_master_to_exact_phase5_schema() -> None:
    converted = build_phase5_compatibility_frame(
        ocr_master(),
        "2026-06-22T12:30:00Z",
    )

    assert list(converted.columns) == list(PHASE5_COLUMNS)
    assert len(converted) == 3
    assert converted["order_id"].iloc[0] == "order-1"
    assert pd.isna(converted["order_id"].iloc[1])
    assert converted["_dedup_key"].tolist() == ["fp-1", "fp-2", "fp-3"]
    assert converted["horario_transacao"].equals(converted["horario_fechamento"])
    assert set(converted["exchange_source"]) == {"bitradex"}
    assert set(converted["market_data_source"]) == {"binance"}


def test_no_write_validates_without_sidecar_or_backup_changes(tmp_path: Path) -> None:
    master_path, expected_hash, master_bytes = prepare_project(tmp_path)
    compatibility = tmp_path / "data/trades/trades_excel.xlsx"
    parquet = tmp_path / "data/trades/trades_master.parquet"
    compatibility_before = compatibility.read_bytes()
    parquet_before = parquet.read_bytes()

    report = sync_ocr_master_v11_phase5_sidecars(
        tmp_path,
        expected_hash,
        3,
        no_write=True,
        now_utc=NOW,
    )

    assert report["status"] == "ok"
    assert report["reason"] == "dry_run_validation_ok"
    assert report["would_write"] is True
    assert report["write_performed"] is False
    assert report["backup_created"] is False
    assert master_path.read_bytes() == master_bytes
    assert compatibility.read_bytes() == compatibility_before
    assert parquet.read_bytes() == parquet_before
    assert not (tmp_path / "data/backups").exists()


def test_write_creates_backup_and_aligned_sidecars_without_changing_master(
    tmp_path: Path,
) -> None:
    master_path, expected_hash, master_bytes = prepare_project(tmp_path)

    report = sync_ocr_master_v11_phase5_sidecars(
        tmp_path,
        expected_hash,
        3,
        no_write=False,
        now_utc=NOW,
    )

    assert report["status"] == "ok"
    assert report["backup_created"] is True
    assert report["write_performed"] is True
    assert report["validation_errors"] == []
    assert len(report["backup_files"]) == 2
    assert master_path.read_bytes() == master_bytes
    compatibility = pd.read_excel(tmp_path / "data/trades/trades_excel.xlsx")
    parquet = pd.read_parquet(tmp_path / "data/trades/trades_master.parquet")
    assert len(compatibility) == len(parquet) == 3
    assert list(compatibility.columns) == list(parquet.columns) == list(PHASE5_COLUMNS)
    assert compatibility["_dedup_key"].nunique() == 3


def test_hash_or_row_mismatch_blocks_without_backup(tmp_path: Path) -> None:
    prepare_project(tmp_path)

    report = sync_ocr_master_v11_phase5_sidecars(
        tmp_path,
        "0" * 64,
        99,
        no_write=False,
        now_utc=NOW,
    )

    assert report["status"] == "blocked"
    assert "master_sha256_mismatch" in report["validation_errors"]
    assert "master_rows_mismatch:3!=99" in report["validation_errors"]
    assert report["backup_created"] is False


def test_phase5_gate_blocks_sidecar_row_divergence(tmp_path: Path) -> None:
    master_path, _, _ = prepare_project(tmp_path)
    result = validate_phase5_source_alignment(
        master_path,
        tmp_path / "data/trades/trades_excel.xlsx",
        tmp_path / "data/trades/trades_master.parquet",
    )

    assert result["status"] == "blocked"
    assert any("rows_mismatch" in error for error in result["validation_errors"])


def test_phase5_gate_accepts_aligned_sidecars(tmp_path: Path) -> None:
    master_path, expected_hash, _ = prepare_project(tmp_path)
    sync_ocr_master_v11_phase5_sidecars(
        tmp_path,
        expected_hash,
        3,
        no_write=False,
        now_utc=NOW,
    )

    result = validate_phase5_source_alignment(
        master_path,
        tmp_path / "data/trades/trades_excel.xlsx",
        tmp_path / "data/trades/trades_master.parquet",
    )

    assert result["status"] == "ok"
    assert result["rows"] == {
        "master_xlsx": 3,
        "compatibility_xlsx": 3,
        "master_parquet": 3,
    }


def test_trade_id_prefers_unique_dedup_key_and_preserves_order_id() -> None:
    frame = build_phase5_compatibility_frame(ocr_master(), "2026-06-22T12:30:00Z")

    normalized = normalize_trades(frame)

    assert normalized["trade_id"].tolist() == ["fp-1", "fp-2", "fp-3"]
    assert normalized["order_id"].iloc[0] == "order-1"
    assert pd.isna(normalized["order_id"].iloc[1])
    assert normalized["trade_id"].duplicated().sum() == 0


def test_ocr_numeric_symbol_and_incomplete_timestamp_rows_are_preserved() -> None:
    frame = build_phase5_compatibility_frame(ocr_master(), "2026-06-22T12:30:00Z")
    frame.loc[0, "moeda"] = "BTC_USDT"
    frame.loc[0, "preco_abertura"] = "93,619.04216 USDT"
    frame.loc[0, "horario_abertura"] = pd.NA

    normalized = normalize_trades(frame)

    assert len(normalized) == len(frame)
    assert normalized.loc[0, "symbol"] == "BTCUSDT"
    assert normalized.loc[0, "entry_price"] == pytest.approx(93619.04216)
    assert pd.isna(normalized.loc[0, "open_ts"])
    assert coerce_float("+4.34030 USDT") == pytest.approx(4.34030)
    assert normalize_symbol("ETH_USDT") == "ETHUSDT"


def test_trade_id_uses_order_id_only_when_complete_and_unique() -> None:
    trades = pd.DataFrame(
        {
            "order_id": ["one", "two"],
            "moeda": ["BTCUSDT", "ETHUSDT"],
            "fechar_side": ["long", "short"],
            "preco_abertura": [1, 2],
            "preco_fechamento": [2, 1],
            "horario_abertura": ["2026-01-01", "2026-01-02"],
            "horario_fechamento": ["2026-01-01", "2026-01-02"],
            "volume_posicao": [1, 1],
            "pnl_fechado": [1, -1],
        }
    )

    assert build_institutional_trade_ids(trades).tolist() == ["one", "two"]


def test_trade_id_blocks_duplicate_deterministic_fingerprint() -> None:
    duplicate = pd.DataFrame(
        {
            "order_id": [pd.NA, pd.NA],
            "moeda": ["BTCUSDT", "BTCUSDT"],
            "fechar_side": ["long", "long"],
            "preco_abertura": [1, 1],
            "preco_fechamento": [2, 2],
            "horario_abertura": ["2026-01-01", "2026-01-01"],
            "horario_fechamento": ["2026-01-01", "2026-01-01"],
            "volume_posicao": [1, 1],
            "pnl_fechado": [1, 1],
        }
    )

    with pytest.raises(ValueError, match="duplicate_trade_id_after_deterministic_fingerprint"):
        build_institutional_trade_ids(duplicate)


def test_vectorized_feature_join_uses_latest_prior_candle() -> None:
    trades = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "open_ts": [pd.Timestamp("2026-01-01T10:02:30Z")],
            "close_ts": [pd.Timestamp("2026-01-01T10:03:30Z")],
        }
    )
    features = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 6,
            "tf": ["1m", "1m", "1m", "5m", "5m", "5m"],
            "ts": pd.to_datetime(
                [
                    "2026-01-01T10:01:00Z",
                    "2026-01-01T10:02:00Z",
                    "2026-01-01T10:04:00Z",
                    "2026-01-01T09:55:00Z",
                    "2026-01-01T10:00:00Z",
                    "2026-01-01T10:05:00Z",
                ],
                utc=True,
            ),
            "close": [101, 102, 104, 95, 100, 105],
        }
    )

    enriched = add_feature_snapshots(trades, features)

    assert enriched.loc[0, "open_1m_close"] == 102
    assert enriched.loc[0, "close_1m_close"] == 102
    assert enriched.loc[0, "open_5m_close"] == 100
    assert enriched.loc[0, "close_5m_close"] == 100


def test_cli_no_write_returns_controlled_json(tmp_path: Path) -> None:
    _, expected_hash, _ = prepare_project(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/sync_ocr_master_v11_phase5_sidecars.py"),
            "--project-root",
            str(tmp_path),
            "--expected-master-sha256",
            expected_hash,
            "--expected-rows",
            "3",
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False


def test_report_preserves_paper_safe_authority_flags(tmp_path: Path) -> None:
    _, expected_hash, _ = prepare_project(tmp_path)
    report = sync_ocr_master_v11_phase5_sidecars(
        tmp_path,
        expected_hash,
        3,
        no_write=True,
        now_utc=NOW,
    )

    assert report["writes_master_xlsx"] is False
    assert report["writes_master_parquet"] is True
    assert report["writes_compatibility_xlsx"] is True
    assert report["changes_training_dataset"] is False
    assert report["changes_model"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
