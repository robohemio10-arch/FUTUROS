from __future__ import annotations

import json

from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.grid_monitor_snapshot_builder import (
    MAX_MATERIALIZED_DEPTH_LEVELS,
    build_grid_channel_payload,
    build_grid_level_distribution,
    build_grid_monitor_snapshot,
    build_order_book_payload,
    count_duplicate_grid_levels,
)


def test_grid_channel_contract_materializes_visual_prices_and_levels() -> None:
    payload = build_grid_channel_payload(
        lower_price=100.0,
        upper_price=110.0,
        current_price=105.0,
        level_prices=[110.0, 100.0, 105.0, 105.0],
        capital_allocated_usdt=300.0,
    )

    assert payload["lower_price"] == 100.0
    assert payload["upper_price"] == 110.0
    assert payload["current_price"] == 105.0
    assert payload["level_prices"] == [100.0, 105.0, 110.0]
    assert payload["metrics_available"] is True
    assert payload["grid_center"] == 105.0
    assert payload["active_levels"] == 3
    assert payload["expected_levels"] == 3
    assert payload["missing_levels"] == 0


def test_grid_channel_contract_does_not_fabricate_zero_for_missing_price() -> None:
    payload = build_grid_channel_payload(
        lower_price=100.0,
        upper_price=110.0,
        current_price=None,
        level_prices=[100.0, 105.0, 110.0],
        capital_allocated_usdt=300.0,
    )

    assert payload["current_price"] is None
    assert payload["metrics_available"] is False
    assert payload["grid_center"] is None
    assert payload["grid_coverage_pct"] is None
    assert payload["price_outside_grid"] is None
    assert payload["expected_levels"] is None
    assert payload["missing_levels"] is None


def test_order_book_contract_materializes_sorted_bounded_depth() -> None:
    bids = [
        [100.0, 1.0],
        [102.0, 2.0],
        [101.0, 3.0],
        [-1.0, 9.0],
        [103.0, 0.0],
    ]
    asks = [
        [106.0, 2.0],
        [104.0, 1.0],
        [105.0, 3.0],
        [0.0, 5.0],
    ]

    payload = build_order_book_payload(bids, asks, level_limit=2)

    assert [row["price"] for row in payload["bids"]] == [102.0, 101.0]
    assert [row["price"] for row in payload["asks"]] == [104.0, 105.0]
    assert payload["source_bid_level_count"] == 3
    assert payload["source_ask_level_count"] == 3
    assert payload["materialized_bid_level_count"] == 2
    assert payload["materialized_ask_level_count"] == 2
    assert payload["depth_level_limit"] == 2
    assert payload["depth_levels_truncated"] is True
    assert payload["depth_materialized"] is True

    first_bid = payload["bids"][0]
    second_bid = payload["bids"][1]
    assert first_bid["notional_usdt"] == 204.0
    assert first_bid["cumulative_notional_usdt"] == 204.0
    assert second_bid["cumulative_notional_usdt"] == 507.0


def test_order_book_contract_caps_requested_depth_limit() -> None:
    bids = [[100.0 - index, 1.0] for index in range(40)]
    asks = [[101.0 + index, 1.0] for index in range(40)]

    payload = build_order_book_payload(bids, asks, level_limit=10_000)

    assert payload["depth_level_limit"] == MAX_MATERIALIZED_DEPTH_LEVELS
    assert len(payload["bids"]) == MAX_MATERIALIZED_DEPTH_LEVELS
    assert len(payload["asks"]) == MAX_MATERIALIZED_DEPTH_LEVELS
    assert payload["depth_levels_truncated"] is True


def test_grid_level_distribution_is_a_price_histogram_not_a_heatmap() -> None:
    distribution = build_grid_level_distribution(
        level_prices=[100.0, 102.0, 104.0, 108.0, 110.0],
        lower_price=100.0,
        upper_price=110.0,
        bucket_count=5,
    )

    assert len(distribution) == 5
    assert sum(bucket["level_count"] for bucket in distribution) == 5
    assert round(sum(bucket["level_share_pct"] for bucket in distribution), 8) == 100.0
    assert distribution[0]["lower_price"] == 100.0
    assert distribution[-1]["upper_price"] == 110.0


def test_grid_level_distribution_stays_unavailable_without_valid_channel() -> None:
    assert (
        build_grid_level_distribution(
            level_prices=[100.0, 105.0, 110.0],
            lower_price=None,
            upper_price=110.0,
        )
        == []
    )
    assert (
        build_grid_level_distribution(
            level_prices=[100.0, 105.0, 110.0],
            lower_price=110.0,
            upper_price=100.0,
        )
        == []
    )


def test_duplicate_grid_level_count_preserves_integrity_signal() -> None:
    assert count_duplicate_grid_levels([100.0, 100.0, 105.0, 110.0]) == 1
    assert count_duplicate_grid_levels([100.0, 100.00000000000001, 105.0]) == 1
    assert count_duplicate_grid_levels([0.0, -1.0, 100.0]) == 0


def test_full_snapshot_materializes_visual_contract_without_fake_heatmap(tmp_path) -> None:
    reports_dir = tmp_path / "data" / "reports"
    reports_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"

    (data_dir / "freqtrade_signals.json").write_text(
        json.dumps(
            {
                "symbol": "BTC/USDT",
                "current_price": 105.0,
                "lower_price": 100.0,
                "upper_price": 110.0,
                "capital_allocated_usdt": 300.0,
                "signals": [
                    {"grid_level_price": 100.0},
                    {"grid_level_price": 105.0},
                    {"grid_level_price": 105.0},
                    {"grid_level_price": 110.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "market_data_health_audit_report.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "bids": [[104.9, 2.0], [104.8, 3.0]],
                "asks": [[105.1, 1.5], [105.2, 2.5]],
                "mark_price": 105.0,
                "estimated_equity_usdt": 1_000.0,
                "dust_qty": 0.001,
            }
        ),
        encoding="utf-8",
    )

    context = create_dashboard_build_context(
        tmp_path,
        output_dir=tmp_path / "out",
        runtime_mode="paper",
    )
    snapshot = build_grid_monitor_snapshot(context)
    sections = snapshot["sections"]

    assert snapshot["schema_version"] == "dashboard_grid_monitor_snapshot_v1"
    assert snapshot["dashboard_readonly"] is True
    assert snapshot["order_submission_enabled"] is False
    assert snapshot["real_order_submission_enabled"] is False

    assert sections["selected_grid"]["current_price"] == 105.0
    assert sections["grid_channel"]["lower_price"] == 100.0
    assert sections["grid_channel"]["upper_price"] == 110.0
    assert sections["grid_channel"]["current_price"] == 105.0
    assert sections["grid_channel"]["metrics_available"] is True

    assert sections["order_book"]["depth_materialized"] is True
    assert sections["order_book"]["bids"][0]["price"] == 104.9
    assert sections["order_book"]["asks"][0]["price"] == 105.1

    assert sections["heatmap"]["status"] == "UNKNOWN"
    assert sections["heatmap"]["heatmap_available"] is False
    assert sections["heatmap"]["level_distribution_available"] is True
    assert sections["heatmap"]["level_distribution_kind"] == "grid_level_price_histogram"

    assert sections["integrity"]["duplicate_orders"] == 1
    assert sections["audit"]["dashboard_reads_only"] is True
    assert sections["audit"]["snapshot_contract_hardened"] is True

    assert snapshot["safety"]["sends_orders"] is False
    assert snapshot["safety"]["changes_risk"] is False
    assert snapshot["safety"]["changes_model"] is False
    assert snapshot["safety"]["uses_private_exchange"] is False
