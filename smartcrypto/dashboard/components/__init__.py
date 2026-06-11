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
from .snapshot_cards import render_key_value_grid, render_metric_cards, render_snapshot_header
from .snapshot_tables import render_section_status_table
from .status_badges import render_status_badge

__all__ = [
    "render_audit_footer",
    "render_disabled_control_stub",
    "render_global_safety_badges",
    "render_key_value_grid",
    "render_metric_cards",
    "render_missing_snapshot_state",
    "render_readonly_banner",
    "render_section_status_table",
    "render_snapshot_header",
    "render_snapshot_page",
    "render_status_badge",
    "render_unknown_state",
]
