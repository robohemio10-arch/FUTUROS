from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from smartcrypto.research.market_intelligence import (
    MarketIntelligenceConfig,
    MarketIntelligenceRequest,
    MarketIntelligenceService,
    build_ablation_manifest,
)

UTC = timezone.utc
DECISION = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
HASH_A = "a" * 64


def _event(
    event_type: str, suffix: int, seconds_ago: int, payload: dict[str, Any]
) -> dict[str, Any]:
    event_time = DECISION - timedelta(seconds=seconds_ago)
    return {
        "event_id": f"{event_type}-{suffix}",
        "source_id": "offline_fixture",
        "exchange": "binance_usdm_public",
        "symbol": "BTCUSDT",
        "event_type": event_type,
        "event_time_utc": event_time.isoformat(),
        "received_at_utc": (event_time + timedelta(milliseconds=10)).isoformat(),
        "available_at_utc": (event_time + timedelta(milliseconds=20)).isoformat(),
        "source_hash": HASH_A,
        "source_sequence": suffix,
        "payload": payload,
    }


def _snapshot():
    request = MarketIntelligenceRequest.model_validate(
        {
            "request_id": "ablation-fixture",
            "exchange": "binance_usdm_public",
            "symbol": "BTCUSDT",
            "decision_time_utc": DECISION.isoformat(),
            "events": [
                _event("agg_trade", 1, 2, {"price": 100, "quantity": 2, "buyer_maker": False}),
                _event("agg_trade", 2, 1, {"price": 101, "quantity": 1, "buyer_maker": True}),
                _event(
                    "book_ticker",
                    3,
                    1,
                    {"best_bid": 100, "best_ask": 101, "bid_qty": 3, "ask_qty": 2},
                ),
            ],
        }
    )
    report = MarketIntelligenceService(MarketIntelligenceConfig()).evaluate(
        request, project_root="."
    )
    assert report.snapshot is not None
    return report.snapshot


def test_ablation_is_deterministic_and_preserves_baseline() -> None:
    snapshot = _snapshot()
    first = build_ablation_manifest(
        snapshot, baseline_feature_names=("feature_ret_1", "feature_rsi")
    )
    second = build_ablation_manifest(
        snapshot, baseline_feature_names=("feature_rsi", "feature_ret_1")
    )
    assert first == second
    assert first.status == "ABLATION_DATA_READY"
    baseline = first.variants[0]
    assert baseline.variant_id == "BASELINE"
    assert baseline.feature_names == ("feature_ret_1", "feature_rsi")
    plus_flow = next(item for item in first.variants if item.variant_id == "BASELINE_PLUS_FLOW")
    assert "feature_ret_1" in plus_flow.feature_names
    assert any(name.startswith("flow_imbalance_") for name in plus_flow.feature_names)


def test_ablation_family_isolation_and_all_available_manifest() -> None:
    manifest = build_ablation_manifest(_snapshot())
    names = {variant.variant_id: variant for variant in manifest.variants}
    assert "BASELINE_PLUS_FLOW" in names
    assert "BASELINE_PLUS_SPREAD" in names
    assert "BASELINE_PLUS_BASIS_FUNDING" not in names
    assert "BASELINE_PLUS_OI" not in names
    combined = names["BASELINE_PLUS_ALL_AVAILABLE_MARKET_INTELLIGENCE"]
    assert combined.feature_families == ("flow", "spread")
    assert combined.feature_count > names["BASELINE_PLUS_FLOW"].feature_count


def test_ablation_rejects_outcome_and_future_leakage() -> None:
    snapshot = _snapshot()
    for forbidden in ("future_ret_60", "target_profit", "label_win", "net_pnl", "exit_reason"):
        manifest = build_ablation_manifest(
            snapshot, baseline_feature_names=("feature_rsi", forbidden)
        )
        assert manifest.status == "BLOCKED_LEAKAGE"
        assert forbidden in manifest.rejected_leakage_features
        assert manifest.variants == ()


def test_ablation_never_trains_promotes_or_writes_registry() -> None:
    manifest = build_ablation_manifest(_snapshot())
    assert manifest.training_performed is False
    assert manifest.model_promoted is False
    assert manifest.registry_write_performed is False
    assert manifest.operational_authority is False
    assert manifest.sends_orders is False
    assert manifest.exchange_private_access is False
    assert manifest.changes_risk is False
    assert manifest.changes_model is False
    assert manifest.writes_active_signals is False
