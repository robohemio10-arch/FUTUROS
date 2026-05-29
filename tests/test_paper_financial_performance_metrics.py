from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import (
    MISSING_PNL_COLUMN,
    MISSING_SOURCE,
    compute_financial_metrics,
    run_paper_financial_performance_metrics,
    run_paper_financial_performance_metrics_from_paths,
)


MODULE_PATH = Path("scripts/run_paper_financial_performance_metrics.py")


def trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3", "t4", "t5"],
            "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT", "SOLUSDT"],
            "side": ["LONG", "SHORT", "LONG", "SHORT", "LONG"],
            "regime": ["trend", "trend", "range", "range", "trend"],
            "strategy": ["paper-a", "paper-a", "paper-b", "paper-b", "paper-a"],
            "opened_at": pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-02T00:00:00Z",
                    "2026-02-01T00:00:00Z",
                    "2026-02-02T00:00:00Z",
                    "2026-02-03T00:00:00Z",
                ]
            ),
            "reported_pnl_usdt": [100.0, -50.0, 200.0, -100.0, 50.0],
        }
    )


def run_report(**overrides):
    kwargs = {
        "trades": trades(),
        "source_path": "trades.parquet",
        "report_path": "report.json",
        "minimum_recommended_trades": 3,
    }
    kwargs.update(overrides)
    return run_paper_financial_performance_metrics(**kwargs)


def load_runner():
    spec = importlib.util.spec_from_file_location("run_paper_financial_performance_metrics", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_calculates_profit_factor_correctly() -> None:
    metrics = compute_financial_metrics(pd.DataFrame({"__pnl": [100.0, -50.0, 200.0, -100.0]}))

    assert metrics["profit_factor"] == 2.0
    assert metrics["profit_factor_status"] == "ok"


def test_calculates_expectancy_correctly() -> None:
    metrics = compute_financial_metrics(pd.DataFrame({"__pnl": [100.0, -50.0, 200.0, -100.0]}))

    assert metrics["expectancy"] == 37.5


def test_calculates_max_drawdown_correctly() -> None:
    metrics = compute_financial_metrics(pd.DataFrame({"__pnl": [100.0, -50.0, 200.0, -300.0, 50.0]}))

    assert metrics["max_drawdown"] == 300.0


def test_calculates_payoff_ratio_correctly() -> None:
    metrics = compute_financial_metrics(pd.DataFrame({"__pnl": [100.0, -50.0, 200.0, -100.0]}))

    assert metrics["payoff_ratio"] == 2.0


def test_handles_zero_loss_without_invalid_division() -> None:
    metrics = compute_financial_metrics(pd.DataFrame({"__pnl": [100.0, 50.0]}))

    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_status"] == "no_losses"
    assert json.dumps(metrics, sort_keys=True)


def test_calculates_symbol_summary() -> None:
    report = run_report()

    btc = next(row for row in report["symbol_summary"] if row["value"] == "BTCUSDT")
    assert btc["trades"] == 2
    assert btc["total_pnl"] == 50.0


def test_calculates_side_summary() -> None:
    report = run_report()

    long = next(row for row in report["side_summary"] if row["value"] == "LONG")
    assert long["trades"] == 3
    assert long["total_pnl"] == 350.0


def test_calculates_regime_summary_when_present() -> None:
    report = run_report()

    assert {row["value"] for row in report["regime_summary"]} == {"range", "trend"}


def test_calculates_monthly_and_daily_summary_when_timestamp_exists() -> None:
    report = run_report()

    assert {row["period"] for row in report["monthly_summary"]} == {"2026-01", "2026-02"}
    assert len(report["daily_summary"]) == 5


def test_blocks_when_source_is_missing(tmp_path) -> None:
    report = run_paper_financial_performance_metrics_from_paths(
        source_path=tmp_path / "missing.parquet",
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == MISSING_SOURCE
    assert report["reason"] == "missing_source"


def test_blocks_when_pnl_column_is_missing() -> None:
    report = run_report(trades=trades().drop(columns=["reported_pnl_usdt"]))

    assert report["status"] == MISSING_PNL_COLUMN
    assert report["reason"] == "missing_pnl_column"


def test_treats_nan_and_infinity_explicitly() -> None:
    bad = trades()
    bad.loc[0, "reported_pnl_usdt"] = np.inf

    report = run_report(trades=bad)

    assert report["status"] == "invalid_schema"
    assert report["reason"] == "pnl_column_contains_null_or_non_finite_values"


def test_cli_generates_controlled_json(tmp_path, capsys) -> None:
    module = load_runner()
    source = tmp_path / "trades.parquet"
    report_path = tmp_path / "report.json"
    trades().to_parquet(source)

    rc = module.main(
        [
            "--source",
            str(source),
            "--report",
            str(report_path),
            "--minimum-recommended-trades",
            "3",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"
    assert output["report_path"] == str(report_path)
    assert report_path.exists()


def test_cli_does_not_write_active_freqtrade_signals(tmp_path) -> None:
    module = load_runner()
    source = tmp_path / "trades.parquet"
    report_path = tmp_path / "report.json"
    active_signals = tmp_path / "active_freqtrade_signals.json"
    active_signals.write_text('{"keep": true}', encoding="utf-8")
    trades().to_parquet(source)

    rc = module.main(["--source", str(source), "--report", str(report_path)])

    assert rc == 0
    assert active_signals.read_text(encoding="utf-8") == '{"keep": true}'


def test_sample_warning_and_reliability_are_exposed() -> None:
    report = run_report(minimum_recommended_trades=30)

    assert report["status"] == "ok"
    assert report["sample_size"] == 5
    assert report["sample_warning"] == "sample_below_minimum_recommended_trades:5:30"
    assert report["metrics_reliable"] is False
