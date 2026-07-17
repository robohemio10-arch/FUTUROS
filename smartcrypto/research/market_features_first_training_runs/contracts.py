"""Institutional contracts for 5m rematerialization and research training."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final


SCHEMA_VERSION: Final = "market_features_rematerialization_first_training_runs_v1"
RESEARCH_DECISION: Final = "MANTER_EM_RESEARCH"
NO_CANDIDATE_DECISION: Final = "NO_ELIGIBLE_MODEL_CANDIDATE"
ELIGIBLE_CANDIDATE_DECISION: Final = "ELIGIBLE_RESEARCH_CANDIDATE_IDENTIFIED"
EXPECTED_MASTER_ROWS: Final = 3504
TIMEFRAME: Final = "5m"
TIMEFRAME_SECONDS: Final = 300
EMBARGO_SECONDS: Final = 1800
RANDOM_SEED: Final = 42
DRIFT_CUTOFF_UTC: Final = "2026-06-10T00:00:00+00:00"
PAPER_V1_WATERMARK_UTC: Final = "2026-07-16T17:17:22.249000+00:00"

CANONICAL_PYTHON_VERSION: Final = "3.11.15"
CANONICAL_SKLEARN_VERSION: Final = "1.8.0"
CANONICAL_JOBLIB_VERSION: Final = "1.5.3"

DEFAULT_MASTER: Final = Path("data/trades/trades_master.parquet")
DEFAULT_MARKET_FEATURES: Final = Path("data/features/market_features_60d.parquet")
DEFAULT_PAPER_SNAPSHOT: Final = Path(
    "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)
DEFAULT_SOURCE_PROFILE: Final = Path(
    "config/freqtrade_paper_closed_trades_source_profile_v2.json"
)
DEFAULT_OUTPUT_ROOT: Final = Path("data/research/market_features_first_training_runs_v1")
DEFAULT_REPORT_JSON: Final = Path(
    "data/reports/market_features_rematerialization_first_training_runs_v1.json"
)
DEFAULT_REPORT_MARKDOWN: Final = Path(
    "data/reports/market_features_rematerialization_first_training_runs_v1.md"
)

MARKET_FEATURE_COLUMNS: Final = (
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_15",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "rsi_14",
    "macd_hist",
    "atr_pct_14",
    "vol_30",
    "vol_120",
    "volume_rel_30",
    "volume_z_30",
    "trend_score",
)
FEATURE_COLUMNS: Final = MARKET_FEATURE_COLUMNS
POINT_IN_TIME_FEATURE_COLUMNS: Final = tuple(
    f"feature_5m_{name}" for name in MARKET_FEATURE_COLUMNS
)
KNOWN_INPUT_FIELDS: Final = (
    "symbol",
    "side",
    "entry_hour_utc",
    "entry_day_of_week",
    "feature_age_seconds",
    "market_regime",
    "volatility_regime",
)
KNOWN_NUMERIC_FEATURE_COLUMNS: Final = (
    "meta_symbol_btcusdt",
    "meta_symbol_ethusdt",
    "meta_side_long",
    "meta_side_short",
    "entry_hour_utc",
    "entry_day_of_week",
    "feature_age_seconds",
    "market_regime_trend_up",
    "market_regime_trend_down",
    "market_regime_range",
    "market_regime_unknown",
    "volatility_regime_low",
    "volatility_regime_normal",
    "volatility_regime_high",
    "volatility_regime_unknown",
)
MODEL_FEATURE_COLUMNS: Final = (
    *POINT_IN_TIME_FEATURE_COLUMNS,
    *KNOWN_NUMERIC_FEATURE_COLUMNS,
)

CLASSIFIER_MODEL_NAMES: Final = (
    "logistic_regression",
    "extra_trees_classifier",
    "random_forest_classifier",
    "hist_gradient_boosting_classifier",
)
REGRESSOR_MODEL_NAMES: Final = (
    "huber_regressor",
    "random_forest_regressor",
    "extra_trees_regressor",
    "hist_gradient_boosting_regressor",
)
MODEL_NAMES: Final = (*CLASSIFIER_MODEL_NAMES, *REGRESSOR_MODEL_NAMES)

FORBIDDEN_EXACT_FEATURES: Final = frozenset(
    {
        "pnl",
        "pnl_fechado",
        "net_pnl",
        "gross_pnl",
        "mfe",
        "mfe_pct",
        "mae",
        "mae_pct",
        "close_time",
        "close_time_utc",
        "exit_price",
        "exit_reason",
        "profit_ratio",
        "target_profitable",
        "provenance",
        "source_file",
        "ocr_source",
    }
)
FORBIDDEN_FEATURE_PREFIXES: Final = (
    "future_ret_",
    "target_",
    "label_",
    "outcome_",
    "pnl_",
    "close_",
    "exit_",
    "mfe_",
    "mae_",
    "post_entry_",
    "source_",
    "provenance_",
)

LOOKAHEAD_EXACT_COLUMNS: Final = frozenset(
    {
        "pnl",
        "pnl_fechado",
        "net_pnl",
        "gross_pnl",
        "mfe",
        "mfe_pct",
        "mae",
        "mae_pct",
        "close_time",
        "close_time_utc",
        "exit_price",
        "exit_reason",
        "profit_ratio",
        "target_profitable",
    }
)
LOOKAHEAD_COLUMN_PREFIXES: Final = (
    "future_ret_",
    "target_",
    "label_",
    "outcome_",
    "pnl_",
    "close_time_",
    "exit_",
    "mfe_",
    "mae_",
    "post_entry_",
)

SAFETY_FLAGS: Final = MappingProxyType(
    {
        "research_only": True,
        "read_only_by_default": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_active_registry": False,
        "writes_signal_file": False,
        "registry_write": False,
        "registry_write_performed": False,
        "model_promotion": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
    }
)


@dataclass(frozen=True)
class RuntimeEnvironment:
    python_version: str
    sklearn_version: str
    joblib_version: str


@dataclass(frozen=True)
class PipelinePaths:
    project_root: Path
    master_path: Path
    market_features_path: Path
    paper_snapshot_path: Path
    source_profile_path: Path
    output_root: Path
    master_dataset_path: Path
    paper_dataset_path: Path
    predictions_path: Path
    report_json_path: Path
    report_markdown_path: Path


@dataclass(frozen=True)
class PipelineConfig:
    allow_paper_read: bool = False
    rematerialize_features: bool = False
    run_baselines: bool = False
    run_supervised_training: bool = False
    run_qlib_training: bool = False
    run_walkforward: bool = False
    run_backtest: bool = False
    run_monte_carlo: bool = False
    evaluate_paper_holdout: bool = False
    write_research_artifacts: bool = False
    expected_master_rows: int = EXPECTED_MASTER_ROWS
    embargo_seconds: int = EMBARGO_SECONDS
    seed: int = RANDOM_SEED
    monte_carlo_iterations: int = 500
    monte_carlo_block_size: int = 20
    maximum_negative_pnl_probability: float = 0.20
    environment_override: RuntimeEnvironment | None = None


def resolve_paths(project_root: str | Path) -> PipelinePaths:
    root = Path(project_root).resolve()
    output_root = (root / DEFAULT_OUTPUT_ROOT).resolve()
    return PipelinePaths(
        project_root=root,
        master_path=(root / DEFAULT_MASTER).resolve(),
        market_features_path=(root / DEFAULT_MARKET_FEATURES).resolve(),
        paper_snapshot_path=(root / DEFAULT_PAPER_SNAPSHOT).resolve(),
        source_profile_path=(root / DEFAULT_SOURCE_PROFILE).resolve(),
        output_root=output_root,
        master_dataset_path=output_root / "master_point_in_time_5m.parquet",
        paper_dataset_path=output_root / "paper_holdout_point_in_time_5m.parquet",
        predictions_path=output_root / "walkforward_predictions.parquet",
        report_json_path=(root / DEFAULT_REPORT_JSON).resolve(),
        report_markdown_path=(root / DEFAULT_REPORT_MARKDOWN).resolve(),
    )


def canonical_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        python_version=CANONICAL_PYTHON_VERSION,
        sklearn_version=CANONICAL_SKLEARN_VERSION,
        joblib_version=CANONICAL_JOBLIB_VERSION,
    )


def safety_flags() -> dict[str, bool]:
    return dict(SAFETY_FLAGS)
