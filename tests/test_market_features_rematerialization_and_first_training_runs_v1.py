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
    PAPER_V1_WATERMARK_UTC,
    PipelineConfig,
    RuntimeEnvironment,
    canonical_environment,
    resolve_paths,
)
from smartcrypto.research.market_features_first_training_runs.pipeline import (
    attach_point_in_time_5m,
    mark_paper_evaluation_sets,
    reconcile_master_rows,
    run_market_features_first_training_pipeline,
)
from smartcrypto.research.market_features_first_training_runs.training import (
    aggregate_fold_matched_financial,
    block_monte_carlo,
    build_candidate_rankings,
    build_fold_contribution_report,
    build_purged_walkforward_splits,
    financial_invariant_errors,
    max_drawdown,
    run_cohort_aware_experiments,
    run_supervised_models,
    select_expected_pnl_threshold,
)
from smartcrypto.research.market_features_first_training_runs.validation import (
    binary_drift_metrics,
    build_concept_drift_report,
    categorical_drift_metrics,
    evaluate_canonical_environment,
    forbidden_feature_columns,
    normalize_5m_features,
    normalize_master,
    population_stability_index,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "run_market_features_rematerialization_and_first_training_runs_v1.py"


def feature_frame(*, periods: int = 800) -> pd.DataFrame:
    timestamps = pd.date_range("2025-12-31", periods=periods, freq="5min", tz="UTC")
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
            for offset, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(index + offset + 1) / 100.0
            rows.append(row)
    return pd.DataFrame(rows)


def master_frame(*, rows: int = 120) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01 01:00", periods=rows, freq="5min", tz="UTC")
    tail_start = max(0, rows - 20)
    return pd.DataFrame(
        {
            "moeda": np.where(np.arange(rows) % 2 == 0, "BTC_USDT", "ETH_USDT"),
            "fechar_side": np.where(
                np.arange(rows) % 3 == 0, "Fechar Short", "Fechar Long"
            ),
            "order_id": [f"master-{index}" for index in range(rows)],
            "pnl_fechado": [
                f"{1.0 if index % 2 == 0 else -0.75} USDT" for index in range(rows)
            ],
            "horario_abertura": timestamps.astype(str),
            "horario_fechamento": (timestamps + pd.Timedelta(minutes=2)).astype(str),
            "taxa_1": ["-0.10 USDT"] * rows,
            "taxa_2": ["-0.05 USDT"] * rows,
            "source_file": [
                "historical_non_ocr"
                if index < tail_start
                else "bitradex_20260714_synthetic_v5"
                for index in range(rows)
            ],
            "ocr_source": [
                None if index < tail_start else "bitradex_ocr_v2"
                for index in range(rows)
            ],
        }
    )


def ready_training_frame(*, rows: int = 120) -> pd.DataFrame:
    normalized, _ = normalize_master(master_frame(rows=rows))
    aligned, _ = attach_point_in_time_5m(
        normalized, normalize_5m_features(feature_frame()), "master"
    )
    return aligned


def materialized_project(tmp_path: Path, *, rows: int = 120) -> Path:
    project = tmp_path / "project"
    (project / "data" / "trades").mkdir(parents=True)
    (project / "data" / "features").mkdir(parents=True)
    master_frame(rows=rows).to_parquet(
        project / "data" / "trades" / "trades_master.parquet"
    )
    feature_frame().to_parquet(
        project / "data" / "features" / "market_features_60d.parquet"
    )
    return project


def canonical_config(**overrides: object) -> PipelineConfig:
    values: dict[str, object] = {
        "rematerialize_features": True,
        "environment_override": canonical_environment(),
    }
    values.update(overrides)
    return PipelineConfig(**values)


def test_environment_gate_requires_exact_canonical_versions() -> None:
    report = evaluate_canonical_environment(canonical_environment())
    assert report["status"] == "ok"
    assert report["training_allowed"] is True
    assert report["expected"] == {
        "python": "3.11.15",
        "scikit_learn": "1.8.0",
        "joblib": "1.5.3",
    }


def test_environment_mismatch_allows_diagnostics_but_blocks_financial_execution() -> None:
    report = evaluate_canonical_environment(RuntimeEnvironment("3.12.10", "1.7.0", "1.5.1"))
    assert report["status"] == "blocked"
    assert report["diagnostics_allowed"] is True
    assert report["training_allowed"] is False
    assert report["backtest_allowed"] is False
    assert report["monte_carlo_allowed"] is False


def test_pipeline_mismatch_rematerializes_but_does_not_train(tmp_path: Path) -> None:
    project = materialized_project(tmp_path)
    result = run_market_features_first_training_pipeline(
        resolve_paths(project),
        PipelineConfig(
            rematerialize_features=True,
            run_baselines=True,
            run_supervised_training=True,
            run_backtest=True,
            run_monte_carlo=True,
            environment_override=RuntimeEnvironment("3.12.10", "1.7.0", "1.5.1"),
        ),
    )
    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "canonical_training_environment_mismatch"
    assert result.report["master_ready_row_count"] > 0
    assert result.report["baselines"] == []
    assert result.report["supervised_training"]["performed"] is False
    assert result.report["backtest"]["performed"] is False
    assert result.report["monte_carlo"]["performed"] is False


def test_default_is_no_write_and_research_only(tmp_path: Path) -> None:
    result = run_market_features_first_training_pipeline(
        resolve_paths(materialized_project(tmp_path)), canonical_config()
    )
    assert result.report["write_performed"] is False
    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["operational_authority"] is False
    assert result.report["runtime"] is False
    assert result.report["risk"] is False
    assert result.report["orders"] is False


def test_available_at_is_candle_timestamp_plus_five_minutes() -> None:
    normalized = normalize_5m_features(feature_frame(periods=2))
    delta = normalized["available_at_utc"] - normalized["candle_timestamp_utc"]
    assert set(delta.dt.total_seconds()) == {300.0}


def test_point_in_time_never_uses_in_progress_candle() -> None:
    trade, _ = normalize_master(master_frame(rows=1))
    aligned, _ = attach_point_in_time_5m(
        trade, normalize_5m_features(feature_frame()), "master"
    )
    assert aligned.loc[0, "feature_available_at_utc"] <= aligned.loc[0, "open_time_utc"]


def test_gap_is_blocked_without_forward_fill() -> None:
    raw = master_frame(rows=1)
    raw.loc[0, "horario_abertura"] = "2026-01-01 01:05:00+00:00"
    raw.loc[0, "horario_fechamento"] = "2026-01-01 01:07:00+00:00"
    trade, _ = normalize_master(raw)
    features = feature_frame()
    features = features.loc[features["ts"] != pd.Timestamp("2026-01-01 01:00:00Z")]
    aligned, blockers = attach_point_in_time_5m(
        trade, normalize_5m_features(features), "master"
    )
    assert aligned.loc[0, "row_status"] == "blocked"
    assert any(
        item["reason"] == "five_minute_candle_gap_no_forward_fill"
        for item in blockers
    )


def test_known_entry_fields_are_materialized_and_numeric_features_are_finite() -> None:
    frame = ready_training_frame(rows=4)
    row = frame.loc[frame["row_status"].eq("ready")].iloc[0]
    for field in (
        "symbol",
        "side",
        "entry_hour_utc",
        "entry_day_of_week",
        "feature_age_seconds",
        "market_regime",
        "volatility_regime",
    ):
        assert field in frame.columns
        assert pd.notna(row[field])
    assert np.isfinite(row.loc[list(MODEL_FEATURE_COLUMNS)].astype(float)).all()


@pytest.mark.parametrize(
    "column",
    [
        "future_ret_1",
        "target_win",
        "pnl_fechado",
        "mfe_pct",
        "close_time",
        "exit_reason",
        "provenance",
        "source_file",
    ],
)
def test_output_and_provenance_fields_are_forbidden_features(column: str) -> None:
    assert column in forbidden_feature_columns([column])
    assert column not in MODEL_FEATURE_COLUMNS


def test_source_provenance_metadata_does_not_block_market_source_normalization() -> None:
    features = feature_frame(periods=80)
    features["source_file"] = "public_5m_source"

    normalized = normalize_5m_features(features)

    assert not normalized.empty
    assert "source_file" not in normalized.columns


def test_expected_3504_and_canonical_3562_are_explicitly_reconciled() -> None:
    result = reconcile_master_rows(canonical_rows=3562, expected_rows=3504)
    assert result["row_count_delta"] == 58
    assert result["all_canonical_rows_retained"] is True
    assert result["silently_discarded_row_count"] == 0


def test_purged_walkforward_has_fit_validation_and_same_test_baseline() -> None:
    frame = ready_training_frame(rows=120)
    splits = build_purged_walkforward_splits(frame, embargo_seconds=300, max_folds=2)
    assert splits
    for split in splits:
        assert split["fit_indices"]
        assert split["validation_indices"]
        cutoff = pd.Timestamp(split["embargo_cutoff_utc"])
        assert (frame.loc[split["train_indices"], "close_time_utc"] < cutoff).all()


def test_eight_approved_models_train_and_regression_thresholds_use_validation() -> None:
    result = run_supervised_models(
        ready_training_frame(rows=120),
        seed=42,
        embargo_seconds=300,
        run_walkforward=True,
    )
    assert {item["model_name"] for item in result.model_summaries} == set(MODEL_NAMES)
    assert set(result.fitted_models) == set(MODEL_NAMES)
    regression = result.predictions.loc[result.predictions["model_kind"].eq("regressor")]
    assert set(regression["threshold_source"]) == {"master_fold_validation_only"}
    assert result.fold_baselines
    for baseline in result.fold_baselines:
        fold_predictions = result.predictions.loc[
            result.predictions["fold_id"].eq(baseline["fold_id"])
        ]
        assert set(baseline["test_trade_ids"]) == set(fold_predictions["trade_id"])


def test_fold_matched_financial_aggregation_preserves_temporal_accounting() -> None:
    aggregate = aggregate_fold_matched_financial(
        [
            {"fold_id": 1, "_pnl_sequence": [10.0, -8.0]},
            {"fold_id": 2, "_pnl_sequence": [2.0, -10.0]},
        ],
        sequence_key="_pnl_sequence",
    )

    assert aggregate["trade_count"] == 4
    assert aggregate["active_trade_count"] == 4
    assert aggregate["gross_profit"] == 12.0
    assert aggregate["gross_loss"] == 18.0
    assert aggregate["net_pnl"] == -6.0
    assert aggregate["expectancy"] == -1.5
    assert aggregate["profit_factor"] == pytest.approx(12.0 / 18.0)
    assert aggregate["max_drawdown"] == 16.0
    assert aggregate["fold_count"] == 2
    assert aggregate["financial_invariants_valid"] is True


@pytest.mark.parametrize(
    ("metrics", "expected_error"),
    [
        (
            {
                "trade_count": 2,
                "net_pnl": 4.0,
                "gross_profit": 5.0,
                "gross_loss": 2.0,
                "expectancy": 2.0,
                "profit_factor": 2.5,
            },
            "net_pnl_not_gross_profit_minus_gross_loss",
        ),
        (
            {
                "trade_count": 2,
                "net_pnl": 3.0,
                "gross_profit": 5.0,
                "gross_loss": 2.0,
                "expectancy": 3.0,
                "profit_factor": 2.5,
            },
            "expectancy_not_net_pnl_div_trade_count",
        ),
        (
            {
                "trade_count": 0,
                "net_pnl": 1.0,
                "gross_profit": 1.0,
                "gross_loss": 0.0,
                "expectancy": 0.0,
                "profit_factor": None,
            },
            "nonzero_net_pnl_with_zero_trade_count",
        ),
        (
            {
                "trade_count": 2,
                "net_pnl": 3.0,
                "gross_profit": 5.0,
                "gross_loss": 2.0,
                "expectancy": 1.5,
                "profit_factor": None,
            },
            "profit_factor_null_with_nonzero_gross_loss",
        ),
    ],
)
def test_financial_invariants_fail_closed(
    metrics: dict[str, object], expected_error: str
) -> None:
    assert expected_error in financial_invariant_errors(metrics)


def test_expected_pnl_threshold_is_selected_from_supplied_validation_only() -> None:
    predictions = np.array([-1.0, -0.2, 0.1, 0.5])
    pnl = np.array([-2.0, -1.0, 1.0, 3.0])
    threshold = select_expected_pnl_threshold(predictions, pnl)
    assert threshold in set(predictions) | {0.0}
    assert threshold > -0.2


def _summary(name: str, net: float, baseline_net: float) -> dict[str, object]:
    return {
        "model_name": name,
        "model_kind": "regressor",
        "fold_count": 3,
        "positive_fold_count": 2 if net > 0 else 0,
        "net_pnl": net,
        "profit_factor": 1.5 if net > 0 else 0.8,
        "expectancy": net / 30.0,
        "max_drawdown": 2.0,
        "stability_std_net_pnl": 1.0,
        "baseline_always_allow": {
            "net_pnl": baseline_net,
            "profit_factor": 1.2,
            "expectancy": baseline_net / 30.0,
            "max_drawdown": 3.0,
        },
    }


def test_negative_or_baseline_losing_models_have_no_selected_candidate() -> None:
    rankings = build_candidate_rankings(
        (_summary("loser", -1.0, 2.0),),
        [
            {
                "model_name": "loser",
                "net_pnl_median": -1.0,
                "probability_negative_net_pnl": 0.8,
            }
        ],
        maximum_negative_pnl_probability=0.2,
        leakage_detected=False,
        paper_rows_used_for_fit=0,
        paper_rows_used_for_threshold=0,
    )
    assert rankings["diagnostic_ranking"]
    assert rankings["eligible_candidate_ranking"] == []
    assert rankings["selected_candidate"] is None
    assert rankings["decision"] == "NO_ELIGIBLE_MODEL_CANDIDATE"


def test_candidate_requires_all_financial_and_monte_carlo_gates() -> None:
    rankings = build_candidate_rankings(
        (_summary("eligible", 10.0, 2.0),),
        [
            {
                "model_name": "eligible",
                "net_pnl_median": 8.0,
                "probability_negative_net_pnl": 0.1,
            }
        ],
        maximum_negative_pnl_probability=0.2,
        leakage_detected=False,
        paper_rows_used_for_fit=0,
        paper_rows_used_for_threshold=0,
    )
    assert rankings["selected_candidate"]["model_name"] == "eligible"
    assert rankings["decision"] == "ELIGIBLE_RESEARCH_CANDIDATE_IDENTIFIED"


def test_concept_drift_includes_required_metrics_and_decompositions() -> None:
    master = ready_training_frame(rows=120)
    master.loc[master.index[:60], "open_time_utc"] = pd.date_range(
        "2026-06-01", periods=60, freq="1h", tz="UTC"
    )
    master.loc[master.index[:60], "close_time_utc"] = (
        master.loc[master.index[:60], "open_time_utc"] + pd.Timedelta(minutes=2)
    )
    master.loc[master.index[60:], "open_time_utc"] = pd.date_range(
        "2026-06-11", periods=60, freq="1h", tz="UTC"
    )
    master.loc[master.index[60:], "close_time_utc"] = (
        master.loc[master.index[60:], "open_time_utc"] + pd.Timedelta(minutes=2)
    )
    paper = master.iloc[:20].copy()
    paper["provenance"] = "freqtrade_paper_snapshot"
    paper["close_time_utc"] = pd.Timestamp("2026-07-15T00:00:00Z")
    report = build_concept_drift_report(master, paper)
    temporal = next(
        item for item in report["comparisons"] if item["comparison_id"] == "master_temporal"
    )
    assert temporal["status"] == "ok"
    assert temporal["continuous_metrics"][0].keys() >= {
        "psi_quantile",
        "ks",
        "wasserstein",
    }
    assert temporal["binary_metrics"][0].keys() >= {
        "reference_prevalence",
        "target_prevalence",
        "psi_categorical",
        "jensen_shannon",
    }
    assert temporal["categorical_metrics"][0].keys() >= {
        "reference_distribution",
        "target_distribution",
        "jensen_shannon",
        "chi_square",
    }
    assert temporal["label_drift"]
    assert temporal["pnl_drift"]
    assert report["decomposition"].keys() == {"master", "paper_v1"}
    assert report["provenance_used_as_feature"] is False


def test_continuous_psi_does_not_invent_fixed_one_for_degenerate_data() -> None:
    result = population_stability_index(np.ones(20), np.full(20, 2.0))
    assert result is None


def test_binary_and_categorical_drift_use_type_appropriate_metrics() -> None:
    binary = binary_drift_metrics(
        "side_long",
        pd.Series([0] * 50 + [1] * 50),
        pd.Series([0] * 20 + [1] * 80),
    )
    categorical = categorical_drift_metrics(
        "symbol",
        pd.Series(["BTCUSDT"] * 60 + ["ETHUSDT"] * 40),
        pd.Series(["BTCUSDT"] * 30 + ["ETHUSDT"] * 70),
    )

    assert binary["feature_type"] == "binary"
    assert binary["target_prevalence"] == 0.8
    assert binary["jensen_shannon"] > 0
    assert categorical["feature_type"] == "categorical"
    assert categorical["chi_square"]["valid"] is True
    assert categorical["jensen_shannon"] > 0


def test_cohort_experiments_and_fold_three_attribution_are_master_only() -> None:
    frame = ready_training_frame(rows=220)
    first = frame.index[:120]
    tail = frame.index[120:]
    frame.loc[first, "open_time_utc"] = pd.date_range(
        "2026-05-01", periods=len(first), freq="1h", tz="UTC"
    )
    frame.loc[tail, "open_time_utc"] = pd.date_range(
        "2026-06-11", periods=len(tail), freq="1h", tz="UTC"
    )
    frame["close_time_utc"] = frame["open_time_utc"] + pd.Timedelta(minutes=2)
    frame.loc[first, "is_ocr_v2_tail"] = False
    frame.loc[first, "provenance"] = "historical_pre_v2"
    frame.loc[tail, "is_ocr_v2_tail"] = True
    frame.loc[tail, "provenance"] = "ocr_v2_tail"
    full = run_supervised_models(
        frame,
        seed=42,
        embargo_seconds=1800,
        run_walkforward=True,
        model_names=("logistic_regression",),
        fit_final_models=False,
    )

    experiments = run_cohort_aware_experiments(
        frame,
        full_population_result=full,
        seed=42,
        embargo_seconds=1800,
        model_names=("logistic_regression",),
    )
    fold_three = build_fold_contribution_report(full.predictions, fold_id=3)

    assert set(experiments["experiments"]) == {"E1", "E2", "E3", "E4", "E5", "E6"}
    assert experiments["paper_rows_used_for_fit"] == 0
    assert experiments["paper_rows_used_for_threshold"] == 0
    assert experiments["provenance_used_as_feature"] is False
    assert experiments["experiments"]["E4"]["always_allow_uses_same_test_rows"] is True
    assert experiments["experiments"]["E5"]["always_allow_uses_same_test_rows"] is True
    assert experiments["experiments"]["E6"]["selection_authority"] is False
    assert fold_three["status"] == "ok"
    assert set(fold_three["dimensions"]) == {
        "provenance",
        "week",
        "symbol",
        "side",
        "cutoff_period",
        "ocr_v2_cohort",
    }
    assert fold_three["baseline_fold_matched"] is True
    assert fold_three["paper_rows_used"] == 0


def test_paper_v1_is_consumed_and_newer_rows_become_prospective_v2() -> None:
    frame = ready_training_frame(rows=2)
    frame["row_status"] = "ready"
    watermark = pd.Timestamp(PAPER_V1_WATERMARK_UTC)
    frame.loc[frame.index[0], "close_time_utc"] = watermark
    frame.loc[frame.index[1], "close_time_utc"] = watermark + pd.Timedelta(seconds=1)
    marked = mark_paper_evaluation_sets(frame)
    assert marked.loc[frame.index[0], "paper_evaluation_set"] == "paper_evaluation_set_v1_consumed"
    assert marked.loc[frame.index[1], "paper_evaluation_set"] == "prospective_holdout_v2"


def test_block_monte_carlo_is_deterministic_and_uses_contiguous_blocks() -> None:
    predictions = pd.DataFrame(
        {
            "trade_id": [f"t{i}" for i in range(30)],
            "open_time_utc": pd.date_range(
                "2026-01-01", periods=30, freq="5min", tz="UTC"
            ),
            "model_name": ["huber_regressor"] * 30,
            "strategy_net_pnl": np.tile([1.0, -0.5, 0.25], 10),
        }
    )
    first = block_monte_carlo(predictions, iterations=50, block_size=5, seed=42)
    second = block_monte_carlo(predictions, iterations=50, block_size=5, seed=42)
    assert first == second
    assert first[0]["method"] == "contiguous_block_bootstrap"


def test_drawdown_is_deterministic() -> None:
    assert max_drawdown(np.array([2.0, -1.0, -3.0, 2.0])) == 4.0


def test_write_flag_materializes_only_research_and_reports(tmp_path: Path) -> None:
    project = materialized_project(tmp_path)
    result = run_market_features_first_training_pipeline(
        resolve_paths(project),
        canonical_config(
            run_baselines=True,
            write_research_artifacts=True,
            expected_master_rows=120,
        ),
    )
    assert result.report["write_performed"] is True
    written = [Path(item) for item in result.report["outputs_written"]]
    assert all((project / "data") in path.parents for path in written)
    assert not any("runtime" in path.parts or "registries" in path.parts for path in written)


def test_cli_current_noncanonical_environment_fails_closed_without_writes(
    tmp_path: Path,
) -> None:
    project = materialized_project(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(project),
            "--rematerialize-features",
            "--run-supervised-training",
            "--run-backtest",
            "--run-monte-carlo",
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["environment_gate"]["status"] == "blocked"
    assert payload["supervised_training"]["performed"] is False
    assert payload["backtest"]["performed"] is False
    assert payload["monte_carlo"]["performed"] is False
    assert payload["write_performed"] is False
    assert payload["selected_candidate"] is None
    assert payload["decision"] == "NO_ELIGIBLE_MODEL_CANDIDATE"
    assert payload["sends_orders"] is False
