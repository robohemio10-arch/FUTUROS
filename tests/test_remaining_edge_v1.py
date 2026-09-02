from datetime import datetime, timedelta, timezone

from smartcrypto.research.portfolio_intelligence import OpenPositionOpportunity, RemainingEVEstimate
from smartcrypto.research.portfolio_intelligence.remaining_edge import build_open_position_view

T = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)
H = "a" * 64


def _position(*, remaining: RemainingEVEstimate | None) -> OpenPositionOpportunity:
    return OpenPositionOpportunity(
        position_id="pos-1",
        symbol="BTCUSDT",
        side="long",
        strategy_id="trend-v1",
        opened_at_utc=T - timedelta(hours=2),
        observed_at_utc=T - timedelta(seconds=10),
        available_at_utc=T - timedelta(seconds=5),
        capital_locked_usdt=100.0,
        position_age_seconds=7200.0,
        remaining_ev=remaining,
        source_hash=H,
    )


def test_missing_remaining_ev_is_explicit_and_not_invented() -> None:
    view = build_open_position_view(_position(remaining=None), T)
    assert view.remaining_ev_status == "SOURCE_MISSING"
    assert view.remaining_ev_usdt is None
    assert view.point_in_time_valid is True


def test_future_remaining_ev_is_fail_closed() -> None:
    remaining = RemainingEVEstimate(
        estimate_id="remaining-future",
        value_usdt=3.0,
        semantics="EXPECTED_REMAINING_NET_PNL_USDT",
        generated_at_utc=T + timedelta(seconds=1),
        available_at_utc=T + timedelta(seconds=2),
        confidence=0.8,
        source_hash=H,
    )
    position = OpenPositionOpportunity(
        position_id="pos-1",
        symbol="BTCUSDT",
        side="long",
        strategy_id="trend-v1",
        opened_at_utc=T - timedelta(hours=2),
        observed_at_utc=T - timedelta(seconds=10),
        available_at_utc=T - timedelta(seconds=5),
        capital_locked_usdt=100.0,
        position_age_seconds=7200.0,
        remaining_ev=remaining,
        source_hash=H,
    )
    view = build_open_position_view(position, T)
    assert view.remaining_ev_status == "INVALID_POINT_IN_TIME"
    assert view.remaining_ev_usdt is None
    assert "remaining_ev_generated_after_decision" in view.point_in_time_errors
