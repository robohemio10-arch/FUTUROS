"""Canonical outcome schema for paper futures-perpetual auto-learning."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "paper_autolearning_foundation_v1"

DEFAULT_CLOSED_TRADES_CSV = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
DEFAULT_FEEDBACK_STORE = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_OUTCOME_EVENTS = Path("data/feedback/outcome_events.parquet")
DEFAULT_MICROBATCH_DIR = Path("data/feedback/training_microbatches")
DEFAULT_REPORT_JSON = Path("data/reports/paper_autolearning_foundation_summary.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_autolearning_foundation_summary.md")
DEFAULT_SOURCE_CONTRACT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.json")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "master_update_requested": False,
    "master_update_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
}

OUTCOME_EVENT_COLUMNS = [
    "event_id",
    "source",
    "source_file",
    "source_sha256",
    "ingestion_run_id",
    "order_id",
    "internal_order_id",
    "trade_id",
    "row_fingerprint",
    "symbol",
    "symbol_norm",
    "market_type",
    "side",
    "position_side",
    "margin_mode",
    "leverage",
    "open_time_utc",
    "close_time_utc",
    "duration_seconds",
    "is_closed",
    "entry_price",
    "exit_price",
    "quantity",
    "notional",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "net_pnl",
    "profit_ratio",
    "pnl_on_margin_pct",
    "pnl_on_notional_pct",
    "liquidation_price",
    "distance_to_liquidation_pct",
    "exit_reason",
    "roi_hit",
    "stoploss_hit",
    "forced_exit",
    "liquidation_flag",
    "label_win_loss",
    "label_sign",
    "label_net_pnl_bucket",
    "label_holding_time_bucket",
    "label_quality_bucket",
    "paper_candidate_filter_called",
    "paper_candidate_filter_decision",
    "qlib_prediction_id",
    "ai_shadow_decision_id",
    "strategy_id",
    "validation_status",
    "validation_errors",
    "created_at_utc",
]

OUTCOME_OR_LABEL_PREFIXES = ("label_",)
OUTCOME_OR_LABEL_COLUMNS = {
    "event_id",
    "source",
    "source_file",
    "source_sha256",
    "ingestion_run_id",
    "row_fingerprint",
    "close_time_utc",
    "duration_seconds",
    "is_closed",
    "exit_price",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "net_pnl",
    "profit_ratio",
    "pnl_on_margin_pct",
    "pnl_on_notional_pct",
    "exit_reason",
    "roi_hit",
    "stoploss_hit",
    "forced_exit",
    "liquidation_flag",
    "validation_status",
    "validation_errors",
    "created_at_utc",
}

FUTURES_COVERAGE_FIELDS = (
    "funding_fee",
    "trading_fee",
    "leverage",
    "margin_mode",
    "liquidation_price",
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def is_feature_column_allowed(column: str) -> bool:
    lower = column.lower()
    if lower.startswith("future_ret_"):
        return False
    if any(lower.startswith(prefix) for prefix in OUTCOME_OR_LABEL_PREFIXES):
        return False
    return column not in OUTCOME_OR_LABEL_COLUMNS


def safety_payload(*, writes_parquet: bool = False) -> dict[str, Any]:
    return {**SAFETY_FLAGS, "writes_parquet": bool(writes_parquet)}


def coverage_ratio(rows: list[Mapping[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    present = sum(1 for row in rows if row.get(field) not in (None, "", []))
    return round(present / len(rows), 10)
