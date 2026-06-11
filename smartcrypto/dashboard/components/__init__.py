"""Reusable read-only components for SMART FUTUROS Command Center."""

from .read_only import (
    render_audit_footer,
    render_disabled_control_stub,
    render_global_safety_badges,
    render_missing_snapshot_state,
    render_readonly_banner,
    render_snapshot_page,
    render_unknown_state,
)
from .alert_stubs import (
    render_notification_dispatch_stub,
    render_notification_routing_table,
    render_notification_stub_only_banner,
)
from .control_stubs import (
    render_command_policy_table,
    render_command_result_stub,
    render_n4_hard_block_panel,
)
from .dataset_pipeline import render_dataset_ocr_training_pipeline_status
from .decision_trace import render_financial_event_log_decision_trace
from .readiness_gates import render_readiness_gates_snapshot_view
from .snapshot_cards import render_key_value_grid, render_metric_cards, render_snapshot_header
from .snapshot_tables import render_section_status_table
from .status_badges import render_status_badge

__all__ = [
    "render_audit_footer",
    "render_command_policy_table",
    "render_command_result_stub",
    "render_disabled_control_stub",
    "render_global_safety_badges",
    "render_key_value_grid",
    "render_metric_cards",
    "render_n4_hard_block_panel",
    "render_notification_dispatch_stub",
    "render_notification_routing_table",
    "render_notification_stub_only_banner",
    "render_readiness_gates_snapshot_view",
    "render_financial_event_log_decision_trace",
    "render_dataset_ocr_training_pipeline_status",
    "render_missing_snapshot_state",
    "render_readonly_banner",
    "render_section_status_table",
    "render_snapshot_header",
    "render_snapshot_page",
    "render_status_badge",
    "render_unknown_state",
]
