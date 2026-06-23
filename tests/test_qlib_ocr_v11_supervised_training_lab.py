from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.research.qlib_ocr_v11_supervised_training import (
    SupervisedTrainingConfig,
    financial_metrics,
    resolve_paths,
    run_supervised_training_lab,
    select_feature_columns,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def research_frame(rows: int = 90) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min")
    edge = [1 if idx % 4 in {1, 2, 3} else 0 for idx in range(rows)]
    pnl = [2.0 if value else -1.5 for value in edge]
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx:04d}" for idx in range(rows)],
            "symbol": ["BTCUSDT" if idx % 2 == 0 else "ETHUSDT" for idx in range(rows)],
            "side": ["long" if idx % 2 == 0 else "short" for idx in range(rows)],
            "open_time": timestamps,
            "close_time": timestamps + pd.Timedelta(minutes=1),
            "feature_momentum": [float(value) for value in edge],
            "feature_volatility": [float((idx % 7) / 10) for idx in range(rows)],
            "feature_spread": [float((idx % 5) / 100) for idx in range(rows)],
            "entry_return_1m": [float((idx % 9) / 100) for idx in range(rows)],
            "entry_rsi": [float(40 + (idx % 20)) for idx in range(rows)],
            "entry_candle_found": [1] * rows,
            "is_win": edge,
            "duration_seconds": [60.0] * rows,
            "exit_candle_found": [1] * rows,
            "max_favorable_price": [100.0 + idx for idx in range(rows)],
            "pnl_should_be_excluded": pnl,
        }
    )


def outcomes_frame(rows: int = 90) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx:04d}" for idx in range(rows)],
            "original_net_pnl": [2.0 if idx % 4 in {1, 2, 3} else -1.5 for idx in range(rows)],
            "original_is_win": [1 if idx % 4 in {1, 2, 3} else 0 for idx in range(rows)],
            "simulated_net_pnl": [-1.0] * rows,
            "simulation_status": ["ok"] * rows,
            "strategy_id": ["fixed_tp_20_sl_200"] * rows,
        }
    )


def write_inputs(tmp_path: Path, rows: int = 90) -> tuple[Path, Path, Path]:
    research_path = tmp_path / "research.parquet"
    outcomes_path = tmp_path / "outcomes.parquet"
    branch03_path = tmp_path / "branch03.json"
    research_frame(rows).to_parquet(research_path, index=False)
    outcomes_frame(rows).to_parquet(outcomes_path, index=False)
    branch03_path.write_text(
        json.dumps(
            {
                "status": "blocked",
                "decision": "DESCARTAR_CANDIDATO",
                "monte_carlo": {"risk_of_ruin": 1.0},
            }
        ),
        encoding="utf-8",
    )
    return research_path, outcomes_path, branch03_path


def training_config() -> SupervisedTrainingConfig:
    return SupervisedTrainingConfig(
        min_rows=60,
        folds=3,
        embargo_seconds=60,
        selector_quantile=0.60,
        min_selected_rows=3,
        model_family="random_forest",
    )


def test_financial_metrics() -> None:
    metrics = financial_metrics(pd.Series([2.0, -1.0, 3.0]))

    assert metrics["rows"] == 3
    assert metrics["net_pnl"] == 4.0
    assert metrics["profit_factor"] == 5.0


def test_select_feature_columns_excludes_leakage_and_post_event_columns() -> None:
    features, excluded = select_feature_columns(research_frame())

    assert "feature_momentum" in features
    assert "entry_return_1m" in features
    assert "entry_rsi" in features
    assert "entry_candle_found" in features

    assert "is_win" in excluded
    assert "duration_seconds" in excluded
    assert "exit_candle_found" in excluded
    assert "max_favorable_price" in excluded
    assert "pnl_should_be_excluded" in excluded

    assert "is_win" not in features
    assert "duration_seconds" not in features
    assert "exit_candle_found" not in features
    assert "max_favorable_price" not in features
    assert "pnl_should_be_excluded" not in features


def test_missing_research_dataset_blocks(tmp_path: Path) -> None:
    _, outcomes_path, branch03_path = write_inputs(tmp_path)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=tmp_path / "missing.parquet",
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
    )

    result = run_supervised_training_lab(paths, SupervisedTrainingConfig())

    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "missing_research_dataset"


def test_missing_outcomes_blocks(tmp_path: Path) -> None:
    research_path, _, branch03_path = write_inputs(tmp_path)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=tmp_path / "missing.parquet",
        walkforward_report_path=branch03_path,
    )

    result = run_supervised_training_lab(paths, SupervisedTrainingConfig())

    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "missing_trade_outcomes"


def test_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
        prediction_output_path=tmp_path / "pred.parquet",
        model_output_path=tmp_path / "model.joblib",
        report_path=tmp_path / "report.json",
        executive_report_path=tmp_path / "exec.md",
        summary_path=tmp_path / "summary.json",
    )

    result = run_supervised_training_lab(paths, training_config(), write=False)

    assert result.report["write_performed"] is False
    assert result.report["prediction_rows"] > 0
    assert not paths.prediction_output_path.exists()
    assert not paths.model_output_path.exists()
    assert not paths.report_path.exists()


def test_write_materializes_research_outputs(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
        prediction_output_path=tmp_path / "pred.parquet",
        model_output_path=tmp_path / "model.joblib",
        report_path=tmp_path / "report.json",
        executive_report_path=tmp_path / "exec.md",
        summary_path=tmp_path / "summary.json",
    )

    result = run_supervised_training_lab(paths, training_config(), write=True)

    assert result.report["write_performed"] is True
    assert paths.prediction_output_path.exists()
    assert paths.model_output_path.exists()
    assert paths.report_path.exists()
    assert paths.executive_report_path.exists()
    assert paths.summary_path.exists()


def test_temporal_training_preserves_safety_flags(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
    )

    result = run_supervised_training_lab(paths, training_config())

    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["live_trading_enabled"] is False
    assert result.report["sends_orders"] is False
    assert result.report["changes_model"] is False
    assert result.report["registers_model"] is False
    assert result.report["updates_qlib_runtime"] is False
    assert result.report["production_enabled"] is False


def test_branch03_discarded_candidate_is_metadata_not_target(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
    )

    result = run_supervised_training_lab(paths, training_config())

    assert result.report["branch03_gate_decision"] == "DESCARTAR_CANDIDATO"
    assert "simulated_net_pnl" in result.report["excluded_leakage_columns"]


def test_suspicious_perfect_metrics_block_candidate(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)
    paths = resolve_paths(
        tmp_path,
        research_dataset_path=research_path,
        trade_outcomes_path=outcomes_path,
        walkforward_report_path=branch03_path,
    )

    result = run_supervised_training_lab(paths, training_config())

    assert result.report["suspicious_perfect_metrics"] is True
    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "suspicious_perfect_metrics_possible_leakage"
    assert result.report["decision"] == "BLOQUEAR_CANDIDATO"


def test_cli_runs_json_no_write(tmp_path: Path) -> None:
    research_path, outcomes_path, branch03_path = write_inputs(tmp_path, rows=120)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_qlib_ocr_v11_supervised_training_lab.py",
            "--project-root",
            str(tmp_path),
            "--research-dataset-path",
            str(research_path),
            "--trade-outcomes-path",
            str(outcomes_path),
            "--walkforward-report-path",
            str(branch03_path),
            "--min-rows",
            "60",
            "--folds",
            "3",
            "--embargo-seconds",
            "60",
            "--selector-quantile",
            "0.60",
            "--min-selected-rows",
            "3",
            "--model-family",
            "random_forest",
            "--no-write",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["write_performed"] is False
    assert payload["prediction_rows"] > 0
