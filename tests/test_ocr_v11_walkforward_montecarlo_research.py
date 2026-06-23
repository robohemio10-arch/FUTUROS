from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.research.walkforward_montecarlo import (
    WalkForwardMonteCarloConfig,
    financial_metrics,
    max_drawdown_abs,
    resolve_paths,
    run_walkforward_montecarlo,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def outcome_frame(rows: int = 60, *, candidate_edge: float = 1.0) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min")
    original = [1.0 if idx % 3 else -0.8 for idx in range(rows)]
    simulated = [(value * candidate_edge) for value in original]
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx:04d}" for idx in range(rows)],
            "symbol": ["BTCUSDT" if idx % 2 == 0 else "ETHUSDT" for idx in range(rows)],
            "side": ["long" if idx % 2 == 0 else "short" for idx in range(rows)],
            "open_time": timestamps,
            "close_time": timestamps + pd.Timedelta(minutes=1),
            "original_net_pnl": original,
            "original_is_win": [value > 0 for value in original],
            "simulation_status": ["ok"] * rows,
            "simulated_net_pnl": simulated,
            "simulated_is_win": [value > 0 for value in simulated],
        }
    )


def blocked_outcome_frame(rows: int = 20) -> pd.DataFrame:
    frame = outcome_frame(rows)
    frame["simulation_status"] = "blocked"
    return frame


def test_financial_metrics_are_deterministic() -> None:
    metrics = financial_metrics([10.0, -5.0, 2.5])

    assert metrics["trades"] == 3
    assert metrics["net_pnl"] == 7.5
    assert metrics["gross_profit"] == 12.5
    assert metrics["gross_loss"] == 5.0
    assert metrics["profit_factor"] == 2.5


def test_max_drawdown_abs() -> None:
    assert max_drawdown_abs([10.0, -3.0, -4.0, 2.0]) == 7.0


def test_missing_trade_outcomes_blocks(tmp_path: Path) -> None:
    paths = resolve_paths(
        tmp_path,
        trade_outcomes_path=tmp_path / "missing.parquet",
        report_path=tmp_path / "report.json",
    )
    result = run_walkforward_montecarlo(paths, WalkForwardMonteCarloConfig())

    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "missing_trade_outcomes"


def test_no_write_is_default_behavior(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(60, candidate_edge=1.2).to_parquet(outcomes, index=False)

    paths = resolve_paths(
        tmp_path,
        trade_outcomes_path=outcomes,
        walkforward_output_path=tmp_path / "wf.parquet",
        monte_carlo_output_path=tmp_path / "mc.parquet",
        report_path=tmp_path / "report.json",
        executive_report_path=tmp_path / "exec.md",
        summary_path=tmp_path / "summary.json",
    )
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=2,
        max_folds=2,
        monte_carlo_iterations=20,
        seed=123,
        block_size=4,
    )

    result = run_walkforward_montecarlo(paths, config, write=False)

    assert result.report["write_performed"] is False
    assert not paths.walkforward_output_path.exists()
    assert not paths.monte_carlo_output_path.exists()
    assert not paths.report_path.exists()


def test_write_materializes_outputs(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(60, candidate_edge=1.2).to_parquet(outcomes, index=False)

    paths = resolve_paths(
        tmp_path,
        trade_outcomes_path=outcomes,
        walkforward_output_path=tmp_path / "wf.parquet",
        monte_carlo_output_path=tmp_path / "mc.parquet",
        report_path=tmp_path / "report.json",
        executive_report_path=tmp_path / "exec.md",
        summary_path=tmp_path / "summary.json",
    )
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=2,
        max_folds=2,
        monte_carlo_iterations=20,
        seed=123,
        block_size=4,
    )

    result = run_walkforward_montecarlo(paths, config, write=True)

    assert result.report["write_performed"] is True
    assert paths.walkforward_output_path.exists()
    assert paths.monte_carlo_output_path.exists()
    assert paths.report_path.exists()
    assert paths.executive_report_path.exists()
    assert paths.summary_path.exists()


def test_walkforward_uses_embargo_rows(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(70, candidate_edge=1.2).to_parquet(outcomes, index=False)

    paths = resolve_paths(tmp_path, trade_outcomes_path=outcomes)
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=3,
        max_folds=2,
        monte_carlo_iterations=10,
        seed=42,
    )

    result = run_walkforward_montecarlo(paths, config)

    assert not result.walkforward.empty
    assert set(result.walkforward["embargo_rows"]) == {3}
    assert set(result.walkforward["purged_rows"]) == {3}


def test_candidate_underperformance_blocks_promotion(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(70, candidate_edge=0.5).to_parquet(outcomes, index=False)

    paths = resolve_paths(tmp_path, trade_outcomes_path=outcomes)
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=2,
        max_folds=2,
        monte_carlo_iterations=20,
        seed=42,
    )

    result = run_walkforward_montecarlo(paths, config)

    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "candidate_does_not_beat_original_walkforward"
    assert result.report["decision"] == "DESCARTAR_CANDIDATO"


def test_monte_carlo_is_reproducible_with_seed(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(70, candidate_edge=1.2).to_parquet(outcomes, index=False)

    paths = resolve_paths(tmp_path, trade_outcomes_path=outcomes)
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=2,
        max_folds=2,
        monte_carlo_iterations=20,
        seed=99,
    )

    first = run_walkforward_montecarlo(paths, config)
    second = run_walkforward_montecarlo(paths, config)

    assert first.report["monte_carlo"] == second.report["monte_carlo"]


def test_safety_flags_are_preserved(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(60, candidate_edge=1.1).to_parquet(outcomes, index=False)

    paths = resolve_paths(tmp_path, trade_outcomes_path=outcomes)
    config = WalkForwardMonteCarloConfig(
        min_train_rows=20,
        test_rows=10,
        embargo_rows=2,
        max_folds=1,
        monte_carlo_iterations=10,
    )
    result = run_walkforward_montecarlo(paths, config)

    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["live_trading_enabled"] is False
    assert result.report["sends_orders"] is False
    assert result.report["changes_risk"] is False
    assert result.report["changes_model"] is False
    assert result.report["runs_training"] is False
    assert result.report["updates_freqtrade"] is False
    assert result.report["updates_qlib_runtime"] is False


def test_all_blocked_rows_prevent_walkforward(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    blocked_outcome_frame(40).to_parquet(outcomes, index=False)

    paths = resolve_paths(tmp_path, trade_outcomes_path=outcomes)
    config = WalkForwardMonteCarloConfig(
        min_train_rows=10,
        test_rows=5,
        embargo_rows=1,
        max_folds=1,
        monte_carlo_iterations=10,
    )
    result = run_walkforward_montecarlo(paths, config)

    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "insufficient_eligible_rows"


def test_cli_runs_with_json_no_write(tmp_path: Path) -> None:
    outcomes = tmp_path / "outcomes.parquet"
    outcome_frame(60, candidate_edge=1.2).to_parquet(outcomes, index=False)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_ocr_v11_walkforward_montecarlo_research.py",
            "--project-root",
            str(tmp_path),
            "--trade-outcomes-path",
            str(outcomes),
            "--min-train-rows",
            "20",
            "--test-rows",
            "10",
            "--embargo-rows",
            "2",
            "--max-folds",
            "2",
            "--monte-carlo-iterations",
            "20",
            "--json",
            "--no-write",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["write_performed"] is False
    assert payload["walkforward_folds"] == 2
