"""Data-driven local SVG charts for read-only dashboard telemetry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from html import escape
from math import isfinite
from typing import Any

from .status import normalize_status


_MAX_SERIES_POINTS = 240
_MAX_LATENCY_POINTS = 120
_MAX_GRID_LEVELS = 200
_MAX_DEPTH_LEVELS = 20
_MISSING_PRESERVED_STATUSES = {
    "blocked",
    "critical",
    "error",
    "hard_blocked",
    "planned",
    "stale",
}


def render_chart_placeholder(
    title: str,
    message: str = "Dados insuficientes para renderização",
    status: str = "unknown",
) -> str:
    """Render an explicit non-telemetry placeholder."""

    normalized = normalize_status(status)
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        '<div class="sfc-chart-grid"></div>'
        f'<div class="sfc-chart-title">{escape(str(title))}</div>'
        f'<div class="sfc-chart-message">{escape(str(message))}</div></div>'
    )


def render_sparkline_placeholder(label: str, status: str = "neutral") -> str:
    """Render an explicit missing-series state without decorative fake bars."""

    return render_chart_placeholder(
        label,
        "Série não fornecida · UNKNOWN",
        _missing_status(status),
    )


def render_sparkline_svg(
    values: Iterable[Any] | None,
    *,
    label: str = "Série",
    status: str = "neutral",
    width: int = 280,
    height: int = 72,
) -> str:
    """Render a tiny dependency-free SVG from finite observed values only."""

    normalized = normalize_status(status)
    series = _finite_series(values, allow_negative=True)[-_MAX_SERIES_POINTS:]
    if len(series) < 2:
        return render_chart_placeholder(
            label,
            "Série insuficiente · UNKNOWN",
            _missing_status(status),
        )

    safe_width = _bounded_int(width, minimum=120, maximum=1200, default=280)
    safe_height = _bounded_int(height, minimum=48, maximum=400, default=72)
    pad = 10.0
    min_v = min(series)
    max_v = max(series)
    span = max_v - min_v
    step = (safe_width - 2 * pad) / max(len(series) - 1, 1)

    points: list[str] = []
    for idx, value in enumerate(series):
        x = pad + idx * step
        if span <= 0.0:
            y = safe_height / 2.0
        else:
            y = safe_height - pad - ((value - min_v) / span) * (safe_height - 2 * pad)
        points.append(f"{x:.2f},{y:.2f}")

    last_x, last_y = points[-1].split(",", maxsplit=1)
    safe_label = escape(str(label))
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{safe_label}</div>'
        f'<svg viewBox="0 0 {safe_width} {safe_height}" role="img" '
        f'aria-label="{safe_label}" width="100%" height="{safe_height}">'
        '<polyline fill="none" stroke="#00B7FF" stroke-width="2.4" '
        f'points="{" ".join(points)}"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="3.4" fill="#00E676"/>'
        "</svg>"
        f'<div class="sfc-chart-message">amostras {len(series)} · '
        f"min {_format_number(min_v)} · max {_format_number(max_v)}</div>"
        "</div>"
    )


def render_latency_scatter_svg(
    values: Iterable[Any] | None,
    *,
    label: str = "Latência",
    status: str = "neutral",
    width: int = 280,
    height: int = 130,
) -> str:
    """Render a latency distribution from finite non-negative observations only."""

    normalized = normalize_status(status)
    series = _finite_series(values, allow_negative=False)[-_MAX_LATENCY_POINTS:]
    if not series:
        return render_chart_placeholder(
            label,
            "Latência indisponível · UNKNOWN",
            _missing_status(status),
        )

    safe_width = _bounded_int(width, minimum=160, maximum=1200, default=280)
    safe_height = _bounded_int(height, minimum=90, maximum=500, default=130)
    pad = 14.0
    max_v = max(series)
    scale_max = max(max_v, 1e-12)
    step = (safe_width - 2 * pad) / max(len(series) - 1, 1)

    dots: list[str] = []
    for idx, value in enumerate(series):
        x = pad + idx * step
        y = safe_height - pad - (value / scale_max) * (safe_height - 2 * pad)
        dots.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.4" '
            'fill="#00B7FF" opacity=".78"/>'
        )

    safe_label = escape(str(label))
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{safe_label}</div>'
        f'<svg viewBox="0 0 {safe_width} {safe_height}" role="img" '
        f'aria-label="{safe_label}" width="100%" height="{safe_height}">'
        f'<line x1="{pad:.0f}" y1="{safe_height-pad:.0f}" '
        f'x2="{safe_width-pad:.0f}" y2="{safe_height-pad:.0f}" '
        'stroke="#1D3A4D" stroke-width="1"/>'
        f'<line x1="{pad:.0f}" y1="{pad:.0f}" '
        f'x2="{pad:.0f}" y2="{safe_height-pad:.0f}" '
        'stroke="#1D3A4D" stroke-width="1"/>'
        f'{"".join(dots)}'
        "</svg>"
        f'<div class="sfc-chart-message">amostras {len(series)} · '
        f"pico {_format_number(max_v)} ms</div>"
        "</div>"
    )


def render_mini_donut_css(
    percent: Any,
    *,
    label: str,
    status: str = "neutral",
) -> str:
    """Render a CSS-only donut only when the percentage is finite and in range."""

    normalized = normalize_status(status)
    pct = _finite_float(percent)
    safe_label = escape(str(label))
    if pct is None or pct < 0.0 or pct > 100.0:
        missing_status = _missing_status(status)
        return (
            f'<div class="sfc-mini-kpi sfc-card-status-{missing_status}">'
            f'<div class="sfc-mini-kpi-label">{safe_label}</div>'
            '<div class="sfc-donut" style="--p:0;" '
            'aria-label="Percentual indisponível"><span>UNKNOWN</span></div>'
            "</div>"
        )

    return (
        f'<div class="sfc-mini-kpi sfc-card-status-{normalized}">'
        f'<div class="sfc-mini-kpi-label">{safe_label}</div>'
        f'<div style="--p:{pct:.2f};" class="sfc-donut" '
        f'aria-label="{pct:.2f}%"><span>{pct:.0f}%</span></div>'
        "</div>"
    )


def render_mini_bar_stack(
    values: dict[str, Any] | None,
    *,
    label: str,
    status: str = "neutral",
) -> str:
    """Render a compact proportional bar stack from finite non-negative values."""

    normalized = normalize_status(status)
    if not values:
        return render_chart_placeholder(
            label,
            "Distribuição não fornecida · UNKNOWN",
            _missing_status(status),
        )

    pairs: list[tuple[str, float]] = []
    for key, raw_value in values.items():
        value = _finite_float(raw_value)
        if value is None or value < 0.0:
            continue
        pairs.append((str(key), value))

    if not pairs:
        return render_chart_placeholder(
            label,
            "Distribuição sem valores válidos · UNKNOWN",
            _missing_status(status),
        )

    total = sum(value for _, value in pairs)
    if total <= 0.0:
        return render_chart_placeholder(
            label,
            "Distribuição observada com total 0",
            normalized,
        )

    bars = "".join(
        f'<span title="{escape(key)}: {_format_number(value)}" '
        f'style="width:{(value / total) * 100:.4f}%"></span>'
        for key, value in pairs
        if value > 0.0
    )
    legend = "".join(
        f'<div class="sfc-status-row"><span>{escape(key)}</span>'
        f"<strong>{_format_number(value)}</strong></div>"
        for key, value in pairs
    )
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{escape(str(label))}</div>'
        f'<div class="sfc-bar-stack">{bars}</div>{legend}</div>'
    )


def render_grid_channel_preview(
    *,
    status: str = "neutral",
    lower_price: Any = None,
    upper_price: Any = None,
    current_price: Any = None,
    level_prices: Iterable[Any] | None = None,
    label: str = "Canal do Grid",
) -> str:
    """Render a grid channel only from explicit observed/materialized prices."""

    lower = _positive_float(lower_price)
    upper = _positive_float(upper_price)
    current = _positive_float(current_price)
    if lower is None or upper is None or current is None or upper <= lower:
        return render_chart_placeholder(
            label,
            "Canal não materializado no snapshot · UNKNOWN",
            _missing_status(status),
        )

    levels = sorted(
        {
            value
            for value in _finite_series(level_prices, allow_negative=False)
            if value > 0.0 and lower <= value <= upper
        }
    )[:_MAX_GRID_LEVELS]

    width = 320.0
    x0 = 18.0
    x1 = width - 18.0
    channel_width = x1 - x0
    span = upper - lower

    def x_for(value: float) -> float:
        ratio = (value - lower) / span
        return x0 + max(0.0, min(ratio, 1.0)) * channel_width

    level_marks = "".join(
        f'<line x1="{x_for(value):.2f}" y1="34" '
        f'x2="{x_for(value):.2f}" y2="70" stroke="#2E607D" '
        'stroke-width="1" opacity=".78"/>'
        for value in levels
    )
    current_x = x_for(current)
    outside = current < lower or current > upper
    effective_status = (
        "warning"
        if outside and normalize_status(status) in {"neutral", "ok", "info", "readonly"}
        else normalize_status(status)
    )
    safe_label = escape(str(label))
    outside_text = " · preço fora do canal" if outside else ""
    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{effective_status}">'
        f'<div class="sfc-chart-title">{safe_label}</div>'
        '<svg viewBox="0 0 320 104" role="img" '
        f'aria-label="{safe_label}" width="100%" height="104">'
        '<line x1="18" y1="52" x2="302" y2="52" '
        'stroke="#1D3A4D" stroke-width="8" stroke-linecap="round"/>'
        f"{level_marks}"
        f'<line x1="{current_x:.2f}" y1="24" x2="{current_x:.2f}" y2="78" '
        'stroke="#00E676" stroke-width="2.6"/>'
        f'<circle cx="{current_x:.2f}" cy="52" r="4.2" fill="#00E676"/>'
        '<text x="18" y="96" fill="#7F9AAD" font-size="10">'
        f'{escape(_format_number(lower))}</text>'
        '<text x="302" y="96" text-anchor="end" fill="#7F9AAD" font-size="10">'
        f'{escape(_format_number(upper))}</text>'
        f'<text x="{current_x:.2f}" y="16" text-anchor="middle" '
        'fill="#D8F3FF" font-size="10">'
        f'{escape(_format_number(current))}</text>'
        "</svg>"
        f'<div class="sfc-chart-message">níveis observados {len(levels)}'
        f"{escape(outside_text)}</div></div>"
    )


def render_depth_preview(
    *,
    status: str = "neutral",
    bids: Iterable[Any] | None = None,
    asks: Iterable[Any] | None = None,
    label: str = "Top of Book",
    max_levels: int = _MAX_DEPTH_LEVELS,
) -> str:
    """Render bounded public depth from explicit bid/ask rows only."""

    safe_limit = _bounded_int(
        max_levels,
        minimum=1,
        maximum=_MAX_DEPTH_LEVELS,
        default=_MAX_DEPTH_LEVELS,
    )
    bid_rows = sorted(_depth_rows(bids), key=lambda row: row[0], reverse=True)[:safe_limit]
    ask_rows = sorted(_depth_rows(asks), key=lambda row: row[0])[:safe_limit]

    if not bid_rows and not ask_rows:
        return render_chart_placeholder(
            label,
            "Profundidade pública não materializada · UNKNOWN",
            _missing_status(status),
        )

    bid_cumulative = _cumulative_notionals(bid_rows)
    ask_cumulative = _cumulative_notionals(ask_rows)
    maximum = max(
        [value for _, _, value in bid_cumulative + ask_cumulative],
        default=0.0,
    )
    if maximum <= 0.0:
        return render_chart_placeholder(
            label,
            "Profundidade sem notional válido · UNKNOWN",
            _missing_status(status),
        )

    width = 320.0
    center = width / 2.0
    max_bar_width = 132.0
    row_height = 5.0
    row_gap = 2.0
    top = 22.0
    visible_rows = max(len(bid_cumulative), len(ask_cumulative), 1)
    height = top + visible_rows * (row_height + row_gap) + 36.0

    bid_bars: list[str] = []
    for idx, (_, _, cumulative) in enumerate(bid_cumulative):
        bar_width = (cumulative / maximum) * max_bar_width
        y = top + idx * (row_height + row_gap)
        bid_bars.append(
            f'<rect x="{center - bar_width - 4:.2f}" y="{y:.2f}" '
            f'width="{bar_width:.2f}" height="{row_height:.2f}" '
            'fill="#00E676" opacity=".58"/>'
        )

    ask_bars: list[str] = []
    for idx, (_, _, cumulative) in enumerate(ask_cumulative):
        bar_width = (cumulative / maximum) * max_bar_width
        y = top + idx * (row_height + row_gap)
        ask_bars.append(
            f'<rect x="{center + 4:.2f}" y="{y:.2f}" '
            f'width="{bar_width:.2f}" height="{row_height:.2f}" '
            'fill="#FF5A6F" opacity=".58"/>'
        )

    best_bid = bid_rows[0][0] if bid_rows else None
    best_ask = ask_rows[0][0] if ask_rows else None
    bid_text = _format_number(best_bid) if best_bid is not None else "UNKNOWN"
    ask_text = _format_number(best_ask) if best_ask is not None else "UNKNOWN"
    safe_label = escape(str(label))
    normalized = normalize_status(status)

    return (
        f'<div class="sfc-chart-placeholder sfc-card-status-{normalized}">'
        f'<div class="sfc-chart-title">{safe_label}</div>'
        f'<svg viewBox="0 0 320 {height:.0f}" role="img" '
        f'aria-label="{safe_label}" width="100%" height="{height:.0f}">'
        '<line x1="160" y1="16" x2="160" '
        f'y2="{height - 22:.0f}" stroke="#1D3A4D" stroke-width="1"/>'
        f'{"".join(bid_bars)}{"".join(ask_bars)}'
        '<text x="152" y="12" text-anchor="end" fill="#00E676" font-size="10">'
        f'BID {escape(bid_text)}</text>'
        '<text x="168" y="12" fill="#FF8A98" font-size="10">'
        f'ASK {escape(ask_text)}</text>'
        "</svg>"
        f'<div class="sfc-chart-message">bids {len(bid_rows)} · '
        f"asks {len(ask_rows)} · notional cumulativo observado</div></div>"
    )


def _finite_series(
    values: Iterable[Any] | None,
    *,
    allow_negative: bool,
) -> list[float]:
    if values is None or isinstance(values, (str, bytes, Mapping)):
        return []

    series: list[float] = []
    try:
        iterator = iter(values)
    except TypeError:
        return []

    for raw_value in iterator:
        value = _finite_float(raw_value)
        if value is None:
            continue
        if not allow_negative and value < 0.0:
            continue
        series.append(value)
    return series


def _depth_rows(values: Iterable[Any] | None) -> list[tuple[float, float]]:
    if values is None or isinstance(values, (str, bytes, Mapping)):
        return []

    rows: list[tuple[float, float]] = []
    try:
        iterator = iter(values)
    except TypeError:
        return []

    for row in iterator:
        price: Any = None
        quantity: Any = None
        if isinstance(row, Mapping):
            price = row.get("price")
            quantity = row.get("quantity", row.get("qty"))
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 2:
            price = row[0]
            quantity = row[1]

        safe_price = _positive_float(price)
        safe_quantity = _positive_float(quantity)
        if safe_price is None or safe_quantity is None:
            continue
        rows.append((safe_price, safe_quantity))
    return rows


def _cumulative_notionals(
    rows: Sequence[tuple[float, float]],
) -> list[tuple[float, float, float]]:
    cumulative = 0.0
    output: list[tuple[float, float, float]] = []
    for price, quantity in rows:
        cumulative += price * quantity
        output.append((price, quantity, cumulative))
    return output


def _finite_float(value: Any) -> float | None:
    try:
        candidate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(candidate):
        return None
    return candidate


def _positive_float(value: Any) -> float | None:
    candidate = _finite_float(value)
    if candidate is None or candidate <= 0.0:
        return None
    return candidate


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError, OverflowError):
        candidate = default
    return max(minimum, min(candidate, maximum))


def _missing_status(status: str) -> str:
    normalized = normalize_status(status)
    if normalized in _MISSING_PRESERVED_STATUSES:
        return normalized
    return "unknown"


def _format_number(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000.0:
        return f"{value:,.2f}"
    if magnitude >= 1.0:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")
