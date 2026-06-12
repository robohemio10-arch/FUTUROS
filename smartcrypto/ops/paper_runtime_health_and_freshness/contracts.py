from __future__ import annotations

from dataclasses import dataclass

PROJECT_NAME = "SMART FUTUROS"
DASHBOARD_NAME = "SMART FUTUROS Command Center"
SCHEMA_VERSION = "paper_runtime_health_and_freshness_v1"
DEFAULT_OUTPUT_PATH = "data/reports/paper_runtime_health_and_freshness_report.json"

SAFE_TRUE_FLAGS = ("paper_only", "shadow_only")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "changes_config",
    "changes_training_dataset",
    "writes_trades_master",
    "writes_official_trades_master",
    "runs_ocr",
    "imports_trades",
)

EXPECTED_PAPER_SERVICES = (
    "freqtrade-paper",
    "phase14-feedback-sync-paper",
    "qlib-refresh-supervisor-paper",
    "smartcrypto-bot-paper",
    "smartcrypto-dashboard-paper",
    "trade-event-notifications-paper",
)

CRITICAL_PAPER_SERVICES = (
    "freqtrade-paper",
    "phase14-feedback-sync-paper",
    "qlib-refresh-supervisor-paper",
    "smartcrypto-bot-paper",
    "smartcrypto-dashboard-paper",
)

OPTIONAL_PAPER_SERVICES = ("trade-event-notifications-paper",)

SERVICE_COMPONENTS = {
    "freqtrade-paper": "freqtrade_paper",
    "phase14-feedback-sync-paper": "phase14_feedback_sync",
    "qlib-refresh-supervisor-paper": "qlib_refresh",
    "smartcrypto-bot-paper": "smartcrypto_bot",
    "smartcrypto-dashboard-paper": "dashboard",
    "trade-event-notifications-paper": "notifications",
}


@dataclass(frozen=True)
class RuntimeReportContract:
    name: str
    path: str
    required: bool
    max_age_seconds: int
    component: str
    timestamp_keys: tuple[str, ...] = (
        "created_at",
        "generated_at",
        "generated_at_utc",
        "last_updated_utc",
        "timestamp_utc",
        "updated_at",
    )


RUNTIME_REPORTS: tuple[RuntimeReportContract, ...] = (
    RuntimeReportContract(
        "phase14_runtime_feedback_sync_report",
        "data/reports/phase14_runtime_feedback_sync_report.json",
        True,
        300,
        "phase14_feedback_sync",
    ),
    RuntimeReportContract(
        "phase14_summary",
        "data/reports/phase14_summary.json",
        True,
        300,
        "phase14_feedback_sync",
    ),
    RuntimeReportContract(
        "phase14_output_summary",
        "data/reports/phase14_output_summary.json",
        True,
        300,
        "phase14_feedback_sync",
    ),
    RuntimeReportContract(
        "phase14_closed_feedback_report",
        "data/reports/phase14_closed_feedback_report.json",
        True,
        300,
        "phase14_feedback_sync",
    ),
    RuntimeReportContract(
        "phase14_open_positions_report",
        "data/reports/phase14_open_positions_report.json",
        True,
        300,
        "phase14_feedback_sync",
    ),
    RuntimeReportContract(
        "qlib_paper_refresh_supervisor_report",
        "data/reports/qlib_paper_refresh_supervisor_report.json",
        True,
        900,
        "qlib_refresh",
    ),
    RuntimeReportContract(
        "qlib_market_features_refresh_report",
        "data/reports/qlib_market_features_refresh_report.json",
        True,
        900,
        "qlib_refresh",
    ),
    RuntimeReportContract(
        "qlib_fresh_prediction_runner_report",
        "data/reports/qlib_fresh_prediction_runner_report.json",
        True,
        900,
        "qlib_refresh",
    ),
    RuntimeReportContract(
        "dashboard_snapshot_build_summary",
        "data/reports/dashboard_snapshot_build_summary.json",
        False,
        900,
        "dashboard",
    ),
    RuntimeReportContract(
        "runtime_evidence_pack_v2",
        "data/reports/runtime_evidence_pack_v2.json",
        False,
        900,
        "runtime_evidence",
    ),
    RuntimeReportContract(
        "readiness_snapshot_v2",
        "data/reports/readiness_snapshot_v2.json",
        False,
        900,
        "readiness",
    ),
    RuntimeReportContract(
        "paper_shadow_soak_gap_accounting_report",
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        False,
        900,
        "gap_accounting",
    ),
    RuntimeReportContract(
        "trade_event_notifications_report",
        "data/reports/trade_event_notifications_report.json",
        False,
        300,
        "notifications",
    ),
)
