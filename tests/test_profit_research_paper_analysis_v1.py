from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.source_profile import (
    load_source_profile,
)
from smartcrypto.research.profit_research.paper_analysis import (
    DEFAULT_SOURCE_PROFILE,
    SAFETY_FLAGS,
    _candidate_exit_price,
    attach_market_context,
    build_block_candidates,
    build_stake_candidates,
    financial_metrics,
    inventory_image_directory,
    load_market_candles,
    normalize_snapshot_trades,
    resolve_profit_research_paths,
    write_research_outputs,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "build_profit_research_paper_analysis_v1.py"


def snapshot_rows() -> pd.DataFrame:
    common = {
        "amount": 1.0,
        "close_profit": 0.097,
        "contract_size": 1.0,
        "fee_close_cost": 0.3,
        "fee_open_cost": 0.1,
        "leverage": 2.0,
        "open_rate": 100.0,
        "stake_amount": 50.0,
        "exit_reason": "exit_signal",
        "strategy": "PaperStrategy",
        "timeframe": "1m",
        "enter_tag": "entry",
    }
    return pd.DataFrame(
        [
            {
                **common,
                "id": 1,
                "pair": "BTC/USDT:USDT",
                "is_short": False,
                "open_date": "2026-01-01T00:00:00Z",
                "close_date": "2026-01-01T00:02:00Z",
                "close_rate": 110.0,
                "funding_fees": 0.2,
                "close_profit_abs": 9.7,
            },
            {
                **common,
                "id": 2,
                "pair": "ETH/USDT:USDT",
                "is_short": True,
                "open_date": "2026-01-01T01:00:00Z",
                "close_date": "2026-01-01T01:02:00Z",
                "close_rate": 90.0,
                "funding_fees": -0.1,
                "close_profit_abs": 9.4,
            },
        ]
    )


def normalized_trades() -> pd.DataFrame:
    profile = load_source_profile(ROOT / DEFAULT_SOURCE_PROFILE)
    return normalize_snapshot_trades(snapshot_rows(), profile)


def candles() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2025-12-31T23:59:00Z"),
                "open": 99.0,
                "high": 100.0,
                "low": 98.0,
                "close": 100.0,
                "atr_14": 2.0,
                "atr_pct_14": 0.02,
                "rsi_14": 55.0,
                "ret_1": 0.001,
                "trend_score": 1.0,
                "volume": 10.0,
            },
            {
                "symbol": "BTCUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
                "open": 100.0,
                "high": 112.0,
                "low": 95.0,
                "close": 105.0,
                "atr_14": 999.0,
                "atr_pct_14": 0.02,
                "rsi_14": 55.0,
                "ret_1": 0.001,
                "trend_score": 1.0,
                "volume": 10.0,
            },
            {
                "symbol": "BTCUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2026-01-01T00:02:00Z"),
                "open": 105.0,
                "high": 110.0,
                "low": 101.0,
                "close": 110.0,
            },
            {
                "symbol": "ETHUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2026-01-01T00:59:00Z"),
                "open": 101.0,
                "high": 102.0,
                "low": 99.0,
                "close": 100.0,
                "atr_14": 3.0,
                "atr_pct_14": 0.03,
                "rsi_14": 45.0,
                "ret_1": -0.001,
                "trend_score": -1.0,
                "volume": 20.0,
            },
            {
                "symbol": "ETHUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2026-01-01T01:00:00Z"),
                "open": 100.0,
                "high": 105.0,
                "low": 88.0,
                "close": 95.0,
                "atr_14": 999.0,
                "atr_pct_14": 0.03,
                "rsi_14": 45.0,
                "ret_1": -0.001,
                "trend_score": -1.0,
                "volume": 20.0,
            },
            {
                "symbol": "ETHUSDT",
                "tf": "1m",
                "ts": pd.Timestamp("2026-01-01T01:02:00Z"),
                "open": 95.0,
                "high": 98.0,
                "low": 90.0,
                "close": 90.0,
            },
        ]
    )


def test_snapshot_accounting_is_independent_and_reconciled() -> None:
    result = normalized_trades()

    assert result["analysis_eligible"].tolist() == [True, True]
    assert result["gross_pnl"].tolist() == pytest.approx([10.0, 10.0])
    assert result["fees"].tolist() == pytest.approx([0.5, 0.5])
    assert result["funding"].tolist() == pytest.approx([-0.2, 0.1])
    assert result["net_pnl_reconstructed"].tolist() == pytest.approx([9.7, 9.4])


def test_point_in_time_features_and_mfe_mae_cover_long_and_short() -> None:
    result = attach_market_context(normalized_trades(), candles())

    assert (result["feature_timestamp_utc"] <= result["open_time_utc"]).all()
    assert result["pre_entry_atr"].tolist() == pytest.approx([2.0, 3.0])
    assert result["mfe_pct"].tolist() == pytest.approx([0.12, 0.12])
    assert result["mae_pct"].tolist() == pytest.approx([-0.05, -0.05])
    assert result["profit_giveback"].tolist() == pytest.approx([2.0, 2.0])


def test_financial_metrics_are_deterministic() -> None:
    frame = normalized_trades()
    first = financial_metrics(frame)
    second = financial_metrics(frame)

    assert first == second
    assert first["total_trades"] == 2
    assert first["net_pnl"] == pytest.approx(19.1)
    assert first["maximum_drawdown"] == pytest.approx(0.0)


def test_block_candidate_requires_positive_oos_effect() -> None:
    count = 40
    frame = pd.DataFrame(
        {
            "trade_id": range(count),
            "close_time_utc": pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC"),
            "side": ["long" if index % 2 == 0 else "short" for index in range(count)],
            "net_pnl": [-2.0 if index % 2 == 0 else 1.0 for index in range(count)],
            "gross_pnl": [-2.0 if index % 2 == 0 else 1.0 for index in range(count)],
            "fees": 0.0,
            "funding": 0.0,
            "stake_amount": 10.0,
            "duration_seconds": 60.0,
        }
    )
    segments = [
        {
            "segment_dimension": "side",
            "segment_value": "long",
            "trade_count": 20,
            "net_pnl": -40.0,
        }
    ]

    candidate = build_block_candidates(frame, segments)[0]

    assert candidate["trades_affected"] == 20
    assert candidate["delta_pnl"] == pytest.approx(40.0)
    assert candidate["out_of_sample_delta_pnl"] > 0
    assert candidate["stable_across_temporal_split"] is True
    assert candidate["decision"] == "PROMOVER_PARA_BACKTEST"


def test_same_candle_exit_is_conservatively_stopped_first() -> None:
    trade = pd.Series({"entry_price": 100.0, "exit_price": 101.0, "side": "long"})
    path = pd.DataFrame([{"high": 102.0, "low": 98.0}])

    result = _candidate_exit_price(
        trade,
        path,
        {"kind": "fixed_tp_sl", "tp": 0.01, "sl": 0.01},
    )

    assert result == pytest.approx(99.0)


def test_candidate_stake_policies_never_increase_stake() -> None:
    frame = normalized_trades()
    frame["volatility_bucket"] = ["volatility_high", "volatility_low"]

    policies = build_stake_candidates(frame)

    assert len(policies) == 3
    assert all(policy["maximum_stake_multiplier"] <= 1.0 for policy in policies)
    assert all(policy["increases_operational_stake"] is False for policy in policies)


def test_image_inventory_uses_only_explicit_root_lot_and_detects_duplicates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "prints"
    nested = source / "lotes" / "historical"
    nested.mkdir(parents=True)
    (source / "one.jpg").write_bytes(b"same")
    (source / "two.jpg").write_bytes(b"same")
    (nested / "old.jpg").write_bytes(b"old")

    result = inventory_image_directory(source)

    assert result["file_count"] == 2
    assert result["nested_files_ignored"] == 1
    assert result["duplicate_image_rows"] == 1
    assert result["incorporated_into_analysis"] is False
    assert result["ocr_policy"] == "black_rectangle_rois_only_red_top_ignored"


def test_market_candles_block_lookahead_columns(tmp_path: Path) -> None:
    path = tmp_path / "candles.parquet"
    pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "tf": ["1m"],
            "ts": [pd.Timestamp("2026-01-01T00:00:00Z")],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "future_ret_1": [0.01],
        }
    ).to_parquet(path, index=False)

    with pytest.raises(ValueError, match="operational_candle_lookahead_columns"):
        load_market_candles(path)


def test_write_materializes_only_explicit_research_outputs(tmp_path: Path) -> None:
    paths = resolve_profit_research_paths(
        tmp_path,
        output_dataset="research/dataset.parquet",
        report_json="reports/report.json",
        report_markdown="reports/report.md",
    )
    report = {"status": "ok", "decision": "MANTER_EM_RESEARCH"}

    write_research_outputs(paths, normalized_trades(), report)

    assert paths.output_dataset.exists()
    assert json.loads(paths.report_json.read_text(encoding="utf-8"))["status"] == "ok"
    assert "MANTER_EM_RESEARCH" in paths.report_markdown.read_text(encoding="utf-8")
    assert not (tmp_path / "data" / "trades" / "trades_master.parquet").exists()


def test_cli_defaults_to_no_write_and_fails_closed_for_missing_snapshot(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.parquet"
    master.write_bytes(b"protected")
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--snapshot-db",
            str(tmp_path / "missing.sqlite"),
            "--trader-master",
            str(master),
            "--output-dataset",
            str(tmp_path / "output.parquet"),
            "--report-json",
            str(tmp_path / "report.json"),
            "--report-markdown",
            str(tmp_path / "report.md"),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert master.read_bytes() == b"protected"
    assert not (tmp_path / "output.parquet").exists()
    assert not (tmp_path / "report.json").exists()


def test_safety_contract_never_authorizes_runtime_or_orders() -> None:
    assert SAFETY_FLAGS == {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "training_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "freqtrade_updated": False,
        "risk_manager_updated": False,
        "stake_runtime_changed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_trader_master": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "runs_ocr": False,
    }
