"""Reusable safe HTML cards for dashboard snapshots."""

from html import escape

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
