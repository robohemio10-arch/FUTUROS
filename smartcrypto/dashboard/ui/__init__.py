"""Institutional visual system for SMART FUTUROS Command Center."""

from .badges import render_status_badges
from .cards import render_compact_metric_card, render_metric_card, render_status_card
from .charts import render_chart_placeholder, render_sparkline_placeholder
from .footer import render_footer_audit_bar
from .layout import (
    render_global_topbar,
    render_page_title,
    render_readonly_banner,
    render_two_column_layout,
)
from .sections import render_panel_title, render_section_panel
from .sidebar import NAV_ITEMS, render_sidebar
from .states import render_empty_state, render_error_state, render_unknown_state
from .status import normalize_status, status_to_css_class, status_to_label
from .tables import render_html_table
from .theme import (
    inject_futuros_command_center_css,
    inject_smart_futuros_command_center_css,
)

__all__ = [
    "NAV_ITEMS",
    "inject_futuros_command_center_css",
    "inject_smart_futuros_command_center_css",
    "normalize_status",
    "render_chart_placeholder",
    "render_compact_metric_card",
    "render_empty_state",
    "render_error_state",
    "render_footer_audit_bar",
    "render_global_topbar",
    "render_html_table",
    "render_metric_card",
    "render_page_title",
    "render_panel_title",
    "render_readonly_banner",
    "render_section_panel",
    "render_sidebar",
    "render_sparkline_placeholder",
    "render_status_badges",
    "render_status_card",
    "render_two_column_layout",
    "render_unknown_state",
    "status_to_css_class",
    "status_to_label",
]
