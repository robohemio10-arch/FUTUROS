
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import pandas as pd

from smartcrypto.ml.ai_shadow_outcome_attribution import (
    AttributionConfig,
    financial_metrics,
    run_ai_shadow_outcome_attribution,
)


def write_dataset(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def write_decisions(path: Path, rows: list[dict], include_probability: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        if include_probability:
            conn.execute(
                """
                CREATE TABLE ai_shadow_decisions (
                    trade_id TEXT PRIMARY KEY,
                    ai_decision TEXT,
                    probability REAL,
                    symbol TEXT,
                    side TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO ai_shadow_decisions
                (trade_id, ai_decision, probability, symbol, side)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_id"],
                        row.get("ai_decision", "AI_ACCEPT"),
                        row.get("probability", 0.5),
                        row.get("symbol", "BTCUSDT"),
                        row.get("side", "LONG"),
                    )
                    for row in rows
                ],
            )
        else:
            conn.execute(
                """
                CREATE TABLE ai_shadow_decisions (
                    trade_id TEXT PRIMARY KEY,
                    ai_decision TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO ai_shadow_decisions
                (trade_id, ai_decision)
                VALUES (?, ?)
                """,
                [(row["trade_id"], row.get("ai_decision", "AI_ACCEPT")) for row in rows],
            )

        conn.commit()


def dataset_rows() -> list[dict]:
    return [
        {"trade_id": "t1", "symbol": "BTCUSDT", "side": "LONG", "reported_pnl_usdt": 0.10},
        {"trade_id": "t2", "symbol": "BTCUSDT", "side": "LONG", "reported_pnl_usdt": -0.05},
        {"trade_id": "t3", "symbol": "ETHUSDT", "side": "SHORT", "reported_pnl_usdt": 0.04},
        {"trade_id": "t4", "symbol": "ETHUSDT", "side": "SHORT", "reported_pnl_usdt": -0.01},
    ]


def decision_rows() -> list[dict]:
    return [
        {"trade_id": "t1", "ai_decision": "AI_ACCEPT", "probability": 0.90, "symbol": "BTCUSDT", "side": "LONG"},
        {"trade_id": "t2", "ai_decision": "AI_REJECT", "probability": 0.30, "symbol": "BTCUSDT", "side": "LONG"},
        {"trade_id": "t3", "ai_decision": "AI_ACCEPT", "probability": 0.70, "symbol": "ETHUSDT", "side": "SHORT"},
        {"trade_id": "t4", "ai_decision": "AI_REJECT", "probability": 0.45, "symbol": "ETHUSDT", "side": "SHORT"},
    ]


def make_config(tmp_path: Path) -> AttributionConfig:
    return AttributionConfig(
        dataset_path=tmp_path / "features" / "training_dataset_quality_gated_binance_1m.parquet",
        decisions_path=tmp_path / "runtime" / "ai_shadow_filter_decisions.sqlite",
        report_path=tmp_path / "reports" / "ai_shadow_outcome_attribution_report.json",
    )


def test_calculates_expectancy_correctly() -> None:
    metrics = financial_metrics([0.10, -0.05, 0.04, -0.01])

    assert metrics["trades"] == 4
    assert metrics["expectancy"] == pytest.approx(0.02)
    assert metrics["total_return"] == pytest.approx(0.08)


def test_calculates_profit_factor_correctly() -> None:
    metrics = financial_metrics([0.10, -0.05, 0.04, -0.01])

    assert metrics["profit_factor"] == pytest.approx(0.14 / 0.06)
    assert metrics["profit_factor_status"] == "ok"


def test_handles_zero_loss_without_invalid_division() -> None:
    metrics = financial_metrics([0.10, 0.04])

    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_status"] == "loss_zero"


def test_blocks_when_dataset_is_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_decisions(config.decisions_path, decision_rows())

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "missing_dataset"
    assert result["reason"] == "missing_dataset"


def test_blocks_when_decisions_are_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, dataset_rows())

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "missing_decisions"
    assert result["reason"] == "missing_decisions"


def test_blocks_when_outcome_column_is_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, [{"trade_id": "t1", "symbol": "BTCUSDT"}])
    write_decisions(config.decisions_path, [{"trade_id": "t1", "ai_decision": "AI_ACCEPT", "probability": 0.80}])

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "missing_outcome_column"


def test_blocks_when_probability_column_is_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, [{"trade_id": "t1", "reported_pnl_usdt": 0.01}])
    write_decisions(config.decisions_path, [{"trade_id": "t1", "ai_decision": "AI_ACCEPT"}], include_probability=False)

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "missing_probability_column"


def test_detects_missing_and_extra_decisions(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, dataset_rows())
    write_decisions(
        config.decisions_path,
        [
            {"trade_id": "t1", "ai_decision": "AI_ACCEPT", "probability": 0.90},
            {"trade_id": "t2", "ai_decision": "AI_REJECT", "probability": 0.30},
            {"trade_id": "t_extra", "ai_decision": "AI_ACCEPT", "probability": 0.95},
        ],
    )

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "ok"
    assert result["missing_decisions"] == 2
    assert result["extra_decisions"] == 1
    assert "t3" in result["missing_decision_ids_sample"]
    assert "t_extra" in result["extra_decision_ids_sample"]


def test_strict_alignment_blocks_missing_extra(tmp_path: Path) -> None:
    config = AttributionConfig(
        dataset_path=tmp_path / "features" / "dataset.parquet",
        decisions_path=tmp_path / "runtime" / "decisions.sqlite",
        report_path=tmp_path / "reports" / "report.json",
        strict_alignment=True,
    )
    write_dataset(config.dataset_path, dataset_rows())
    write_decisions(config.decisions_path, [{"trade_id": "t1", "ai_decision": "AI_ACCEPT", "probability": 0.90}])

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "blocked"
    assert result["reason"] == "dataset_decision_alignment_mismatch"


def test_generates_probability_buckets_and_decision_metrics(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, dataset_rows())
    write_decisions(config.decisions_path, decision_rows())

    result = run_ai_shadow_outcome_attribution(config)

    assert result["status"] == "ok"
    assert result["accepted_count"] == 2
    assert result["rejected_count"] == 2
    assert "AI_ACCEPT" in result["metrics_by_decision"]
    assert "0.80-1.00" in result["probability_bucket_summary"]


def test_calculates_best_thresholds(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, dataset_rows())
    write_decisions(config.decisions_path, decision_rows())

    result = run_ai_shadow_outcome_attribution(config)

    assert result["best_threshold_by_expectancy"] is not None
    assert result["best_threshold_by_profit_factor"] is not None
    assert result["threshold_summary"]


def test_generates_symbol_and_side_summary(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_dataset(config.dataset_path, dataset_rows())
    write_decisions(config.decisions_path, decision_rows())

    result = run_ai_shadow_outcome_attribution(config)

    assert "BTCUSDT" in result["symbol_summary"]
    assert "LONG" in result["side_summary"]


def test_cli_prints_json_and_does_not_write_signals(tmp_path: Path) -> None:
    dataset = tmp_path / "features" / "dataset.parquet"
    decisions = tmp_path / "runtime" / "decisions.sqlite"
    report = tmp_path / "reports" / "report.json"
    forbidden_signal = tmp_path / "data" / "runtime" / "active_freqtrade_signals.json"

    write_dataset(dataset, dataset_rows())
    write_decisions(decisions, decision_rows())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_ai_shadow_outcome_attribution.py",
            "--dataset",
            str(dataset),
            "--decisions",
            str(decisions),
            "--report-json",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["live_trading_enabled"] is False
    assert payload["order_submission_enabled"] is False
    assert payload["exchange_private_access"] is False
    assert report.exists()
    assert not forbidden_signal.exists()
