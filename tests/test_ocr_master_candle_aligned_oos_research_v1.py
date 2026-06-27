from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.research.ocr_master_candle_aligned_oos_research import (
    build_ocr_master_candle_aligned_oos_research_report,
)


def _write_fixture_sources(root: Path) -> tuple[Path, Path]:
    master = root / "data" / "trades" / "trades_master.csv"
    candles = root / "data" / "candles" / "BTCUSDT_1m.csv"
    master.parent.mkdir(parents=True, exist_ok=True)
    candles.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "open_time_utc": "2026-01-01T00:30:00Z",
                "close_time_utc": "2026-01-01T00:45:00Z",
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "pnl_usdt": -5.0,
                "exit_reason": "stop_loss",
            },
            {
                "open_time_utc": "2026-01-01T01:00:00Z",
                "close_time_utc": "2026-01-01T03:30:00Z",
                "symbol": "BTC/USDT:USDT",
                "side": "short",
                "pnl_usdt": 9.0,
                "exit_reason": "roi",
            },
        ]
    ).to_csv(master, index=False)
    rows = []
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    price = 100.0
    for minute in range(121):
        price = price - 0.08 if minute < 30 else price + 0.04
        ts = base + pd.Timedelta(minutes=minute)
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "symbol": "BTCUSDT",
                "open": price - 0.02,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 10.0,
            }
        )
    pd.DataFrame(rows).to_csv(candles, index=False)
    return master, candles.parent


def _write_ocr_fixture_sources(root: Path) -> tuple[Path, Path]:
    master = root / "data" / "trades" / "trades_master.xlsx"
    candle_dir = root / "data" / "raw" / "binance_futures_klines"
    master.parent.mkdir(parents=True, exist_ok=True)
    candle_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [
            {
                "11_moeda": "ETH_USDT",
                "12_fechar_long_short": "Fechar Long",
                "1_pnl_fechado": "+4.192557 USDT",
                "2_taxa_lucros_perdas_fechados": "0.84%",
                "3_preco_de_abertura": "2939.773604 USDT",
                "4_preco_de_fechamento": "2942.184982 USDT",
                "7_horario_de_abertura": "2026-01-21 11:59:42",
                "8_horario_de_fechamento": "2026-01-21 12:00:54",
                "10_numero_do_pedido": "order-1",
                "layout_detectado": "full_com_abertura",
            },
            {
                "11_moeda": "BTC_USDT",
                "12_fechar_long_short": "Fechar Short",
                "1_pnl_fechado": "-2.195619 USDT",
                "2_taxa_lucros_perdas_fechados": "-0.43%",
                "3_preco_de_abertura": "90033.061410 USDT",
                "4_preco_de_fechamento": "90034.862072 USDT",
                "7_horario_de_abertura": "2026-01-21 21:29:58",
                "8_horario_de_fechamento": "2026-01-21 21:30:03",
                "10_numero_do_pedido": "order-2",
                "layout_detectado": "full_com_abertura",
            },
            {
                "11_moeda": "BTC_USDT",
                "12_fechar_long_short": "Fechar Long",
                "1_pnl_fechado": "+2.33831 USDT",
                "2_taxa_lucros_perdas_fechados": "0.46%",
                "3_preco_de_abertura": "94029.98140 USDT",
                "4_preco_de_fechamento": "94052.01391 USDT",
                "7_horario_de_abertura": None,
                "8_horario_de_fechamento": "2026-01-06 00:32:14",
                "10_numero_do_pedido": "legacy-missing-open",
                "layout_detectado": "legacy_janeiro_sem_abertura",
            },
        ]
    ).to_excel(master, index=False)

    for symbol, start_price in (("ETHUSDT", 2945.0), ("BTCUSDT", 90100.0)):
        rows = []
        base = pd.Timestamp("2026-01-21T11:00:00Z")
        price = start_price
        for minute in range(720):
            # Drift down enough to trigger the H6 candidate for some late entries.
            price -= 0.10 if symbol == "ETHUSDT" else 1.0
            ts = base + pd.Timedelta(minutes=minute)
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": price + 0.02,
                    "high": price + 0.05,
                    "low": price - 0.05,
                    "close": price,
                    "volume": 100.0,
                    "quote_asset_volume": 1000.0,
                    "number_of_trades": 10,
                    "taker_buy_base_volume": 50.0,
                    "taker_buy_quote_volume": 500.0,
                    "symbol": symbol,
                }
            )
        (candle_dir / f"{symbol}_1m_20251230_20261208.csv").write_text(pd.DataFrame(rows).to_csv(index=False), encoding="utf-8")
    return master, root / "data"


def test_default_is_blocked_without_runtime_read(tmp_path: Path) -> None:
    report = build_ocr_master_candle_aligned_oos_research_report(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["allow_runtime_read"] is False
    assert report["trades_master_loaded"] is False
    assert report["master_candle_alignment_computed"] is False
    assert report["operational_authority"] is False
    assert report["can_promote_rules"] is False
    assert report["sends_orders"] is False
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_runtime_read_loads_master_and_candles_read_only(tmp_path: Path) -> None:
    master, candle_root = _write_fixture_sources(tmp_path)
    report = build_ocr_master_candle_aligned_oos_research_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        trades_master=master,
        candle_roots=[candle_root],
    )
    assert report["status"] == "blocked"
    assert report["input_mode"] == "runtime_read_only"
    assert report["trades_master_loaded"] is True
    assert report["trades_master_rows"] == 2
    assert report["candle_sources_loaded"] is True
    assert report["candle_rows"] > 0
    assert report["master_candle_alignment_computed"] is True
    assert report["feature_rows"] == 2
    assert report["slice_count"] > 0
    assert report["global_metrics"]["H1"]["triggered_count"] >= 1
    assert report["global_metrics"]["H6"]["trade_count"] == 2
    assert report["ready_for_candidate_registry"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False


def test_ocr_bitradex_master_schema_aligns_to_canonical_candles(tmp_path: Path) -> None:
    master, candle_root = _write_ocr_fixture_sources(tmp_path)
    report = build_ocr_master_candle_aligned_oos_research_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        trades_master=master,
        candle_roots=[candle_root],
    )
    assert report["trades_master_loaded"] is True
    assert report["trades_master_rows"] == 3
    assert report["normalized_trade_rows"] == 2
    assert report["trades_master_schema_status"] == "candidate_trade_schema"
    assert report["candle_sources_loaded"] is True
    assert report["candle_source_count"] == 2
    assert report["master_candle_alignment_computed"] is True
    assert report["aligned_rows"] == 2
    assert report["alignment_coverage_ratio"] == 1.0
    assert report["global_metrics"]["H1"]["trade_count"] == 2
    assert report["global_metrics"]["H2"]["triggered_count"] == 1
    assert report["slice_count"] > 0
    assert report["reason"] == "ocr_master_candle_alignment_computed_research_only"


def test_write_requires_explicit_write_and_path(tmp_path: Path) -> None:
    master, candle_root = _write_fixture_sources(tmp_path)
    output = tmp_path / "report.json"
    report = build_ocr_master_candle_aligned_oos_research_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        trades_master=master,
        candle_roots=[candle_root],
        output_path=output,
        write=True,
    )
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "ocr_master_candle_aligned_oos_research_v1"
    assert report["write_performed"] is True
    assert report["writes_reports"] is True
    assert report["writes_runtime"] is False


def test_cli_default_json(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_ocr_master_candle_aligned_oos_research_v1.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", str(tmp_path), "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["allow_runtime_read"] is False
    assert report["master_candle_alignment_computed"] is False
    assert report["sends_orders"] is False


def test_cli_runtime_json(tmp_path: Path) -> None:
    master, candle_root = _write_fixture_sources(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_ocr_master_candle_aligned_oos_research_v1.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--allow-runtime-read",
            "--trades-master",
            str(master),
            "--candle-root",
            str(candle_root),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["trades_master_loaded"] is True
    assert report["candle_sources_loaded"] is True
    assert report["master_candle_alignment_computed"] is True
    assert report["feature_rows"] == 2
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_missing_sources_remain_blocked(tmp_path: Path) -> None:
    report = build_ocr_master_candle_aligned_oos_research_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        trades_master=tmp_path / "missing.xlsx",
        candle_roots=[tmp_path / "missing_candles"],
    )
    assert report["status"] == "blocked"
    assert report["trades_master_loaded"] is False
    assert report["candle_sources_loaded"] is False
    assert report["master_candle_alignment_computed"] is False
    assert "trades_master_missing" in report["critical_warnings"]
