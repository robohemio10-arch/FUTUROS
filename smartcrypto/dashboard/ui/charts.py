"""Lightweight local SVG charts for visual-only dashboard telemetry."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from math import isnan
from typing import Any

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


def render_sparkline_svg(
    values: Iterable[Any] | None,
    *,
    label: str = "Série",
    status: str = "neutral",
    width: int = 280,
    height: int = 72,
) -> str:
    """Render a tiny dependency-free SVG sparkline."""

    normalized = normalize_status(status)
    series = _to_float_series(values)
    if len(series) < 2:
        return render_chart_placeholder(label, "Série insuficiente", normalized)

    safe_width = max(120, min(int(width), 1200))
    safe_height = max(48, min(int(height), 400))
    pad = 10
    min_v = min(series)
    max_v = max(series)
    span = max(max_v - min_v, 1e-9)
    step = (safe_width - 2 * pad) / max(len(series) - 1, 1)

    points = []
    for idx, value in enumerate(series):
        x = pad + idx * step
        y = safe_height - pad - ((value - min_v) / span) * (safe_height - 2 * pad)
        points.append(f"{x:.2f},{y:.2f}")

    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{escape(label)}</div>'
        f'<svg viewBox="0 0 {safe_width} {safe_height}" role="img" '
        f'aria-label="{escape(label)}" width="100%" height="{safe_height}">'
        '<defs><linearGradient id="sfcSpark" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0%" stop-color="#00B7FF" stop-opacity=".35"/>'
        '<stop offset="100%" stop-color="#00E676" stop-opacity=".85"/>'
        '</linearGradient></defs>'
        f'<polyline fill="none" stroke="url(#sfcSpark)" stroke-width="2.4" points="{" ".join(points)}"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="3.4" fill="#00E676"/>'
        '</svg>'
        f'<div class="sfc-chart-message">min {min_v:.2f} · max {max_v:.2f}</div>'
        '</div>'
    )


def render_latency_scatter_svg(
    values: Iterable[Any] | None,
    *,
    label: str = "Latência",
    status: str = "neutral",
    width: int = 280,
    height: int = 130,
) -> str:
    """Render a small latency distribution SVG."""

    normalized = normalize_status(status)
    series = _to_float_series(values)
    if not series:
        return render_chart_placeholder(label, "Latência indisponível", normalized)

    safe_width = max(160, min(int(width), 1200))
    safe_height = max(90, min(int(height), 500))
    pad = 14
    max_v = max(max(series), 1.0)
    step = (safe_width - 2 * pad) / max(len(series) - 1, 1)

    dots = []
    for idx, value in enumerate(series[:120]):
        x = pad + idx * step
        y = safe_height - pad - (value / max_v) * (safe_height - 2 * pad)
        dots.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.4" fill="#00B7FF" opacity=".78"/>')

    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{escape(label)}</div>'
        f'<svg viewBox="0 0 {safe_width} {safe_height}" role="img" '
        f'aria-label="{escape(label)}" width="100%" height="{safe_height}">'
        f'<line x1="{pad}" y1="{safe_height-pad}" x2="{safe_width-pad}" y2="{safe_height-pad}" '
        'stroke="#1D3A4D" stroke-width="1"/>'
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{safe_height-pad}" '
        'stroke="#1D3A4D" stroke-width="1"/>'
        f'{"".join(dots)}'
        '</svg>'
        f'<div class="sfc-chart-message">amostras {len(series)} · pico {max_v:.2f} ms</div>'
        '</div>'
    )


def render_mini_donut_css(
    percent: Any,
    *,
    label: str,
    status: str = "neutral",
) -> str:
    """Render a CSS-only donut indicator."""

    normalized = normalize_status(status)
    pct = _clamp_percent(percent)
    return (
        f'<div class="sfc-mini-kpi sfc-card-status-{normalized}">'
        f'<div class="sfc-mini-kpi-label">{escape(label)}</div>'
        f'<div style="--p:{pct:.2f};" class="sfc-donut">'
        f'<span>{pct:.0f}%</span></div>'
        '</div>'
    )


def render_mini_bar_stack(
    values: dict[str, Any] | None,
    *,
    label: str,
    status: str = "neutral",
) -> str:
    """Render a compact proportional bar stack."""

    normalized = normalize_status(status)
    pairs = [(str(k), _safe_float(v)) for k, v in (values or {}).items()]
    pairs = [(k, v) for k, v in pairs if v > 0]
    if not pairs:
        return render_chart_placeholder(label, "Sem distribuição", normalized)
    total = sum(v for _, v in pairs) or 1.0
    bars = "".join(
        f'<span title="{escape(k)}: {v:.2f}" style="width:{(v / total) * 100:.2f}%"></span>'
        for k, v in pairs
    )
    legend = "".join(
        f'<div class="sfc-status-row"><span>{escape(k)}</span><strong>{v:.2f}</strong></div>'
        for k, v in pairs
    )
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{escape(label)}</div>'
        f'<div class="sfc-bar-stack">{bars}</div>{legend}</div>'
    )


def render_grid_channel_preview(*, status: str = "neutral") -> str:
    """Render a decorative read-only grid connectivity preview."""

    normalized = normalize_status(status)
    cells = "".join("<i></i>" for _ in range(36))
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        '<div class="sfc-chart-title">Canais monitorados</div>'
        f'<div class="sfc-grid-preview">{cells}</div>'
        '<div class="sfc-chart-message">visualização read-only</div></div>'
    )


def render_depth_preview(*, status: str = "neutral") -> str:
    """Render a decorative top-of-book depth preview."""

    normalized = normalize_status(status)
    bids = "".join(f'<i style="height:{height}px"></i>' for height in (18, 27, 38, 44, 31, 21))
    asks = "".join(f'<i style="height:{height}px"></i>' for height in (22, 34, 45, 39, 26, 16))
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        '<div class="sfc-chart-title">Top of Book</div>'
        f'<div class="sfc-depth-preview"><div>{bids}</div><div>{asks}</div></div>'
        '<div class="sfc-chart-message">book público agregado</div></div>'
    )


def _to_float_series(values: Iterable[Any] | None) -> list[float]:
    series = []
    for value in values or ():
        candidate = _safe_float(value)
        if candidate >= 0:
            series.append(candidate)
    return series


def _safe_float(value: Any) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return 0.0
    if isnan(candidate):
        return 0.0
    return candidate


def _clamp_percent(value: Any) -> float:
    return max(0.0, min(_safe_float(value), 100.0))
