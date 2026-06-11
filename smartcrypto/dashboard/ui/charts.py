"""Lightweight chart placeholders without plotting dependencies."""

from html import escape

from .status import normalize_status


def render_chart_placeholder(
    title: str,
    message: str = "Dados insuficientes para renderização",
    status: str = "unknown",
) -> str:
    normalized = normalize_status(status)
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        '<div class="sfc-chart-grid"></div>'
        f'<div class="sfc-chart-title">{escape(title)}</div>'
        f'<div class="sfc-chart-message">{escape(message)}</div></div>'
    )


def render_sparkline_placeholder(label: str, status: str = "neutral") -> str:
    normalized = normalize_status(status)
    return (
        f'<div class="sfc-sparkline sfc-card-status-{normalized}">'
        f'<span>{escape(label)}</span><i></i><i></i><i></i><i></i><i></i></div>'
    )
