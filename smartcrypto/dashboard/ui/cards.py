"""Reusable safe HTML cards for dashboard snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from html import escape
from typing import Any

from .status import normalize_status, status_to_label


def render_metric_card(
    label: str,
    value: str,
    unit: str | None = None,
    status: str = "neutral",
    helper: str | None = None,
    icon: str | None = None,
    size: str = "md",
) -> str:
    """Return safe HTML for an institutional metric card."""

    normalized = normalize_status(status)
    safe_size = size if size in {"sm", "md", "lg"} else "md"
    icon_html = f'<span class="sfc-card-icon">{escape(icon)}</span>' if icon else ""
    unit_html = f'<span class="sfc-card-unit">{escape(unit)}</span>' if unit else ""
    helper_html = f'<div class="sfc-card-helper">{escape(helper)}</div>' if helper else ""
    return (
        f'<article class="sfc-card sfc-card-{safe_size} sfc-card-status-{normalized}">'
        f'<div class="sfc-card-label">{icon_html}{escape(label)}</div>'
        f'<div class="sfc-card-value">{escape(value)}{unit_html}</div>'
        f'{helper_html}<span class="sfc-card-status">{status_to_label(normalized)}</span>'
        "</article>"
    )


def render_status_card(
    title: str,
    status: str,
    description: str | None = None,
    size: str = "md",
) -> str:
    """Return safe HTML for a status-led card."""

    normalized = normalize_status(status)
    safe_size = size if size in {"sm", "md", "lg"} else "md"
    description_html = (
        f'<div class="sfc-card-helper">{escape(description)}</div>' if description else ""
    )
    return (
        f'<article class="sfc-card sfc-card-{safe_size} sfc-card-status-{normalized}">'
        f'<div class="sfc-card-label">{escape(title)}</div>'
        f'<div class="sfc-status-pill sfc-status-{normalized}">{status_to_label(normalized)}</div>'
        f"{description_html}</article>"
    )


def render_compact_metric_card(label: str, value: str, status: str = "neutral") -> str:
    """Return a compact metric card."""

    return render_metric_card(label, value, status=status, size="sm")


def render_compact_kpi(
    label: str,
    value: Any,
    *,
    helper: str | None = None,
    status: str = "neutral",
) -> str:
    """Return a dense KPI tile for grid-based visual panels."""

    normalized = normalize_status(status)
    helper_html = f'<div class="sfc-mini-kpi-helper">{escape(helper)}</div>' if helper else ""
    return (
        f'<div class="sfc-mini-kpi sfc-card-status-{normalized}">'
        f'<div class="sfc-mini-kpi-label">{escape(label)}</div>'
        f'<div class="sfc-mini-kpi-value">{escape(_display_value(value))}</div>'
        f"{helper_html}</div>"
    )


def render_health_card(
    title: str,
    status: str,
    *,
    description: str | None = None,
    rows: Sequence[tuple[str, Any, str | None]] | None = None,
) -> str:
    """Return a health card with one headline status and optional status rows."""

    normalized = normalize_status(status)
    description_html = (
        f'<div class="sfc-card-helper">{escape(description)}</div>' if description else ""
    )
    row_html = "".join(
        _render_status_row(label, value, row_status)
        for label, value, row_status in (rows or ())
    )
    return (
        f'<article class="sfc-card sfc-card-lg sfc-card-status-{normalized}">'
        f'<div class="sfc-card-label">{escape(title)}</div>'
        f'<div class="sfc-status-pill sfc-status-{normalized}">{status_to_label(normalized)}</div>'
        f"{description_html}"
        f'<div class="sfc-card-helper">{row_html}</div>'
        "</article>"
    )


def render_mini_panel_card(
    number: str,
    title: str,
    status: str,
    *,
    description: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> str:
    """Return a compact institutional card summarizing one dashboard area."""

    normalized = normalize_status(status)
    meta_rows = "".join(
        _render_status_row(str(key), value, None)
        for key, value in (meta or {}).items()
    )
    description_html = (
        f'<div class="sfc-card-helper">{escape(description)}</div>' if description else ""
    )
    return (
        f'<article class="sfc-card sfc-card-md sfc-card-status-{normalized}">'
        f'<div class="sfc-card-label">{escape(number)} · {escape(title)}</div>'
        f'<div class="sfc-status-pill sfc-status-{normalized}">{status_to_label(normalized)}</div>'
        f"{description_html}"
        f'<div class="sfc-card-helper">{meta_rows}</div>'
        "</article>"
    )


def render_blocked_action_card(
    title: str,
    reason: str,
    *,
    action: str | None = None,
    status: str = "blocked",
) -> str:
    """Return a visual-only blocker card. It never executes remediation."""

    normalized = normalize_status(status)
    action_html = (
        f'<div class="sfc-card-helper"><strong>Ação manual:</strong> {escape(action)}</div>'
        if action
        else ""
    )
    return (
        f'<article class="sfc-card sfc-card-lg sfc-card-status-{normalized}">'
        f'<div class="sfc-card-label">{escape(title)}</div>'
        f'<div class="sfc-status-pill sfc-status-{normalized}">{status_to_label(normalized)}</div>'
        f'<div class="sfc-card-helper">{escape(reason)}</div>'
        f"{action_html}</article>"
    )


def render_card_grid(cards: Iterable[str], *, css_class: str = "sfc-mini-kpi-grid") -> str:
    """Return a safe card grid wrapper around already-safe component HTML."""

    safe_class = " ".join(part for part in css_class.split() if part.replace("-", "").replace("_", "").isalnum())
    return f'<div class="{escape(safe_class or "sfc-mini-kpi-grid")}">{"".join(cards)}</div>'


def _render_status_row(label: str, value: Any, status: str | None) -> str:
    normalized = normalize_status(status or value)
    pill = (
        f'<span class="sfc-status-pill sfc-status-{normalized}">'
        f"{status_to_label(normalized)}</span>"
        if status
        else f"<strong>{escape(_display_value(value))}</strong>"
    )
    return (
        '<div class="sfc-status-row">'
        f'<span>{escape(label)}</span>'
        f"{pill}"
        "</div>"
    )


def _display_value(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
