from __future__ import annotations

import ast
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from smartcrypto.research.market_intelligence import (
    FreshnessStatus,
    MarketEvent,
    MarketIntelligenceConfig,
    MarketIntelligenceRequest,
    MarketIntelligenceService,
)
from smartcrypto.research.market_intelligence.feature_builder import build_spread_features
from smartcrypto.research.market_intelligence.liquidations import build_liquidation_features

UTC = timezone.utc
DECISION = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
HASH_A = "a" * 64
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "smartcrypto" / "research" / "market_intelligence"
CLI = ROOT / "scripts" / "build_market_intelligence_snapshot_v1.py"
ABLATION_CLI = ROOT / "scripts" / "run_market_intelligence_ablation_v1.py"
CONFIG = ROOT / "config" / "research" / "market_intelligence.yaml"


def _event(
    event_type: str,
    *,
    suffix: str,
    seconds_ago: int,
    payload: dict[str, Any],
    source_id: str = "offline_fixture",
) -> dict[str, Any]:
    event_time = DECISION - timedelta(seconds=seconds_ago)
    received = event_time + timedelta(milliseconds=50)
    available = received + timedelta(milliseconds=50)
    return {
        "event_id": f"{event_type}-{suffix}",
        "source_id": source_id,
        "exchange": "binance_usdm_public",
        "symbol": "BTCUSDT",
        "event_type": event_type,
        "event_time_utc": event_time.isoformat(),
        "received_at_utc": received.isoformat(),
        "available_at_utc": available.isoformat(),
        "source_hash": HASH_A,
        "source_sequence": int(suffix) if suffix.isdigit() else None,
        "payload": payload,
    }


def _request(
    events: list[dict[str, Any]], *, decision: datetime = DECISION
) -> MarketIntelligenceRequest:
    return MarketIntelligenceRequest.model_validate(
        {
            "request_id": "w3-contract-test",
            "exchange": "binance_usdm_public",
            "symbol": "BTCUSDT",
            "decision_time_utc": decision.isoformat(),
            "events": events,
        }
    )


def _config(**overrides: Any) -> MarketIntelligenceConfig:
    base = MarketIntelligenceConfig().model_dump(mode="python")
    base.update(overrides)
    return MarketIntelligenceConfig.model_validate(base)


def _full_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for idx, seconds in enumerate((12, 8, 4, 2, 1), start=1):
        events.append(
            _event(
                "agg_trade",
                suffix=str(idx),
                seconds_ago=seconds,
                payload={
                    "price": 100 + idx,
                    "quantity": 1 + idx / 10,
                    "buyer_maker": bool(idx % 2),
                },
            )
        )
        events.append(
            _event(
                "book_ticker",
                suffix=str(idx + 10),
                seconds_ago=seconds,
                payload={
                    "best_bid": 100 + idx * 0.1,
                    "best_ask": 100.2 + idx * 0.1,
                    "bid_qty": 2 + idx,
                    "ask_qty": 1 + idx,
                },
            )
        )
    for idx, seconds in enumerate((250, 200, 150, 100, 40), start=1):
        events.append(
            _event(
                "mark_price",
                suffix=str(idx + 20),
                seconds_ago=seconds,
                payload={
                    "mark_price": 101.0 + idx / 100,
                    "index_price": 100.0 + idx / 100,
                    "premium_index": 0.0002,
                    "funding_rate": 0.0001 * idx,
                    "funding_rate_kind": "predicted",
                    "funding_interval_hours": 8,
                    "next_funding_time_utc": (DECISION + timedelta(hours=2)).isoformat(),
                },
            )
        )
    events.extend(
        [
            _event(
                "open_interest",
                suffix="31",
                seconds_ago=120,
                payload={"open_interest": 1000, "reference_price": 100},
            ),
            _event(
                "open_interest",
                suffix="32",
                seconds_ago=20,
                payload={"open_interest": 1100, "reference_price": 101},
            ),
            _event(
                "liquidation",
                suffix="41",
                seconds_ago=10,
                payload={"side": "LONG", "notional": 5000},
            ),
            _event(
                "liquidation",
                suffix="42",
                seconds_ago=5,
                payload={"side": "SHORT", "notional": 8000},
            ),
        ]
    )
    return events


def test_market_event_rejects_future_outcome_and_non_utc() -> None:
    payload = _event(
        "agg_trade",
        suffix="1",
        seconds_ago=1,
        payload={"price": 100, "quantity": 1, "buyer_maker": False, "pnl": 9},
    )
    with pytest.raises(ValidationError, match="outcome_or_future_field_forbidden"):
        MarketEvent.model_validate(payload)
    payload = _event(
        "agg_trade",
        suffix="2",
        seconds_ago=1,
        payload={"price": 100, "quantity": 1, "buyer_maker": False},
    )
    payload["event_time_utc"] = "2026-08-28T14:59:59"
    with pytest.raises(ValidationError, match="timestamp_must_be_timezone_aware"):
        MarketEvent.model_validate(payload)


def test_point_in_time_future_event_fails_closed() -> None:
    future = _event(
        "agg_trade",
        suffix="1",
        seconds_ago=1,
        payload={"price": 100, "quantity": 1, "buyer_maker": False},
    )
    future["available_at_utc"] = (DECISION + timedelta(seconds=1)).isoformat()
    request = _request([future])
    report = MarketIntelligenceService(_config()).evaluate(request, project_root=".")
    assert report.status.value == "BLOCKED"
    assert report.invalid_point_in_time_event_count == 1
    assert report.write_performed is False


def test_deterministic_snapshot_watermarks_freshness_and_feature_families() -> None:
    config = _config(real_source_available={name: True for name in _config().real_source_available})
    request = _request(_full_events())
    first = MarketIntelligenceService(config).evaluate(request, project_root=".")
    second = MarketIntelligenceService(config).evaluate(request, project_root=".")
    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.model_dump(mode="json") == second.snapshot.model_dump(mode="json")
    assert len(first.snapshot.source_watermarks) == 5
    assert first.snapshot.feature_family_statuses["flow"].status is FreshnessStatus.FRESH
    assert first.snapshot.feature_family_statuses["basis_funding"].status is FreshnessStatus.FRESH
    assert first.snapshot.coverage == 1.0
    assert set(first.snapshot.available_feature_families) == {
        "flow",
        "spread",
        "basis_funding",
        "open_interest",
        "liquidations",
    }
    assert first.snapshot.flow_features is not None
    assert first.snapshot.flow_features["trade_count_15s"] == 5
    assert first.snapshot.spread_features is not None
    assert first.snapshot.spread_features["spread_bps"] > 0
    assert first.snapshot.basis_funding_features is not None
    assert first.snapshot.basis_funding_features["funding_rate_predicted"] == pytest.approx(0.0005)
    assert first.snapshot.open_interest_features is not None
    assert first.snapshot.open_interest_features["oi_pct_change"] == pytest.approx(0.1)
    assert first.snapshot.liquidation_features is not None
    assert first.snapshot.liquidation_features["liquidation_count"] == 2


def test_missing_and_source_unavailable_are_explicit() -> None:
    request = _request(
        [
            _event(
                "agg_trade",
                suffix="1",
                seconds_ago=1,
                payload={"price": 100, "quantity": 1, "buyer_maker": False},
            )
        ]
    )
    report = MarketIntelligenceService(_config()).evaluate(request, project_root=".")
    assert report.snapshot is not None
    health = report.snapshot.feature_family_statuses
    assert health["spread"].status is FreshnessStatus.MISSING
    assert health["basis_funding"].status is FreshnessStatus.SOURCE_UNAVAILABLE
    assert health["open_interest"].status is FreshnessStatus.SOURCE_UNAVAILABLE
    assert health["liquidations"].status is FreshnessStatus.SOURCE_UNAVAILABLE
    assert report.snapshot.status == "PARTIAL"


def test_stale_source_is_not_promoted_to_fresh() -> None:
    event = _event(
        "book_ticker",
        suffix="1",
        seconds_ago=120,
        payload={"best_bid": 100, "best_ask": 101, "bid_qty": 2, "ask_qty": 2},
    )
    report = MarketIntelligenceService(_config()).evaluate(_request([event]), project_root=".")
    assert report.snapshot is not None
    assert report.snapshot.feature_family_statuses["spread"].status is FreshnessStatus.STALE


def test_orderflow_buy_and_sell_pressure_and_zero_volume() -> None:
    events = [
        _event(
            "agg_trade",
            suffix="1",
            seconds_ago=2,
            payload={"price": 100, "quantity": 3, "buyer_maker": False},
        ),
        _event(
            "agg_trade",
            suffix="2",
            seconds_ago=1,
            payload={"price": 100, "quantity": 1, "buyer_maker": True},
        ),
    ]
    report = MarketIntelligenceService(_config()).evaluate(_request(events), project_root=".")
    assert report.snapshot is not None and report.snapshot.flow_features is not None
    assert report.snapshot.flow_features["flow_imbalance_5s"] == pytest.approx(0.5)
    assert report.snapshot.flow_features["buy_trade_count_5s"] == 1
    assert report.snapshot.flow_features["sell_trade_count_5s"] == 1


def test_crossed_book_is_invalid_and_zero_depth_is_safe() -> None:
    crossed = MarketEvent.model_validate(
        _event(
            "book_ticker",
            suffix="1",
            seconds_ago=1,
            payload={"best_bid": 101, "best_ask": 100, "bid_qty": 1, "ask_qty": 1},
        )
    )
    with pytest.raises(ValueError, match="book_ticker_crossed_or_locked"):
        build_spread_features(
            [crossed],
            decision_time_utc=DECISION,
            zscore_window_seconds=60,
            zscore_min_observations=5,
        )
    zero_depth = MarketEvent.model_validate(
        _event(
            "book_ticker",
            suffix="2",
            seconds_ago=1,
            payload={"best_bid": 100, "best_ask": 101, "bid_qty": 0, "ask_qty": 0},
        )
    )
    features = build_spread_features(
        [zero_depth],
        decision_time_utc=DECISION,
        zscore_window_seconds=60,
        zscore_min_observations=5,
    )
    assert features["top_book_imbalance"] is None
    assert features["microprice"] is None


def test_funding_semantics_are_not_mixed() -> None:
    missing_kind = _event(
        "mark_price",
        suffix="1",
        seconds_ago=10,
        payload={"mark_price": 101, "index_price": 100, "funding_rate": 0.001},
    )
    report = MarketIntelligenceService(_config()).evaluate(
        _request([missing_kind]), project_root="."
    )
    assert report.status.value == "BLOCKED"
    realized = _event(
        "mark_price",
        suffix="2",
        seconds_ago=10,
        payload={
            "mark_price": 101,
            "index_price": 100,
            "funding_rate": -0.001,
            "funding_rate_kind": "realized",
            "funding_interval_hours": 8,
        },
    )
    report = MarketIntelligenceService(_config()).evaluate(_request([realized]), project_root=".")
    assert report.snapshot is not None and report.snapshot.basis_funding_features is not None
    assert report.snapshot.basis_funding_features["funding_rate_predicted"] is None
    assert report.snapshot.basis_funding_features["funding_rate_realized"] == pytest.approx(-0.001)


def test_liquidation_aggregation_never_synthesizes_missing_events() -> None:
    assert build_liquidation_features([], decision_time_utc=DECISION, window_seconds=60) == {
        "long_liquidation_notional": 0.0,
        "short_liquidation_notional": 0.0,
        "net_liquidation_pressure": 0.0,
        "liquidation_count": 0,
        "liquidation_intensity": 0.0,
        "liquidation_imbalance": None,
        "window_seconds": 60,
    }


def test_no_write_cli_runs_offline_without_creating_data(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    (project / "config" / "research").mkdir(parents=True)
    (project / "config" / "research" / "market_intelligence.yaml").write_text(
        CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    fixture = project / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "request_id": "cli-test",
                "exchange": "binance_usdm_public",
                "symbol": "BTCUSDT",
                "decision_time_utc": DECISION.isoformat(),
                "events": [
                    _event(
                        "agg_trade",
                        suffix="1",
                        seconds_ago=1,
                        payload={"price": 100, "quantity": 1, "buyer_maker": False},
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("market_intelligence_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = module.main(
        [
            "--project-root",
            str(project),
            "--input-json",
            str(fixture),
            "--no-write",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["write_performed"] is False
    assert payload["network_calls_executed"] is False
    assert not (project / "data").exists()


def test_config_is_fail_closed_and_contains_no_secret_fields() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["mode"] == "research"
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["research_only"] is True
    assert payload["network_required"] is False
    for key in (
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "changes_model",
        "live_release_allowed",
        "canary_release_allowed",
        "writes_active_signals",
    ):
        assert payload[key] is False
    lowered = {str(key).casefold() for key in payload}
    assert not any("token" in key or "secret" in key or "api_key" in key for key in lowered)


def test_unapproved_public_source_is_blocked() -> None:
    event = _event(
        "agg_trade",
        suffix="1",
        seconds_ago=1,
        payload={"price": 100, "quantity": 1, "buyer_maker": False},
        source_id="unknown_public_source",
    )
    report = MarketIntelligenceService(_config()).evaluate(_request([event]), project_root=".")
    assert report.status.value == "BLOCKED"
    assert report.reason == "UNAUTHORIZED_PUBLIC_SOURCE:unknown_public_source"


def test_static_safety_has_no_order_private_network_or_runtime_mutations() -> None:
    denied_calls = {
        "create_order",
        "cancel_order",
        "submit_order",
        "send_order",
        "promote_model",
        "write_active_registry",
        "write_active_signals",
        "urlopen",
    }
    denied_imports = (
        "ccxt",
        "freqtrade",
        "requests",
        "httpx",
        "urllib",
        "smartcrypto.risk",
        "smartcrypto.execution.signal_producer",
    )
    findings: list[str] = []
    for path in sorted([*PACKAGE.glob("*.py"), CLI, ABLATION_CLI]):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                leaf = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                if leaf in denied_calls:
                    findings.append(f"{path.name}:{node.lineno}:call:{leaf}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(denied_imports):
                        findings.append(f"{path.name}:{node.lineno}:import:{alias.name}")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith(denied_imports)
            ):
                findings.append(f"{path.name}:{node.lineno}:import:{node.module}")
    assert findings == []
