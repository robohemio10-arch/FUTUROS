"""Versioned contracts for point-in-time 5m research training runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final


SCHEMA_VERSION: Final = "market_features_rematerialization_first_training_runs_v1"
DECISION: Final = "MANTER_EM_RESEARCH"
EXPECTED_MASTER_ROWS: Final = 3504
TIMEFRAME: Final = "5m"
TIMEFRAME_SECONDS: Final = 300
EMBARGO_SECONDS: Final = 1800
RANDOM_SEED: Final = 42

DEFAULT_MASTER: Final = Path("data/trades/trades_master.parquet")
DEFAULT_MARKET_FEATURES: Final = Path("data/features/market_features_60d.parquet")
DEFAULT_PAPER_SNAPSHOT: Final = Path(
    "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)
DEFAULT_SOURCE_PROFILE: Final = Path(
    "config/freqtrade_paper_closed_trades_source_profile_v2.json"
)
DEFAULT_OUTPUT_ROOT: Final = Path(
    "data/research/market_features_first_training_runs_v1"
)
DEFAULT_REPORT_JSON: Final = Path(
    "data/reports/market_features_rematerialization_first_training_runs_v1.json"
)
DEFAULT_REPORT_MARKDOWN: Final = Path(
    "data/reports/market_features_rematerialization_first_training_runs_v1.md"
)

FEATURE_COLUMNS: Final = (
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
MODEL_FEATURE_COLUMNS: Final = tuple(f"feature_5m_{name}" for name in FEATURE_COLUMNS)
MODEL_NAMES: Final = (
    "logistic_regression",
    "extra_trees",
    "random_forest",
    "hist_gradient_boosting",
)

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
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
    }
)


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


def resolve_paths(project_root: str | Path) -> PipelinePaths:
    """Resolve all inputs and research-only outputs below the project root."""

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


def safety_flags() -> dict[str, bool]:
    """Return a mutable copy for report composition."""

    return dict(SAFETY_FLAGS)
