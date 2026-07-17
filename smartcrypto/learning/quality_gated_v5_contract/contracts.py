"""Versioned contracts for the V5 quality-gated research projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

SCHEMA_VERSION: Final = "quality_gated_v5_provenance_freshness_nonregression_contract_v1"
DECISION_RESEARCH: Final = "MANTER_EM_RESEARCH"

DEFAULT_TRADE_ENRICHED: Final = Path("data/features/trade_enriched.parquet")
DEFAULT_MARKET_FEATURES: Final = Path("data/features/market_features_60d.parquet")
DEFAULT_OFFICIAL_QUALITY_GATED: Final = Path(
    "data/features/training_dataset_quality_gated_binance_1m.parquet"
)
DEFAULT_MODEL_PATH: Final = Path("data/models/ai_shadow_filter_extratrees_050.joblib")

DEFAULT_REPORT_JSON: Final = Path(
    "data/reports/quality_gated_v5_provenance_freshness_nonregression_contract_v1.json"
)
DEFAULT_REPORT_ROWS_JSONL: Final = Path(
    "data/reports/quality_gated_v5_provenance_freshness_nonregression_rows_v1.jsonl"
)
DEFAULT_REPORT_MARKDOWN: Final = Path(
    "data/reports/quality_gated_v5_provenance_freshness_nonregression_contract_v1.md"
)

EXPECTED_MODEL_SHA256: Final = (
    "b5599c0d09881051b0e4456a9b3e94a38a10c4ecd1f0f14e37f736c2f9d083d4"
)

PRIOR_FEATURE_SUFFIXES: Final = (
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

META_FEATURES: Final = (
    "meta_symbol_btcusdt",
    "meta_symbol_ethusdt",
    "meta_side_long",
    "meta_side_short",
    "meta_side_unknown",
    "meta_hour_sin",
    "meta_hour_cos",
    "meta_dow_sin",
    "meta_dow_cos",
    "meta_month_sin",
    "meta_month_cos",
    "meta_session_asia",
    "meta_session_europe",
    "meta_session_newyork",
    "meta_session_europe_newyork_overlap",
    "meta_is_weekend",
)

V13_FEATURE_SUFFIXES: Final = (
    "range_pct",
    "body_pct",
    "body_to_range",
    "upper_wick_pct",
    "lower_wick_pct",
    "close_pos",
    "is_green",
    "ret_20",
    "ret_50",
    "dist_high_20",
    "dist_low_20",
    "range_z_50",
    "volume_z_50",
)

MODEL_FEATURES: Final = (
    *(f"prior_1m_{name}" for name in PRIOR_FEATURE_SUFFIXES),
    *(f"prior_5m_{name}" for name in PRIOR_FEATURE_SUFFIXES),
    *META_FEATURES,
    *(f"v13_1m_{name}" for name in V13_FEATURE_SUFFIXES),
    *(f"v13_5m_{name}" for name in V13_FEATURE_SUFFIXES),
)

if len(MODEL_FEATURES) != 74:  # pragma: no cover - import-time invariant
    raise RuntimeError(f"invalid_model_feature_contract:{len(MODEL_FEATURES)}")


@dataclass(frozen=True)
class ProvenanceContract:
    contract_id: str
    required_fields: MappingProxyType[str, str]
    segment: str = "BITRADEX_OCR"


PROVENANCE_CONTRACTS: Final = (
    ProvenanceContract(
        contract_id="legacy_v1",
        required_fields=MappingProxyType(
            {"source_file": "bitradex_ocr_locked_candidates_20260528_090243"}
        ),
    ),
    ProvenanceContract(
        contract_id="ocr_v11",
        required_fields=MappingProxyType({"ocr_source": "bitradex_ocr_candidate_v1_1"}),
    ),
    ProvenanceContract(
        contract_id="ocr_v5_20260714",
        required_fields=MappingProxyType(
            {
                "source_file": (
                    "bitradex_ocr_locked_candidates_"
                    "20260714_151816_time_repaired_orderid_synthetic_v5"
                ),
                "ocr_source": (
                    "bitradex_black_rectangles_"
                    "time_repaired_orderid_synthetic_v5"
                ),
            }
        ),
    ),
)

KNOWN_PROVENANCE_FIELD_VALUES: Final = frozenset(
    (field_name, expected.casefold())
    for contract in PROVENANCE_CONTRACTS
    for field_name, expected in contract.required_fields.items()
)

FRESHNESS_MAX_AGE_SECONDS: Final = MappingProxyType({"1m": 120, "5m": 600})
TIMEFRAME_SECONDS: Final = MappingProxyType({"1m": 60, "5m": 300})
SNAPSHOT_TIMESTAMP_SEMANTICS: Final = "candle_open"

BLOCK_REASON_PRECEDENCE: Final = (
    "BLOCKED_EMPTY_TRADE_ID",
    "BLOCKED_DUPLICATE_TRADE_ID",
    "BLOCKED_INVALID_OPEN_TIME",
    "BLOCKED_FUTURE_1M_SNAPSHOT",
    "BLOCKED_FUTURE_5M_SNAPSHOT",
    "BLOCKED_IN_PROGRESS_1M_SNAPSHOT",
    "BLOCKED_IN_PROGRESS_5M_SNAPSHOT",
    "BLOCKED_STALE_1M_SNAPSHOT",
    "BLOCKED_STALE_5M_SNAPSHOT",
    "BLOCKED_MISSING_1M_SNAPSHOT",
    "BLOCKED_MISSING_5M_SNAPSHOT",
    "BLOCKED_UNKNOWN_SNAPSHOT_TIMESTAMP_SEMANTICS",
    "BLOCKED_AMBIGUOUS_PROVENANCE",
    "BLOCKED_PARTIAL_PROVENANCE",
    "BLOCKED_UNRECOGNIZED_PROVENANCE",
    "BLOCKED_FEATURE_LEAKAGE",
    "BLOCKED_MODEL_FEATURE_SCHEMA",
    "BLOCKED_NON_NUMERIC_MODEL_FEATURES",
    "BLOCKED_NON_FINITE_MODEL_FEATURES",
    "BLOCKED_MISSING_PRIOR_1M_FEATURES",
    "BLOCKED_MISSING_PRIOR_5M_FEATURES",
    "BLOCKED_MISSING_V13_1M_FEATURES",
    "BLOCKED_MISSING_V13_5M_FEATURES",
)

LEAKAGE_EXACT_COLUMNS: Final = frozenset(
    {
        "pnl",
        "net_pnl",
        "gross_pnl",
        "reported_pnl_usdt",
        "return_pct",
        "profit_ratio",
        "mfe_pct",
        "mae_pct",
        "exit_price",
        "close_time",
        "close_time_utc",
        "close_ts",
    }
)

LEAKAGE_PREFIXES: Final = (
    "target_",
    "label_",
    "outcome",
    "future_ret_",
    "pnl_",
    "close_",
    "post_entry",
)

SAFETY_FLAGS: Final = MappingProxyType(
    {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "projection_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_official": False,
        "writes_candidate": False,
        "writes_full_audit": False,
        "changes_model": False,
        "model_deserialization_performed": False,
        "training_requested": False,
        "training_performed": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "services_started": False,
    }
)
