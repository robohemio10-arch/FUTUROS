"""Section containers used throughout the command center."""

from html import escape

from .status import normalize_status


def render_panel_title(title: str, subtitle: str | None = None) -> str:
    """Return safe section title HTML."""

    subtitle_html = f'<div class="sfc-section-subtitle">{escape(subtitle)}</div>' if subtitle else ""
    return f'<div class="sfc-section-title">{escape(title)}</div>{subtitle_html}'


def render_section_panel(
    title: str,
    body_html: str | None = None,
    subtitle: str | None = None,
    status: str = "neutral",
) -> str:
    """Return a visual section panel around trusted component HTML."""

    normalized = normalize_status(status)
    return (
        f'<section class="sfc-section sfc-section-status-{normalized}">'
        f"{render_panel_title(title, subtitle)}"
        f'<div class="sfc-section-body">{body_html or ""}</div></section>'
    )
