from __future__ import annotations

from math import inf, nan
from pathlib import Path

from smartcrypto.dashboard.ui.charts import (
    render_depth_preview,
    render_grid_channel_preview,
    render_latency_scatter_svg,
    render_mini_bar_stack,
    render_mini_donut_css,
    render_sparkline_placeholder,
    render_sparkline_svg,
)


def test_sparkline_placeholder_is_explicit_unknown_not_decorative_bars() -> None:
    html = render_sparkline_placeholder("Equity")

    assert "UNKNOWN" in html
    assert "sfc-chart-placeholder" in html
    assert "<i></i>" not in html
    assert "sfc-sparkline" not in html


def test_sparkline_preserves_signed_and_zero_observations() -> None:
    html = render_sparkline_svg([-2.0, -1.0, 0.0, 1.0], label="PnL")

    assert "amostras 4" in html
    assert "min -2" in html
    assert "max 1" in html
    assert "<polyline" in html


def test_sparkline_discards_invalid_values_instead_of_fabricating_zero() -> None:
    html = render_sparkline_svg(["invalid", nan, inf, 5.0], label="PnL")

    assert "Série insuficiente" in html
    assert "UNKNOWN" in html
    assert "<polyline" not in html


def test_latency_discards_negative_and_invalid_observations() -> None:
    html = render_latency_scatter_svg(
        [-10.0, "invalid", nan, 0.0, 2.0],
        label="Latency",
    )

    assert "amostras 2" in html
    assert "pico 2 ms" in html
    assert html.count("<circle ") == 2


def test_latency_caps_series_before_positioning_points() -> None:
    html = render_latency_scatter_svg(range(200), label="Latency", width=280)

    assert "amostras 120" in html
    assert html.count("<circle ") == 120
    assert 'cx="266.00"' in html


def test_invalid_donut_is_unknown_and_not_presented_as_zero_percent() -> None:
    html = render_mini_donut_css("invalid", label="API weight")

    assert "UNKNOWN" in html
    assert "0%</span>" not in html
    assert "sfc-card-status-unknown" in html


def test_valid_zero_donut_remains_real_zero_percent() -> None:
    html = render_mini_donut_css(0, label="API weight", status="ok")

    assert "0%</span>" in html
    assert "UNKNOWN" not in html
    assert "sfc-card-status-ok" in html


def test_out_of_range_donut_is_not_silently_clamped() -> None:
    html = render_mini_donut_css(120, label="API weight", status="ok")

    assert "UNKNOWN" in html
    assert "100%" not in html


def test_bar_stack_uses_only_finite_non_negative_observations() -> None:
    html = render_mini_bar_stack(
        {"BTC": 1.0, "ETH": 3.0, "bad": "invalid", "negative": -8.0},
        label="Allocation",
    )

    assert "width:25.0000%" in html
    assert "width:75.0000%" in html
    assert "bad" not in html
    assert "negative" not in html


def test_bar_stack_distinguishes_real_zero_total_from_missing_data() -> None:
    zero_html = render_mini_bar_stack(
        {"BTC": 0.0, "ETH": 0.0},
        label="Allocation",
        status="ok",
    )
    missing_html = render_mini_bar_stack(None, label="Allocation", status="ok")

    assert "total 0" in zero_html
    assert "sfc-card-status-ok" in zero_html
    assert "não fornecida" in missing_html
    assert "sfc-card-status-unknown" in missing_html


def test_grid_preview_without_contract_is_unknown_not_decorative() -> None:
    html = render_grid_channel_preview(status="ok")

    assert "UNKNOWN" in html
    assert "sfc-card-status-unknown" in html
    assert "sfc-grid-preview" not in html
    assert "<i></i>" not in html


def test_grid_preview_is_derived_from_explicit_prices() -> None:
    html = render_grid_channel_preview(
        status="ok",
        lower_price=100.0,
        upper_price=110.0,
        current_price=105.0,
        level_prices=[100.0, 102.5, 105.0, 107.5, 110.0],
    )

    assert "100</text>" in html
    assert "105</text>" in html
    assert "110</text>" in html
    assert "níveis observados 5" in html
    assert "sfc-grid-preview" not in html
    assert "<svg" in html


def test_grid_preview_marks_outside_price_without_fabricating_channel() -> None:
    html = render_grid_channel_preview(
        status="ok",
        lower_price=100.0,
        upper_price=110.0,
        current_price=115.0,
        level_prices=[100.0, 105.0, 110.0],
    )

    assert "preço fora do canal" in html
    assert "sfc-card-status-warning" in html
    assert "115</text>" in html


def test_depth_preview_without_rows_is_unknown_not_static_bars() -> None:
    html = render_depth_preview(status="ok")

    assert "UNKNOWN" in html
    assert "sfc-card-status-unknown" in html
    assert "sfc-depth-preview" not in html
    assert "height:18px" not in html


def test_depth_preview_accepts_materialized_mapping_rows() -> None:
    html = render_depth_preview(
        status="ok",
        bids=[
            {"price": 104.9, "quantity": 2.0, "notional_usdt": 209.8},
            {"price": 104.8, "quantity": 3.0, "notional_usdt": 314.4},
        ],
        asks=[
            {"price": 105.1, "quantity": 1.5, "notional_usdt": 157.65},
            {"price": 105.2, "quantity": 2.5, "notional_usdt": 263.0},
        ],
    )

    assert "BID 104.9" in html
    assert "ASK 105.1" in html
    assert "bids 2" in html
    assert "asks 2" in html
    assert "notional cumulativo observado" in html
    assert "<rect " in html


def test_depth_preview_accepts_tuple_rows_and_discards_invalid_rows() -> None:
    html = render_depth_preview(
        bids=[(100.0, 2.0), ("bad", 9.0), (99.0, -1.0)],
        asks=[(101.0, 1.0)],
    )

    assert "BID 100" in html
    assert "ASK 101" in html
    assert "bids 1" in html
    assert "asks 1" in html


def test_charts_source_has_no_legacy_hardcoded_preview_patterns() -> None:
    source = Path("smartcrypto/dashboard/ui/charts.py").read_text(encoding="utf-8")

    assert "range(36)" not in source
    assert "height in (18, 27, 38, 44, 31, 21)" not in source
    assert "height in (22, 34, 45, 39, 26, 16)" not in source
    assert "return 0.0" not in source
    assert "decorative read-only grid connectivity preview" not in source
    assert "decorative top-of-book depth preview" not in source
