from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.research.ocr_master_candle_positive_ev_slice_mining.slice_mining import (
    build_positive_ev_slice_mining_report,
    mine_positive_ev_slices,
    normalize_candles,
    normalize_legacy_trade_dataset,
)


def _write_fixture_sources(root: Path) -> tuple[Path, Path]:
    trades_dir = root / "data" / "trades"
    candle_dir = root / "data" / "raw" / "binance_futures_klines"
    trades_dir.mkdir(parents=True)
    candle_dir.mkdir(parents=True)

    rows = []
    for idx in range(80):
        symbol = "BTC_USDT" if idx < 40 else "ETH_USDT"
        side = "Fechar Long" if idx % 2 == 0 else "Fechar Short"
        hour = 1 if idx < 45 else 14
        minute = idx % 55
        pnl = 2.0 if hour == 1 else (-1.5 if idx % 3 else 0.4)
        rows.append(
            {
                "11_moeda": symbol,
                "12_fechar_long_short": side,
                "1_pnl_fechado": f"{pnl:+.4f} USDT",
                "3_preco_de_abertura": "100.0 USDT" if symbol.startswith("BTC") else "50.0 USDT",
                "4_preco_de_fechamento": "101.0 USDT" if pnl > 0 else "99.0 USDT",
                "7_horario_de_abertura": f"2026-01-21 {hour:02d}:{minute:02d}:30",
                "8_horario_de_fechamento": f"2026-01-21 {hour:02d}:{minute:02d}:59",
            }
        )
    trades_path = trades_dir / "trades_master.csv"
    pd.DataFrame(rows).to_csv(trades_path, index=False)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        candles = []
        for hour in (1, 14):
            for minute in range(60):
                close = 100.0 + minute * 0.01 if symbol == "BTCUSDT" else 50.0 + minute * 0.01
                candles.append(
                    {
                        "timestamp": f"2026-01-21 {hour:02d}:{minute:02d}:00+00:00",
                        "open": close - 0.01,
                        "high": close + 0.02,
                        "low": close - 0.02,
                        "close": close,
                        "volume": 1000.0,
                        "quote_asset_volume": 1000.0,
                        "number_of_trades": 100,
                        "taker_buy_base_volume": 500.0,
                        "taker_buy_quote_volume": 500.0,
                        "symbol": symbol,
                    }
                )
        candle_path = candle_dir / f"{symbol}_1m_20251230_20261208.csv"
        pd.DataFrame(candles).to_csv(candle_path, index=False)
    return trades_path, root / "data"


def test_default_no_runtime_read_is_blocked_and_safe(tmp_path: Path) -> None:
    report = build_positive_ev_slice_mining_report(project_root=tmp_path, allow_runtime_read=False, no_write=True)

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["legacy_trade_dataset_loaded"] is False
    assert report["aligned_rows"] == 0
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["write_performed"] is False


def test_normalize_ocr_master_schema() -> None:
    raw = pd.DataFrame(
        [
            {
                "11_moeda": "BTC_USDT",
                "12_fechar_long_short": "Fechar Long",
                "1_pnl_fechado": "+1.25 USDT",
                "3_preco_de_abertura": "100.0 USDT",
                "4_preco_de_fechamento": "101.0 USDT",
                "7_horario_de_abertura": "2026-01-21 11:59:42",
                "8_horario_de_fechamento": "2026-01-21 12:00:54",
            }
        ]
    )

    normalized = normalize_legacy_trade_dataset(raw)

    assert len(normalized) == 1
    assert normalized.iloc[0]["symbol_norm"] == "BTCUSDT"
    assert normalized.iloc[0]["side_norm"] == "long"
    assert normalized.iloc[0]["pnl_usdt"] == 1.25


def test_normalize_candles_schema() -> None:
    raw = pd.DataFrame(
        [
            {
                "timestamp": "2026-01-21 11:59:00+00:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "symbol": "BTCUSDT",
            }
        ]
    )

    normalized = normalize_candles(raw)

    assert len(normalized) == 1
    assert normalized.iloc[0]["symbol_norm"] == "BTCUSDT"
    assert str(normalized.iloc[0]["candle_ts"]).endswith("+00:00")


def test_runtime_read_mines_positive_candidates(tmp_path: Path) -> None:
    trades_path, candle_root = _write_fixture_sources(tmp_path)

    report = build_positive_ev_slice_mining_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        legacy_trade_dataset=trades_path,
        candle_roots=[candle_root],
        min_trade_count=10,
        max_day_concentration=1.0,
        no_write=True,
    )

    assert report["legacy_trade_dataset_loaded"] is True
    assert report["candle_sources_loaded"] is True
    assert report["master_candle_alignment_computed"] is True
    assert report["aligned_rows"] == 80
    assert report["candidate_count"] > 0
    assert report["positive_candidate_count"] > 0
    assert report["ready_for_oos_validation"] is True
    assert report["ready_for_candidate_registry"] is False
    assert report["paper_observation_allowed"] is False


def test_positive_candidates_remain_research_only(tmp_path: Path) -> None:
    trades_path, candle_root = _write_fixture_sources(tmp_path)

    report = build_positive_ev_slice_mining_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        legacy_trade_dataset=trades_path,
        candle_roots=[candle_root],
        min_trade_count=10,
        max_day_concentration=1.0,
        no_write=True,
    )

    assert report["positive_candidate_count"] > 0
    candidate = report["top_positive_candidates"][0]
    assert candidate["eligible_for_oos_validation"] is True
    assert candidate["ready_for_candidate_registry"] is False
    assert candidate["operational_authority"] is False
    assert candidate["can_promote_rules"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["updates_ai_shadow_runtime"] is False


def test_candidate_rejected_when_concentrated(tmp_path: Path) -> None:
    trades_path, candle_root = _write_fixture_sources(tmp_path)

    report = build_positive_ev_slice_mining_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        legacy_trade_dataset=trades_path,
        candle_roots=[candle_root],
        min_trade_count=10,
        max_day_concentration=0.01,
        no_write=True,
    )

    assert report["positive_candidate_count"] == 0
    assert report["ready_for_oos_validation"] is False


def test_mining_handles_empty_frame() -> None:
    mining = mine_positive_ev_slices(pd.DataFrame())

    assert mining["baseline_metrics"]["trade_count"] == 0
    assert mining["candidate_count"] == 0
    assert mining["positive_candidate_count"] == 0


def test_no_write_does_not_create_report(tmp_path: Path) -> None:
    trades_path, candle_root = _write_fixture_sources(tmp_path)

    report = build_positive_ev_slice_mining_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        legacy_trade_dataset=trades_path,
        candle_roots=[candle_root],
        write=True,
        no_write=True,
    )

    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ocr_master_candle_positive_ev_slice_mining_v1.json").exists()


def test_cli_outputs_json(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_ocr_master_candle_positive_ev_slice_mining_v1.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ocr_master_candle_positive_ev_slice_mining_v1"
    assert payload["status"] == "blocked"
    assert payload["write_performed"] is False
