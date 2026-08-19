from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.research.shadow_opportunity_engine.contracts import PositionSnapshot
from smartcrypto.research.shadow_opportunity_engine.engine import (
    SAFETY_FLAGS,
    ShadowOpportunityEngine,
    _attach_market_context,
    build_candidate,
    build_shadow_opportunity_engine_v1,
    inspect_market_source,
)
from smartcrypto.research.shadow_opportunity_engine.exit_efficiency import (
    analyze_exit_efficiency,
)
from smartcrypto.research.shadow_opportunity_engine.persistence import (
    append_ledger_idempotent,
    resolve_ledger_path,
    resolve_report_path,
    write_report,
)


SCRIPT_PATH = Path("scripts/build_shadow_opportunity_engine_v1.py")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
OBSERVED = "2026-08-18T10:00:00Z"


def market_evidence(timeframe: str = "1m", **overrides: Any) -> dict[str, Any]:
    timestamps = {
        "15s": ("2026-08-18T09:59:45Z", "2026-08-18T10:00:00Z"),
        "1m": ("2026-08-18T09:59:00Z", "2026-08-18T10:00:00Z"),
        "5m": ("2026-08-18T09:55:00Z", "2026-08-18T10:00:00Z"),
    }
    candle, available = timestamps[timeframe]
    payload: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "timeframe": timeframe,
        "timestamp": candle,
        "available_at_utc": available,
        "generated_at_utc": available,
        "source_hash": HASH_B,
        "source_row_identity": f"market-{timeframe}-1",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
    }
    payload.update(overrides)
    return payload


def candidate_event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "observed_at_utc": OBSERVED,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "source_hash": HASH_A,
        "source_row_identity": "candidate-row-1",
        "ranking_score": 0.75,
        "score_generated_at_utc": "2026-08-18T09:59:50Z",
        "score_available_at_utc": "2026-08-18T09:59:55Z",
        "score_source_hash": HASH_A,
        "model_version": "challenger-v1",
        "entry_market_regime": "trend_up",
        "regime_method": "rolling_return_volatility",
        "regime_lookback": "12x5m",
        "regime_generated_at_utc": "2026-08-18T09:59:55Z",
        "regime_available_at_utc": "2026-08-18T09:59:55Z",
        "regime_source_hash": HASH_C,
        "market_evidence": {"1m": market_evidence("1m")},
    }
    payload.update(overrides)
    return payload


def financial_event(value: float = 2.5, **overrides: Any) -> dict[str, Any]:
    payload = candidate_event(
        candidate_ev=value,
        financial_ev_semantics="EXPECTED_NET_PNL_USDT",
        financial_ev_generated_at_utc="2026-08-18T09:59:58Z",
        financial_ev_source_hash=HASH_C,
    )
    payload.update(overrides)
    return payload


def position(symbol: str = "BTCUSDT") -> PositionSnapshot:
    return PositionSnapshot(
        trade_id=7,
        pair=f"{symbol[:-4]}/USDT:USDT",
        symbol=symbol,
        side="SHORT",
        open_date="2026-08-18T09:00:00Z",
        stake_amount=50.0,
        leverage=2.0,
        open_rate=100.0,
        max_rate=101.0,
        min_rate=99.0,
        position_age_seconds=3600.0,
        capital_locked_usdt=50.0,
        capital_hours=50.0,
        estimated_notional_usdt=100.0,
    )


def create_paper_db(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            is_open INTEGER NOT NULL,
            pair TEXT NOT NULL,
            is_short INTEGER NOT NULL,
            open_date TEXT NOT NULL,
            close_date TEXT,
            close_profit_abs REAL,
            close_profit REAL,
            stake_amount REAL NOT NULL,
            max_stake_amount REAL,
            open_trade_value REAL,
            leverage REAL,
            open_rate REAL NOT NULL,
            close_rate REAL,
            max_rate REAL,
            min_rate REAL,
            fee_open REAL,
            fee_open_cost REAL,
            fee_close REAL,
            fee_close_cost REAL,
            funding_fees REAL,
            funding_fee_running REAL,
            realized_profit REAL,
            amount REAL,
            exit_reason TEXT,
            strategy TEXT,
            enter_tag TEXT,
            timeframe INTEGER
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            ft_trade_id INTEGER NOT NULL,
            ft_order_side TEXT NOT NULL,
            ft_is_open INTEGER NOT NULL,
            status TEXT,
            filled REAL,
            remaining REAL,
            order_id TEXT NOT NULL,
            ft_order_tag TEXT
        );
        """
    )
    connection.execute(
        """
        INSERT INTO trades VALUES (
            1, 0, 'BTC/USDT:USDT', 0, '2026-08-18T09:00:00Z',
            '2026-08-18T09:01:00Z', 1.0, 0.01, 100.0, 100.0, 100.0,
            2.0, 100.0, 101.0, 102.0, 99.0, 0.001, 0.1, 0.001, 0.1,
            0.0, 0.0, 1.0, 1.0, 'roi', 'Strategy', 'entry', 5
        )
        """
    )
    connection.execute(
        """
        INSERT INTO trades VALUES (
            2, 1, 'ETH/USDT:USDT', 1, '2026-08-18T09:30:00Z', NULL,
            NULL, NULL, 40.0, 40.0, 40.0, 3.0, 2000.0, NULL, 2010.0,
            1980.0, 0.001, 0.1, 0.001, NULL, 0.0, 0.0, 0.0, 0.02,
            NULL, 'Strategy', 'entry', 5
        )
        """
    )
    connection.execute(
        "INSERT INTO orders VALUES (1, 1, 'buy', 0, 'closed', 1.0, 0.0, 'o1', 'entry')"
    )
    connection.execute(
        "INSERT INTO orders VALUES (2, 1, 'sell', 0, 'closed', 1.0, 0.0, 'o2', 'roi')"
    )
    connection.commit()
    connection.close()
    return path


def closed_trade_frame(close_minutes: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": 1,
                "pair": "BTC/USDT:USDT",
                "is_short": 0,
                "open_date": pd.Timestamp("2026-08-18T09:00:00Z"),
                "close_date": pd.Timestamp("2026-08-18T09:00:00Z")
                + pd.Timedelta(minutes=close_minutes),
                "open_rate": 100.0,
                "close_rate": 104.0,
                "close_profit_abs": 4.0,
            }
        ]
    )


def candle_frame(*, long_path: bool = True, minutes: int = 1) -> pd.DataFrame:
    if minutes == 1:
        timestamps = pd.date_range("2026-08-18T09:00:00Z", periods=5, freq="15s")
    else:
        timestamps = pd.date_range(
            "2026-08-18T09:00:00Z", periods=minutes + 1, freq="1min"
        )
    if long_path:
        highs = [100.2, 110.0, 108.0, 106.0, 105.0] + [105.0] * max(0, len(timestamps) - 5)
        lows = [99.5, 100.2, 103.0, 102.0, 103.0] + [103.0] * max(0, len(timestamps) - 5)
        closes = [100.1, 109.0, 104.0, 103.0, 104.0] + [104.0] * max(0, len(timestamps) - 5)
    else:
        highs = [100.5] * len(timestamps)
        lows = [99.5] * len(timestamps)
        closes = [100.0] * len(timestamps)
    return pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "timestamp": timestamps,
            "open": [100.0] * len(timestamps),
            "high": highs[: len(timestamps)],
            "low": lows[: len(timestamps)],
            "close": closes[: len(timestamps)],
        }
    )


def load_cli() -> Any:
    spec = importlib.util.spec_from_file_location("shadow_opportunity_engine_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_id_and_event_replay_are_deterministic() -> None:
    first = build_candidate(candidate_event())
    second = build_candidate(candidate_event())
    assert first.candidate_id == second.candidate_id

    one = ShadowOpportunityEngine().process_events([candidate_event()])
    two = ShadowOpportunityEngine().process_events([candidate_event()])
    assert one == two


@pytest.mark.parametrize("timeframe", ["15s", "1m", "5m"])
def test_market_lineage_is_valid_per_supported_timeframe(timeframe: str) -> None:
    event = candidate_event(market_evidence={timeframe: market_evidence(timeframe)})
    assert build_candidate(event).market_lineage_valid is True


def test_future_market_score_and_regime_are_rejected() -> None:
    future_market = candidate_event(
        market_evidence={
            "1m": market_evidence("1m", generated_at_utc="2026-08-18T10:00:01Z")
        }
    )
    future_score = candidate_event(score_generated_at_utc="2026-08-18T10:00:01Z")
    future_regime = candidate_event(regime_generated_at_utc="2026-08-18T10:00:01Z")

    assert build_candidate(future_market).market_lineage_valid is False
    assert build_candidate(future_score).score_lineage_valid is False
    assert build_candidate(future_regime).regime_lineage_valid is False


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"observed_at_utc": "invalid"}, "candidate_observed_at_missing_or_invalid"),
        ({"symbol": None}, "candidate_symbol_missing"),
        ({"side": "FLAT"}, "candidate_side_invalid"),
        ({"source_hash": "invalid"}, "candidate_source_hash_missing_or_invalid"),
        ({"source_row_identity": None}, "candidate_source_row_identity_missing"),
    ],
)
def test_candidate_global_integrity_is_fail_closed(
    overrides: dict[str, Any],
    expected_error: str,
) -> None:
    candidate = build_candidate(candidate_event(**overrides))

    assert candidate.candidate_integrity_valid is False
    assert candidate.candidate_actionable_shadow is False
    assert candidate.lineage_status == "BLOCKED"
    assert expected_error in candidate.lineage_errors


def test_invalid_candidate_never_counts_as_missed_opportunity() -> None:
    engine = ShadowOpportunityEngine(positions=[position("BTCUSDT")])
    processed = engine.process_market_event(candidate_event(side="FLAT"))
    snapshot = engine.snapshot()

    assert processed["decision"]["candidate_actionable_shadow"] is False
    assert processed["decision"]["would_enter_if_capacity_available"] is False
    assert processed["decision"]["missed_due_to_pair_occupancy"] is False
    assert snapshot["opportunity_cost"]["missed_opportunity_count"] == 0


def test_score_available_at_is_mandatory_and_future_availability_is_rejected() -> None:
    missing = build_candidate(candidate_event(score_available_at_utc=None))
    future = build_candidate(
        candidate_event(score_available_at_utc="2026-08-18T10:00:01Z")
    )

    assert missing.score_lineage_valid is False
    assert missing.candidate_actionable_shadow is False
    assert future.score_lineage_valid is False
    assert future.candidate_actionable_shadow is False


def test_ranking_score_source_and_raw_scores_are_preserved() -> None:
    candidate = build_candidate(
        candidate_event(
            ranking_score=None,
            qlib_score=0.73,
            prob_up=0.61,
            signal_confidence=0.82,
        )
    )

    assert candidate.ranking_score == pytest.approx(0.73)
    assert candidate.ranking_score_source_field == "qlib_score"
    assert candidate.qlib_score == pytest.approx(0.73)
    assert candidate.prob_up == pytest.approx(0.61)
    assert candidate.signal_confidence == pytest.approx(0.82)


def test_regime_is_derived_from_preferred_market_evidence_with_lineage() -> None:
    event = candidate_event(
        entry_market_regime=None,
        regime_method=None,
        regime_lookback=None,
        regime_generated_at_utc=None,
        regime_available_at_utc=None,
        regime_source_hash=None,
        market_evidence={
            "1m": market_evidence(
                "1m",
                market_regime="range",
                regime_method="market_1m",
            ),
            "5m": market_evidence(
                "5m",
                market_regime="trend_up",
                regime_method="market_5m",
            ),
        },
    )
    candidate = build_candidate(event)

    assert candidate.regime == "trend_up"
    assert candidate.regime_source_timeframe == "5m"
    assert candidate.regime_method == "market_5m"
    assert candidate.regime_lookback is None
    assert candidate.regime_generated_at_utc == "2026-08-18T10:00:00Z"
    assert candidate.regime_available_at_utc == "2026-08-18T10:00:00Z"
    assert candidate.regime_source_hash == HASH_B
    assert candidate.regime_lineage_valid is True


def test_missing_timestamps_and_hashes_fail_closed() -> None:
    event = candidate_event(
        observed_at_utc=None,
        source_hash=None,
        score_source_hash=None,
        regime_source_hash=None,
        market_evidence={"1m": market_evidence("1m", source_hash=None)},
    )
    candidate = build_candidate(event)

    assert candidate.market_lineage_valid is False
    assert candidate.score_lineage_valid is False
    assert candidate.regime_lineage_valid is False
    assert candidate.lineage_errors


@pytest.mark.parametrize("column", ["prob_up", "qlib_score", "signal_confidence"])
def test_ordinal_scores_are_never_financial_ev(column: str) -> None:
    event = candidate_event(ranking_score=None, qlib_score=None)
    event[column] = 0.99
    candidate = build_candidate(event)

    assert candidate.ranking_score == pytest.approx(0.99)
    assert candidate.candidate_ev is None
    assert candidate.candidate_ev_status == "SOURCE_MISSING"
    assert candidate.to_dict()["ranking_score_semantics"] == "NON_FINANCIAL_ORDINAL"


def test_candidate_ev_requires_explicit_financial_point_in_time_contract() -> None:
    valid = build_candidate(financial_event(3.25))
    invalid = build_candidate(
        financial_event(3.25, financial_ev_semantics="DIRECTIONAL_SCORE")
    )

    assert valid.candidate_ev == pytest.approx(3.25)
    assert valid.candidate_ev_status == "AVAILABLE"
    assert invalid.candidate_ev is None


def test_open_position_capital_hours_use_stake_not_leverage(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    report = build_shadow_opportunity_engine_v1(
        project_root=tmp_path,
        paper_db=db,
        evaluated_at_utc=OBSERVED,
    )
    row = report["current_positions"][0]

    assert row["trade_id"] == 2
    assert row["capital_locked_usdt"] == 40.0
    assert row["capital_hours"] == pytest.approx(20.0)
    assert row["estimated_notional_usdt"] == 120.0
    assert row["capital_hours_basis"] == "stake_amount_usdt_times_position_age_hours"


def test_sqlite_read_is_byte_and_hash_invariant(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    before = db.read_bytes()
    report = build_shadow_opportunity_engine_v1(
        project_root=tmp_path,
        paper_db=db,
        evaluated_at_utc=OBSERVED,
    )

    assert db.read_bytes() == before
    assert report["sources"]["paper_db"]["hash_invariant"] is True
    assert report["writes_sqlite"] is False


def test_pair_and_global_capacity_are_recorded_without_orders() -> None:
    pair_engine = ShadowOpportunityEngine(positions=[position("BTCUSDT")])
    pair = pair_engine.process_market_event(candidate_event())["decision"]
    global_engine = ShadowOpportunityEngine(
        positions=[position("ETHUSDT")], shadow_capacity_limit=1
    )
    global_decision = global_engine.process_market_event(candidate_event())["decision"]

    assert pair["missed_due_to_pair_occupancy"] is True
    assert pair["missed_ev_status"] == "UNKNOWN"
    assert global_decision["missed_due_to_global_capacity"] is True
    assert pair["replacement_authorized"] is False
    assert pair["replacement_executed"] is False


def test_opportunity_book_orders_financial_ev_or_ordinal_without_mixing_semantics() -> None:
    financial = ShadowOpportunityEngine()
    financial.process_market_event(financial_event(1.0, source_row_identity="f1"))
    financial.process_market_event(financial_event(4.0, source_row_identity="f2"))
    financial_book = financial.snapshot()["opportunity_book"]

    ordinal = ShadowOpportunityEngine()
    ordinal.process_market_event(candidate_event(ranking_score=0.2, source_row_identity="o1"))
    ordinal.process_market_event(candidate_event(ranking_score=0.8, source_row_identity="o2"))
    ordinal_book = ordinal.snapshot()["opportunity_book"]

    assert financial_book["ranking_mode"] == "FINANCIAL_EV"
    assert financial_book["new_candidates"][0]["candidate_ev"] == 4.0
    assert ordinal_book["ranking_mode"] == "NON_FINANCIAL_ORDINAL"
    assert ordinal_book["new_candidates"][0]["ranking_score"] == 0.8


def test_alpha_decay_and_missing_history_are_ordinal_only() -> None:
    engine = ShadowOpportunityEngine()
    first = engine.process_market_event(candidate_event(ranking_score=1.0))["alpha_decay"]
    second = engine.process_market_event(
        candidate_event(
            observed_at_utc="2026-08-18T10:01:00Z",
            ranking_score=0.5,
            source_row_identity="candidate-row-2",
            market_evidence={
                "1m": market_evidence(
                    "1m",
                    timestamp="2026-08-18T10:00:00Z",
                    available_at_utc="2026-08-18T10:01:00Z",
                    generated_at_utc="2026-08-18T10:01:00Z",
                )
            },
        )
    )["alpha_decay"]

    assert first["alpha_decay_status"] == "INSUFFICIENT_HISTORY"
    assert second["alpha_age_seconds"] == 60.0
    assert second["score_decay_ratio"] == pytest.approx(0.5)
    assert "NON_FINANCIAL" in second["alpha_decay_score_semantics"]


def test_replacement_delta_requires_all_financial_and_cost_evidence() -> None:
    complete = financial_event(
        position_remaining_ev=0.5,
        remaining_position_ev_semantics="EXPECTED_NET_PNL_USDT",
        remaining_position_ev_generated_at_utc="2026-08-18T09:59:57Z",
        remaining_position_ev_source_hash=HASH_B,
        switching_cost_estimate=0.25,
        switching_cost_status="COMPLETE",
    )
    engine = ShadowOpportunityEngine(positions=[position()])
    decision = engine.process_market_event(complete)["decision"]

    missing_ev = ShadowOpportunityEngine(positions=[position()]).process_market_event(
        candidate_event(switching_cost_estimate=0.25, switching_cost_status="COMPLETE")
    )["decision"]
    missing_cost = ShadowOpportunityEngine(positions=[position()]).process_market_event(
        financial_event(
            position_remaining_ev=0.5,
            remaining_position_ev_semantics="EXPECTED_NET_PNL_USDT",
            remaining_position_ev_generated_at_utc="2026-08-18T09:59:57Z",
            remaining_position_ev_source_hash=HASH_B,
        )
    )["decision"]

    assert decision["replacement_delta"] == pytest.approx(1.75)
    assert decision["would_replace"] is True
    assert decision["replacement_authorized"] is False
    assert missing_ev["replacement_delta"] is None
    assert missing_cost["replacement_delta"] is None


def test_multiasset_shadow_never_changes_freqtrade() -> None:
    engine = ShadowOpportunityEngine(positions=[position("BTCUSDT")])
    engine.process_market_event(candidate_event(symbol="ETHUSDT", source_row_identity="eth"))
    engine.process_market_event(candidate_event(symbol="SOLUSDT", source_row_identity="sol"))
    multiasset = engine.snapshot()["multiasset"]

    assert multiasset["symbols_observed"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert multiasset["multiasset_shadow_ready"] is True
    assert multiasset["freqtrade_whitelist_changed"] is False


def test_exit_efficiency_computes_mfe_mae_times_and_giveback() -> None:
    report = analyze_exit_efficiency(
        closed_trade_frame(),
        {"15s": candle_frame()},
    )
    trade = report["trades"][0]

    assert trade["path_coverage_status"] == "SUFFICIENT_15S"
    assert trade["mfe"] == pytest.approx(0.10)
    assert trade["mae"] == pytest.approx(-0.005)
    assert trade["time_to_MFE"] == 15.0
    assert trade["time_to_MAE"] == 0.0
    assert trade["price_give_back"] == pytest.approx(0.06)
    assert report["metrics"]["analyzed_trade_count"] == 1
    assert report["full_or_sufficient_path_trade_count"] == 1
    assert report["partial_path_trade_count"] == 0
    assert report["policy_eligible_trade_count"] == 1


def test_candle_ending_after_trade_close_is_excluded_from_exit_metrics() -> None:
    closed = closed_trade_frame()
    closed.loc[0, "close_date"] = pd.Timestamp("2026-08-18T09:00:20Z")
    candles = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "timestamp": pd.to_datetime(
                ["2026-08-18T09:00:00Z", "2026-08-18T09:00:15Z"], utc=True
            ),
            "open": [100.0, 100.0],
            "high": [101.0, 999.0],
            "low": [99.0, 1.0],
            "close": [100.5, 500.0],
        }
    )

    report = analyze_exit_efficiency(closed, {"15s": candles})
    trade = report["trades"][0]

    assert trade["bars_15s"] == 1
    assert trade["mfe"] == pytest.approx(0.01)
    assert trade["mae"] == pytest.approx(-0.01)


def test_partial_path_is_excluded_from_metrics_and_policy_simulation() -> None:
    report = analyze_exit_efficiency(
        closed_trade_frame(),
        {"15s": candle_frame().iloc[:1].copy()},
    )
    trade = report["trades"][0]

    assert trade["path_coverage_status"] == "PARTIAL"
    assert trade["policy_eligible"] is False
    assert trade["mfe"] is None
    assert trade["mae"] is None
    assert report["metrics"]["analyzed_trade_count"] == 0
    assert report["policy_comparison"] == []
    assert report["partial_path_trade_count"] == 1
    assert report["policy_eligible_trade_count"] == 0


def test_exit_policy_grid_is_small_descriptive_and_intrabar_fail_closed() -> None:
    report = analyze_exit_efficiency(closed_trade_frame(), {"15s": candle_frame()})
    policies = {row["policy"]: row for row in report["policy_comparison"]}

    assert len(policies) == 10
    assert any(row["ambiguous_intrabar_count"] > 0 for row in policies.values())
    assert any(name.startswith("break_even_") for name in policies)
    assert any(name.startswith("trailing_") for name in policies)
    assert any(name.startswith("time_stop_") for name in policies)
    assert report["policy_search_performed"] is False
    assert all(row["net_pnl_delta_estimate"] is None for row in policies.values())


def test_time_stop_simulation_triggers_on_longer_path() -> None:
    report = analyze_exit_efficiency(
        closed_trade_frame(close_minutes=20),
        {"1m": candle_frame(minutes=20)},
    )
    policies = {row["policy"]: row for row in report["policy_comparison"]}

    assert policies["time_stop_15m"]["triggered_count"] == 1


def test_market_files_out_of_order_select_latest_temporal_row(tmp_path: Path) -> None:
    market_dir = tmp_path / "market"
    market_dir.mkdir()
    columns = ["symbol", "tf", "timestamp", "open", "high", "low", "close", "generated_at_utc", "market_regime"]
    pd.DataFrame(
        [["BTCUSDT", "5m", "2026-08-18T09:55:00Z", 100, 101, 99, 100.5, "2026-08-18T10:00:00Z", "latest"]],
        columns=columns,
    ).to_parquet(market_dir / "a-newer.parquet", index=False)
    pd.DataFrame(
        [["BTCUSDT", "5m", "2026-08-18T09:50:00Z", 90, 91, 89, 90.5, "2026-08-18T09:55:00Z", "older"]],
        columns=columns,
    ).to_parquet(market_dir / "z-older.parquet", index=False)

    descriptor, frame = inspect_market_source(market_dir, "5m")
    attached = _attach_market_context(
        candidate_event(market_evidence=None),
        {"5m": frame},
        {"5m": descriptor},
    )

    assert frame["timestamp"].is_monotonic_increasing
    assert attached["market_evidence"]["5m"]["market_regime"] == "latest"


def test_duplicate_candidate_does_not_duplicate_alpha_history() -> None:
    engine = ShadowOpportunityEngine()
    first = engine.process_market_event(candidate_event())
    duplicate = engine.process_market_event(candidate_event())

    assert first["duplicate_event"] is False
    assert duplicate["duplicate_event"] is True
    assert duplicate["decision"]["duplicate_event"] is True
    assert duplicate["alpha_decay"]["alpha_decay_status"] == "INSUFFICIENT_HISTORY"
    assert engine.snapshot()["opportunity_cost"]["candidate_count"] == 1


def test_different_model_version_does_not_share_alpha_decay_history() -> None:
    engine = ShadowOpportunityEngine()
    engine.process_market_event(candidate_event(model_version="model-a"))
    second = engine.process_market_event(
        candidate_event(
            observed_at_utc="2026-08-18T10:01:00Z",
            source_row_identity="candidate-row-2",
            model_version="model-b",
            score_generated_at_utc="2026-08-18T10:00:30Z",
            score_available_at_utc="2026-08-18T10:00:45Z",
            market_evidence={
                "1m": market_evidence(
                    "1m",
                    timestamp="2026-08-18T10:00:00Z",
                    available_at_utc="2026-08-18T10:01:00Z",
                    generated_at_utc="2026-08-18T10:01:00Z",
                )
            },
        )
    )

    assert second["alpha_decay"]["alpha_decay_status"] == "INSUFFICIENT_HISTORY"


def test_report_and_ledger_paths_are_restricted_and_ledger_is_idempotent(
    tmp_path: Path,
) -> None:
    report_path = resolve_report_path(tmp_path, "data/reports/report.json")
    ledger_path = resolve_ledger_path(tmp_path, "data/reports/ledger.jsonl")
    report = {"status": "ok", "write_performed": True}
    row = {"ledger_id": "ledger-1", "reason": "PAIR_OCCUPIED"}

    write_report(tmp_path, report_path, report)
    assert append_ledger_idempotent(tmp_path, ledger_path, [row]) == 1
    assert append_ledger_idempotent(tmp_path, ledger_path, [row]) == 0
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(ValueError, match="output_must_be_under_data_reports"):
        resolve_report_path(tmp_path, tmp_path / "outside.json")


def test_ledger_idempotency_is_serialized_across_concurrent_callers(
    tmp_path: Path,
) -> None:
    ledger_path = resolve_ledger_path(tmp_path, "data/reports/ledger.jsonl")
    row = {"ledger_id": "ledger-concurrent", "reason": "PAIR_OCCUPIED"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: append_ledger_idempotent(tmp_path, ledger_path, [row]),
                range(4),
            )
        )

    assert sum(results) == 1
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1


def test_cli_defaults_to_no_write_and_preserves_safety(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    cli = load_cli()

    result = cli.main(
        [
            "--project-root",
            str(tmp_path),
            "--paper-db",
            str(db),
            "--evaluated-at-utc",
            OBSERVED,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["write_performed"] is False
    assert payload["ledger_append_performed"] is False
    assert not (tmp_path / "data").exists()
    assert payload["safety"] == SAFETY_FLAGS
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False
    assert payload["writes_runtime"] is False


def test_cli_write_report_only_materializes_authorized_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    cli = load_cli()

    result = cli.main(
        [
            "--project-root",
            str(tmp_path),
            "--paper-db",
            str(db),
            "--evaluated-at-utc",
            OBSERVED,
            "--write-report",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    outputs = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert result == 0
    assert payload["write_performed"] is True
    assert "data/reports/shadow_opportunity_engine_v1.json" in outputs
    assert not any("runtime" in path for path in outputs)
    assert db.read_bytes()


def test_cli_preserves_ledger_append_audit_when_report_write_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = load_cli()

    def fake_build_report(**_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "opportunity_cost": {
                "ledger": [{"ledger_id": "ledger-after-append", "reason": "PAIR_OCCUPIED"}]
            },
        }

    def fail_report_write(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("report_write_failed")

    monkeypatch.setattr(
        cli,
        "_domain",
        lambda: (
            SAFETY_FLAGS,
            fake_build_report,
            (resolve_report_path, resolve_ledger_path),
            (fail_report_write, append_ledger_idempotent),
        ),
    )

    result = cli.main(
        [
            "--project-root",
            str(tmp_path),
            "--paper-db",
            str(tmp_path / "unused.sqlite"),
            "--evaluated-at-utc",
            OBSERVED,
            "--append-ledger",
            "--write-report",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["status"] == "blocked"
    assert payload["ledger_append_performed"] is True
    assert payload["ledger_rows_appended"] == 1
    assert payload["write_performed"] is True


def test_build_with_candidate_and_market_sources_is_point_in_time(
    tmp_path: Path,
) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    candidates = tmp_path / "candidates.csv"
    market = tmp_path / "market.parquet"
    pd.DataFrame([candidate_event(market_evidence=None)]).drop(
        columns=["market_evidence"]
    ).to_csv(candidates, index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "tf": "1m",
                "timestamp": "2026-08-18T09:59:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "generated_at_utc": "2026-08-18T10:00:00Z",
            },
            {
                "symbol": "BTCUSDT",
                "tf": "5m",
                "timestamp": "2026-08-18T09:55:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "generated_at_utc": "2026-08-18T10:00:00Z",
            },
        ]
    ).to_parquet(market, index=False)

    report = build_shadow_opportunity_engine_v1(
        project_root=tmp_path,
        paper_db=db,
        evaluated_at_utc=OBSERVED,
        candidate_source=candidates,
        market_data_1m=market,
        market_data_5m=market,
    )

    assert report["lineage"]["candidate_count"] == 1
    assert report["gates"]["market_data_1m_available"] is True
    assert report["gates"]["market_data_5m_available"] is True
    assert report["current_positions"][0]["symbol"] == "ETHUSDT"
    assert report["operational_authority"] is False
