from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.alerts.contracts import NotificationIntent
from smartcrypto.dashboard.alerts.notification_stub_dispatcher import evaluate_notification_intent
from smartcrypto.dashboard.alerts.routing import list_notification_routing_policies
from smartcrypto.dashboard.components.alert_stubs import (
    render_notification_dispatch_stub,
    render_notification_routing_table,
    render_notification_stub_only_banner,
)
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "08. Alertas & Mensageria"
SNAPSHOT_PATH = "data/reports/dashboard_alerts_messaging_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_alerts_messaging_snapshot_v1"
REQUIRED_SECTIONS = (
    "dispatcher_status", "channels", "queue", "severity_breakdown", "critical_events",
    "retry_backoff", "routing_policy", "messaging_audit", "audit",
)
METRICS = (
    ("Dispatcher Status", "dispatcher_status", "dispatcher_status"),
    ("Telegram Channel Status", "channels", "telegram"),
    ("NTFY Channel Status", "channels", "ntfy"),
    ("Queue Pending", "queue", "pending_count"),
    ("Critical Undelivered", "queue", "critical_undelivered_count"),
    ("Success Rate", "queue", "success_rate_pct"),
    ("Failure Rate", "queue", "failure_rate_pct"),
    ("Dead Letter Count", "queue", "dead_letter_count"),
    ("Retry Backoff", "retry_backoff", "current_backoff_seconds"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    render_snapshot_page(
        title=PAGE_TITLE, snapshot_path=SNAPSHOT_PATH, snapshot=snapshot,
        section_order=REQUIRED_SECTIONS, metric_specs=METRICS, ui=target_ui,
    )
    render_notification_stub_only_banner(ui=target_ui)
    render_notification_routing_table(list_notification_routing_policies(), ui=target_ui)
    render_notification_dispatch_stub(
        evaluate_notification_intent(
            NotificationIntent(
                notification_id="dashboard-warning-example",
                severity="WARNING",
                title="Dry-run notification example",
                message="No delivery is attempted by this dashboard stub.",
            )
        ),
        ui=target_ui,
    )


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.alerts_messaging, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
