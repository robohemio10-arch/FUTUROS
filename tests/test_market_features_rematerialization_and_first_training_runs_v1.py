from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.market_features_first_training_runs.contracts import (
    FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    MODEL_NAMES,
    PipelineConfig,
    resolve_paths,
)
from smartcrypto.research.market_features_first_training_runs.pipeline import (
    attach_point_in_time_5m,
    reconcile_master_rows,
    run_market_features_first_training_pipeline,
)
from smartcrypto.research.market_features_first_training_runs.training import (
    block_monte_carlo,
    build_purged_walkforward_splits,
    max_drawdown,
    rank_models,
    run_supervised_models,
)
from smartcrypto.research.market_features_first_training_runs.validation import (
    forbidden_feature_columns,
    normalize_5m_features,
    normalize_master,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "run_market_features_rematerialization_and_first_training_runs_v1.py"


def feature_frame(*, periods: int = 500, gap_position: int | None = None) -> pd.DataFrame:
    timestamps = pd.date_range("2025-12-31", periods=periods, freq="5min", tz="UTC")
    if gap_position is not None:
        timestamps = timestamps.delete(gap_position)
    rows: list[dict[str, object]] = []
    for symbol_offset, symbol in enumerate(("BTCUSDT", "ETHUSDT")):
        for index, timestamp in enumerate(timestamps):
            price = 100.0 + index * 0.05 + symbol_offset
            row: dict[str, object] = {
                "symbol": symbol,
                "tf": "5m",
                "ts": timestamp,
                "open": price,
                "high": price + 0.08,
                "low": price - 0.06,
                "close": price + (0.02 if index % 2 == 0 else -0.01),
                "volume": 10.0 + (index % 17),
            }
            for feature_offset, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(index + feature_offset + symbol_offset + 1) / 100.0
            rows.append(row)
    return pd.DataFrame(rows)


def master_frame(*, rows: int = 100) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01 01:00", periods=rows, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "moeda": np.where(np.arange(rows) % 2 == 0, "BTC_USDT", "ETH_USDT"),
            "fechar_side": np.where(np.arange(rows) % 3 == 0, "Fechar Short", "Fechar Long"),
            "order_id": [f"master-{index}" for index in range(rows)],
            "pnl_fechado": [f"{1.0 if index % 2 == 0 else -0.75} USDT" for index in range(rows)],
            "horario_abertura": timestamps.astype(str),
            "horario_fechamento": (timestamps + pd.Timedelta(minutes=2)).astype(str),
            "taxa_1": ["-0.10 USDT"] * rows,
            "taxa_2": ["-0.05 USDT"] * rows,
        }
    )


def ready_training_frame(*, rows: int = 100) -> pd.DataFrame:
    normalized, _ = normalize_master(master_frame(rows=rows))
    features = normalize_5m_features(feature_frame(periods=700))
    aligned, _ = attach_point_in_time_5m(normalized, features, "master")
    return aligned


def materialized_project(tmp_path: Path, *, rows: int = 100) -> Path:
    project = tmp_path / "project"
    (project / "data" / "trades").mkdir(parents=True)
    (project / "data" / "features").mkdir(parents=True)
    master_frame(rows=rows).to_parquet(project / "data" / "trades" / "trades_master.parquet")
    feature_frame(periods=700).to_parquet(
        project / "data" / "features" / "market_features_60d.parquet"
    )
    return project


def test_default_is_no_write_and_research_only(tmp_path: Path) -> None:
    project = materialized_project(tmp_path)
    result = run_market_features_first_training_pipeline(
        resolve_paths(project),
        PipelineConfig(rematerialize_features=True),
    )
    assert result.report["write_requested"] is False
    assert result.report["write_performed"] is False
    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["operational_authority"] is False
    assert not (project / "data" / "research").exists()


def test_available_at_is_candle_timestamp_plus_five_minutes() -> None:
    normalized = normalize_5m_features(feature_frame(periods=2))
    delta = normalized["available_at_utc"] - normalized["candle_timestamp_utc"]
    assert set(delta.dt.total_seconds()) == {300.0}


def test_point_in_time_never_uses_in_progress_candle() -> None:
    trade, _ = normalize_master(master_frame(rows=1))
    features = normalize_5m_features(feature_frame(periods=400))
    aligned, _ = attach_point_in_time_5m(trade, features, "master")
    assert aligned.loc[0, "feature_available_at_utc"] <= aligned.loc[0, "open_time_utc"]
    assert aligned.loc[0, "feature_timestamp_utc"] == pd.Timestamp("2026-01-01 00:55:00Z")


def test_gap_is_blocked_without_forward_fill() -> None:
    raw = master_frame(rows=1)
    raw.loc[0, "horario_abertura"] = "2026-01-01 01:05:00+00:00"
    raw.loc[0, "horario_fechamento"] = "2026-01-01 01:07:00+00:00"
    trade, _ = normalize_master(raw)
    features = feature_frame(periods=400)
    features = features.loc[features["ts"] != pd.Timestamp("2026-01-01 01:00:00Z")]
    aligned, blockers = attach_point_in_time_5m(
        trade,
        normalize_5m_features(features),
        "master",
    )
    assert aligned.loc[0, "row_status"] == "blocked"
    assert any(item["reason"] == "five_minute_candle_gap_no_forward_fill" for item in blockers)


def test_missing_feature_is_blocked_without_imputation() -> None:
    trade, _ = normalize_master(master_frame(rows=1))
    features = feature_frame(periods=400)
    features.loc[features["symbol"] == "BTCUSDT", "volume"] = 1.0
    aligned, blockers = attach_point_in_time_5m(
        trade,
        normalize_5m_features(features),
        "master",
    )
    assert aligned.loc[0, "row_status"] == "blocked"
    assert any(item["reason"] == "missing_numeric_5m_features_no_imputation" for item in blockers)


@pytest.mark.parametrize(
    "column",
    ["future_ret_1", "target_win", "pnl_fechado", "mfe_pct", "close_time", "exit_reason"],
)
def test_output_and_lookahead_fields_are_forbidden_features(column: str) -> None:
    assert column in forbidden_feature_columns([column])
    assert column not in MODEL_FEATURE_COLUMNS


def test_expected_3504_and_canonical_3562_are_explicitly_reconciled() -> None:
    result = reconcile_master_rows(canonical_rows=3562, expected_rows=3504)
    assert result["row_count_delta"] == 58
    assert result["all_canonical_rows_retained"] is True
    assert result["silently_discarded_row_count"] == 0
    assert result["unresolved_delta_row_count"] == 58


def test_invalid_master_rows_have_individual_blockers() -> None:
    frame = master_frame(rows=2)
    frame.loc[1, "horario_abertura"] = None
    normalized, blockers = normalize_master(frame)
    assert len(normalized) == 2
    assert normalized.loc[1, "row_status"] == "blocked"
    assert {item["reason"] for item in blockers} == {"invalid_open_time"}
    assert blockers[0]["source_row_number"] == 1


def test_purged_walkforward_enforces_close_before_embargo_cutoff() -> None:
    frame = ready_training_frame(rows=100)
    splits = build_purged_walkforward_splits(frame, embargo_seconds=300, max_folds=2)
    assert splits
    for split in splits:
        cutoff = pd.Timestamp(split["embargo_cutoff_utc"])
        assert (frame.loc[split["train_indices"], "close_time_utc"] < cutoff).all()


def test_four_approved_models_train_without_imputation() -> None:
    result = run_supervised_models(
        ready_training_frame(rows=100),
        seed=42,
        embargo_seconds=300,
        run_walkforward=True,
    )
    assert {item["model_name"] for item in result.model_summaries} == set(MODEL_NAMES)
    assert set(result.fitted_models) == set(MODEL_NAMES)
    assert not result.predictions.empty


def test_ranking_never_promotes() -> None:
    summaries = (
        {
            "model_name": "a",
            "net_pnl": 10.0,
            "profit_factor": 2.0,
            "expectancy": 1.0,
            "max_drawdown": 2.0,
            "stability_std_net_pnl": 1.0,
        },
        {
            "model_name": "b",
            "net_pnl": 5.0,
            "profit_factor": 1.2,
            "expectancy": 0.5,
            "max_drawdown": 4.0,
            "stability_std_net_pnl": 3.0,
        },
    )
    ranked = rank_models(summaries)
    assert ranked[0]["model_name"] == "a"
    assert all(item["promotion_eligible"] is False for item in ranked)


def test_drawdown_is_deterministic() -> None:
    pnl = np.array([2.0, -1.0, -3.0, 2.0])
    assert max_drawdown(pnl) == 4.0


def test_block_monte_carlo_is_deterministic_and_uses_blocks() -> None:
    predictions = pd.DataFrame(
        {
            "trade_id": [f"t{i}" for i in range(30)],
            "open_time_utc": pd.date_range("2026-01-01", periods=30, freq="5min", tz="UTC"),
            "model_name": ["logistic_regression"] * 30,
            "strategy_net_pnl": np.tile([1.0, -0.5, 0.25], 10),
        }
    )
    first = block_monte_carlo(predictions, iterations=50, block_size=5, seed=42)
    second = block_monte_carlo(predictions, iterations=50, block_size=5, seed=42)
    assert first == second
    assert first[0]["method"] == "contiguous_block_bootstrap"


def test_write_flag_materializes_only_research_and_reports(tmp_path: Path) -> None:
    project = materialized_project(tmp_path)
    result = run_market_features_first_training_pipeline(
        resolve_paths(project),
        PipelineConfig(
            rematerialize_features=True,
            run_baselines=True,
            write_research_artifacts=True,
            expected_master_rows=100,
        ),
    )
    assert result.report["write_performed"] is True
    written = [Path(item) for item in result.report["outputs_written"]]
    assert written
    assert all((project / "data") in path.parents for path in written)
    assert not any("runtime" in path.parts or "registries" in path.parts for path in written)


def test_cli_no_write_json_executes_with_required_contract(tmp_path: Path) -> None:
    project = materialized_project(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(project),
            "--rematerialize-features",
            "--run-baselines",
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is False
    assert payload["one_minute_gate"]["status"] == "blocked"
    assert payload["five_minute_contract"]["forward_fill_across_gaps"] is False
    assert payload["sends_orders"] is False
