"""Versioned contracts for the paper profit research dataset."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd


SCHEMA_VERSION: Final = "profit_research_dataset_snapshot_v1"
DATASET_ID: Final = "paper_closed_trades_candle_aligned_research_v1"
DEFAULT_SOURCE_PROFILE: Final = Path(
    "config/freqtrade_paper_closed_trades_source_profile_v2.json"
)
DEFAULT_PAPER_DB: Final = Path("freqtrade/user_data/tradesv3.paper.sqlite")
DEFAULT_PAPER_SNAPSHOT_DB: Final = Path(
    "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)
DEFAULT_CANDLE_ROOT: Final = Path("data/features/market_features_60d.parquet")
DEFAULT_OUTPUT_ROOT: Final = Path("data")

SAFETY_FLAGS: Final[dict[str, bool]] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "writes_runtime": False,
    "writes_master": False,
    "writes_sqlite": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
}

ENTRY_FEATURE_SPECS: Final[tuple[dict[str, Any], ...]] = (
    {"name": "entry_return_1", "lookback": 1, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_return_3", "lookback": 3, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_return_6", "lookback": 6, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_return_12", "lookback": 12, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_return_24", "lookback": 24, "dtype": "float64", "unit": "ratio"},
    {
        "name": "entry_rolling_volatility_24",
        "lookback": 24,
        "dtype": "float64",
        "unit": "ratio",
    },
    {
        "name": "entry_atr_normalized_14",
        "lookback": 14,
        "dtype": "float64",
        "unit": "ratio",
    },
    {"name": "entry_relative_range", "lookback": 1, "dtype": "float64", "unit": "ratio"},
    {
        "name": "entry_volume_relative_20",
        "lookback": 20,
        "dtype": "float64",
        "unit": "ratio",
    },
    {
        "name": "entry_distance_from_ma20",
        "lookback": 20,
        "dtype": "float64",
        "unit": "ratio",
    },
    {"name": "entry_slope_6", "lookback": 6, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_momentum_6", "lookback": 6, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_trend_regime", "lookback": 20, "dtype": "string", "unit": "category"},
    {
        "name": "entry_volatility_regime",
        "lookback": 100,
        "dtype": "string",
        "unit": "category",
    },
    {"name": "entry_candle_direction", "lookback": 1, "dtype": "string", "unit": "category"},
    {"name": "entry_body_ratio", "lookback": 1, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_upper_wick_ratio", "lookback": 1, "dtype": "float64", "unit": "ratio"},
    {"name": "entry_lower_wick_ratio", "lookback": 1, "dtype": "float64", "unit": "ratio"},
    {
        "name": "entry_distance_local_high_20",
        "lookback": 20,
        "dtype": "float64",
        "unit": "ratio",
    },
    {
        "name": "entry_distance_local_low_20",
        "lookback": 20,
        "dtype": "float64",
        "unit": "ratio",
    },
    {"name": "entry_hour_utc", "lookback": 0, "dtype": "int64", "unit": "hour"},
    {"name": "entry_day_of_week", "lookback": 0, "dtype": "string", "unit": "category"},
)

ENTRY_FEATURE_COLUMNS: Final[tuple[str, ...]] = tuple(
    str(item["name"]) for item in ENTRY_FEATURE_SPECS
)


@dataclass(frozen=True)
class DatasetBuildPaths:
    project_root: Path
    source_profile: Path
    paper_db: Path
    paper_snapshot_db: Path
    candle_root: Path
    output_root: Path
    report_json: Path
    report_markdown: Path
    dataset_parquet: Path
    dataset_manifest: Path
    dataset_schema: Path
    coverage_sidecar: Path
    rejection_sidecar: Path


@dataclass(frozen=True)
class DatasetBuildResult:
    dataset: pd.DataFrame
    segments: tuple[dict[str, Any], ...]
    report: dict[str, Any]


def resolve_build_paths(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    paper_db: str | Path | None = None,
    paper_snapshot_db: str | Path | None = None,
    candle_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> DatasetBuildPaths:
    root = Path(project_root).resolve()
    output = _resolve(root, output_root, DEFAULT_OUTPUT_ROOT)
    reports = output / "reports"
    research = output / "research"
    stem = "profit_research_dataset_snapshot_v1"
    return DatasetBuildPaths(
        project_root=root,
        source_profile=_resolve(root, source_profile, DEFAULT_SOURCE_PROFILE),
        paper_db=_resolve(root, paper_db, DEFAULT_PAPER_DB),
        paper_snapshot_db=_resolve(root, paper_snapshot_db, DEFAULT_PAPER_SNAPSHOT_DB),
        candle_root=_resolve(root, candle_root, DEFAULT_CANDLE_ROOT),
        output_root=output,
        report_json=reports / f"{stem}.json",
        report_markdown=reports / f"{stem}.md",
        dataset_parquet=research / f"{stem}.parquet",
        dataset_manifest=research / f"{stem}.manifest.json",
        dataset_schema=research / f"{stem}.schema.json",
        coverage_sidecar=research / f"{stem}.coverage.json",
        rejection_sidecar=research / f"{stem}.rejections.json",
    )


def dataset_contract(paths: DatasetBuildPaths) -> dict[str, Any]:
    feature_specs = []
    for item in ENTRY_FEATURE_SPECS:
        feature_specs.append(
            {
                **item,
                "timestamp_semantics": "source_candle_close_time_utc_lte_trade_open_time_utc",
                "null_policy": "preserve_null_no_imputation",
                "leakage_classification": "entry_time_observable",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "source_contracts": [
            "freqtrade_paper_closed_trades_source_profile_v2",
            "market_candles_point_in_time_readonly_v1",
        ],
        "primary_key": ["stable_trade_id"],
        "row_identity": "freqtrade_paper_trade_id_with_source_hash_lineage",
        "timestamp_semantics": {
            "trade_timestamps": "UTC",
            "entry_feature_timestamp": "latest_completed_candle_available_at_or_before_open",
            "path_timestamps": "post_entry_outcome_diagnostics_only",
        },
        "feature_availability_rules": feature_specs,
        "label_definitions": {
            "winner_to_loser_conversion": "mfe_absolute_gt_zero_and_realized_net_pnl_lt_zero",
            "path_features": "diagnostic_only_not_entry_features",
        },
        "null_policy": "no_silent_imputation",
        "dedup_policy": "stable_trade_id_first_occurrence_after_deterministic_sort",
        "sort_order": ["open_time_utc", "stable_trade_id"],
        "determinism_requirements": [
            "sorted_input_paths",
            "stable_row_order",
            "fixed_bootstrap_seed",
            "generated_at_excluded_from_artifact_identity",
        ],
        "leakage_guards": [
            "entry_feature_timestamp_utc_lte_open_time_utc",
            "future_ret_target_label_columns_forbidden",
            "path_features_never_used_as_entry_features",
            "no_imputed_candles",
        ],
        "output_paths": {
            "dataset": str(paths.dataset_parquet),
            "manifest": str(paths.dataset_manifest),
            "schema": str(paths.dataset_schema),
            "coverage": str(paths.coverage_sidecar),
            "rejections": str(paths.rejection_sidecar),
            "report_json": str(paths.report_json),
            "report_markdown": str(paths.report_markdown),
        },
        "output_formats": ["parquet", "json", "markdown"],
        "generated_at_semantics": "audit_metadata_only_excluded_from_dataset_hash",
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _resolve(root: Path, supplied: str | Path | None, default: Path) -> Path:
    candidate = Path(supplied) if supplied is not None else default
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
