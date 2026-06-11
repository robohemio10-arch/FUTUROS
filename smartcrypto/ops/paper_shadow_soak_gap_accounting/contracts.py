from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_shadow_soak_continuity_gap_accounting_v1"
PROJECT_NAME = "SMART FUTUROS"
DASHBOARD_NAME = "SMART FUTUROS Command Center"
DEFAULT_OUTPUT_PATH = Path("data/reports/paper_shadow_soak_gap_accounting_report.json")
DEFAULT_DIAGNOSTIC_SOAK_DAYS = 7
DEFAULT_REQUIRED_SOAK_DAYS = 30
DEFAULT_MAX_WARNING_GAP_MINUTES = 60
DEFAULT_MAX_CRITICAL_GAP_MINUTES = 360

SAFE_TRUE_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "dashboard_readonly": True,
    "live_locked": True,
}

SAFE_FALSE_FLAGS: dict[str, bool] = {
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "sends_notifications": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_config": False,
    "changes_active_signals": False,
    "changes_readiness": False,
    "changes_training_dataset": False,
    "writes_trades_master": False,
    "runs_ocr": False,
    "imports_trades": False,
    "rebuilds_dataset": False,
    "cleans_sqlite": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
}

TIMESTAMP_KEYS = (
    "generated_at",
    "generated_at_utc",
    "created_at",
    "updated_at",
    "timestamp",
    "started_at",
    "finished_at",
    "last_activity_at",
    "last_activity_timestamp",
    "last_trade_timestamp",
    "last_closed_trade_timestamp",
    "market_features_max_timestamp",
    "input_data_timestamp",
    "max_timestamp",
    "min_timestamp",
)

START_KEYS = ("start", "started_at", "start_time", "from", "from_timestamp", "begin", "min_timestamp")
END_KEYS = ("end", "ended_at", "end_time", "to", "to_timestamp", "finish", "finished_at", "max_timestamp")
OBSERVED_SOAK_KEYS = (
    "observed_soak_days",
    "observed_calendar_days",
    "observed_active_days",
    "paper_shadow_soak_days",
    "paper_soak_days",
    "soak_days",
    "continuous_soak_days",
    "runtime_days",
)
CRITICAL_GAP_KEYS = ("critical_gap_count", "critical_soak_gap_count")
WARNING_GAP_KEYS = ("warning_gap_count", "soak_gap_count")
MAX_GAP_KEYS = ("max_gap_minutes", "max_soak_gap_minutes")
STATUS_KEYS = ("status", "readiness_status", "continuity_status")


@dataclass(frozen=True)
class SoakEvidenceSource:
    name: str
    path: str
    required_for_accounting: bool
    required_for_readiness: bool
    description: str


@dataclass(frozen=True)
class GapWindow:
    start_utc: str
    end_utc: str
    duration_minutes: float
    severity: str
    source: str


@dataclass(frozen=True)
class GapAccountingResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool
