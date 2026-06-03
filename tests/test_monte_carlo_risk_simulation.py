from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.monte_carlo_risk_simulation import (
    max_losing_streak,
    run_monte_carlo_risk_simulation,
    run_monte_carlo_risk_simulation_frame,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def returns_frame(rows: int = 40) -> pd.DataFrame:
    values = [0.02, -0.01, 0.015, -0.005, 0.01] * ((rows // 5) + 1)
    return pd.DataFrame({"target_return": values[:rows]})


def bad_returns_frame(rows: int = 40) -> pd.DataFrame:
    return pd.DataFrame({"target_return": [-0.08, -0.06, -0.04, 0.01] * ((rows // 4) + 1)}).iloc[:rows]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def default_kwargs() -> dict:
    return {
        "report_path": None,
        "initial_capital": 1000,
        "stake": 100,
        "leverage": 1,
        "fee_bps": 1,
        "slippage_bps": 1,
        "spread_bps": 1,
        "simulations": 150,
        "horizon_trades": 20,
        "seed": 123,
        "min_trades": 10,
    }


def test_monte_carlo_blocks_missing_input(tmp_path: Path) -> None:
    report = run_monte_carlo_risk_simulation(
        input_path=tmp_path / "missing.jsonl",
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_input"


def test_monte_carlo_blocks_empty_input(tmp_path: Path) -> None:
    source = tmp_path / "empty.jsonl"
    source.write_text("", encoding="utf-8")

    report = run_monte_carlo_risk_simulation(input_path=source, report_path=None)

    assert report["status"] == "blocked"
    assert report["reason"] == "empty_input"


def test_monte_carlo_blocks_missing_return_columns() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=pd.DataFrame({"foo": [1, 2, 3]}),
        **default_kwargs(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_return_column"


def test_monte_carlo_blocks_invalid_capital_stake_or_leverage() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(),
        initial_capital=0,
        stake=-1,
        leverage=0,
        report_path=None,
        simulations=150,
        horizon_trades=20,
    )

    assert report["status"] == "blocked"
    assert "invalid_initial_capital" in report["reason"]
    assert "invalid_stake" in report["reason"]
    assert "invalid_leverage" in report["reason"]


def test_monte_carlo_blocks_unsafe_safety_flags() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(),
        strict=True,
        safety_overrides={"live_trading_enabled": True, "sends_orders": True},
        **default_kwargs(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_safety_flags"
    assert "live_trading_enabled" in report["blocking_errors"]
    assert "sends_orders" in report["blocking_errors"]


def test_monte_carlo_is_reproducible_with_seed() -> None:
    first = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())
    second = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    assert first["risk_metrics"] == second["risk_metrics"]
    assert first["distribution_summary"] == second["distribution_summary"]


def test_monte_carlo_calculates_equity_distribution() -> None:
    report = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    metrics = report["risk_metrics"]
    assert report["status"] in {"ok", "warning"}
    assert metrics["median_final_equity"] > 0
    assert metrics["p05_final_equity"] <= metrics["p95_final_equity"]
    assert report["distribution_summary"]["final_equity_count"] == 150


def test_monte_carlo_calculates_drawdown_metrics() -> None:
    report = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    metrics = report["risk_metrics"]
    assert metrics["median_max_drawdown_pct"] >= 0
    assert metrics["p95_max_drawdown_pct"] >= metrics["median_max_drawdown_pct"]
    assert metrics["worst_max_drawdown_pct"] >= metrics["p95_max_drawdown_pct"]


def test_monte_carlo_calculates_losing_streak() -> None:
    assert max_losing_streak(pd.Series([1, -1, -2, 3, -1]).to_numpy()) == 2
    report = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    assert report["risk_metrics"]["median_max_losing_streak"] >= 0
    assert report["risk_metrics"]["p95_max_losing_streak"] >= report["risk_metrics"]["median_max_losing_streak"]


def test_monte_carlo_calculates_var_and_cvar() -> None:
    report = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    metrics = report["risk_metrics"]
    assert "var_95" in metrics
    assert "cvar_95" in metrics
    assert metrics["cvar_95"] <= metrics["var_95"]


def test_monte_carlo_calculates_risk_of_ruin() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=bad_returns_frame(),
        ruin_threshold_pct=5,
        max_acceptable_drawdown_pct=99,
        **default_kwargs(),
    )

    assert report["risk_metrics"]["risk_of_ruin"] > 0


def test_monte_carlo_applies_fee_slippage_spread_stress() -> None:
    no_cost = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(),
        fee_bps=0,
        slippage_bps=0,
        spread_bps=0,
        stress_multiplier=1,
        report_path=None,
        initial_capital=1000,
        stake=100,
        leverage=1,
        simulations=150,
        horizon_trades=20,
        seed=123,
        min_trades=10,
    )
    stressed = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(),
        fee_bps=10,
        slippage_bps=10,
        spread_bps=10,
        stress_multiplier=2,
        report_path=None,
        initial_capital=1000,
        stake=100,
        leverage=1,
        simulations=150,
        horizon_trades=20,
        seed=123,
        min_trades=10,
    )

    assert stressed["risk_metrics"]["mean_final_equity"] < no_cost["risk_metrics"]["mean_final_equity"]
    assert stressed["stress_metrics"]["stress_fee_bps"] == 20


def test_monte_carlo_marks_small_sample_warning() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(8),
        strict=False,
        min_trades=30,
        report_path=None,
        initial_capital=1000,
        stake=100,
        leverage=1,
        simulations=150,
        horizon_trades=20,
    )

    assert report["sample_warning"] is True
    assert report["status"] in {"insufficient_data", "blocked"}


def test_monte_carlo_blocks_excessive_ruin_or_drawdown() -> None:
    report = run_monte_carlo_risk_simulation_frame(
        frame=bad_returns_frame(),
        ruin_threshold_pct=5,
        max_acceptable_drawdown_pct=5,
        **default_kwargs(),
    )

    assert report["recommendation_status"] == "blocked"
    assert report["status"] == "blocked"


def test_cli_run_monte_carlo_runs_successfully(tmp_path: Path) -> None:
    source = tmp_path / "outcomes.jsonl"
    report_path = tmp_path / "report.json"
    write_jsonl(source, returns_frame().to_dict(orient="records"))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_monte_carlo_risk_simulation.py",
            "--input",
            str(source),
            "--report",
            str(report_path),
            "--initial-capital",
            "1000",
            "--stake",
            "100",
            "--simulations",
            "150",
            "--horizon-trades",
            "20",
            "--min-trades",
            "10",
            "--seed",
            "123",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["risk_metrics"]["simulations"] == 150
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master = tmp_path / "trades_master.xlsx"
    training_dataset.write_bytes(b"training")
    trades_master.write_bytes(b"master")

    report = run_monte_carlo_risk_simulation_frame(
        frame=returns_frame(),
        report_path=tmp_path / "report.json",
        **{key: value for key, value in default_kwargs().items() if key != "report_path"},
    )

    assert report["input_rows"] == 40
    assert training_dataset.read_bytes() == b"training"
    assert trades_master.read_bytes() == b"master"


def test_does_not_touch_registry_models_signal_producer_or_freqtrade(tmp_path: Path) -> None:
    registry = tmp_path / "model_registry.json"
    model = tmp_path / "model.joblib"
    signals = tmp_path / "active_freqtrade_signals.json"
    freqtrade_db = tmp_path / "tradesv3.paper.sqlite"
    registry.write_text('{"registry": true}', encoding="utf-8")
    model.write_bytes(b"model")
    signals.write_text('{"signals":[]}', encoding="utf-8")
    freqtrade_db.write_bytes(b"sqlite")

    report = run_monte_carlo_risk_simulation_frame(frame=returns_frame(), **default_kwargs())

    assert report["registry_updated"] is False
    assert report["signal_producer_updated"] is False
    assert report["model_updated"] is False
    assert report["risk_manager_updated"] is False
    assert report["freqtrade_db_touched"] is False
    assert registry.read_text(encoding="utf-8") == '{"registry": true}'
    assert model.read_bytes() == b"model"
    assert signals.read_text(encoding="utf-8") == '{"signals":[]}'
    assert freqtrade_db.read_bytes() == b"sqlite"
