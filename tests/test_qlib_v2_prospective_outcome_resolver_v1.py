from __future__ import annotations

import ast
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from scripts.observe_qlib_v2_prospective_signals_v1 import _merge_ledger
from scripts.resolve_qlib_v2_prospective_outcomes_v1 import _validate_report_path
from smartcrypto.execution.decision_ledger_v4_2 import seal_decision_record
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.publication import (
    DECISION_LEDGER_KEY,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_outcome_resolver import (
    BOOTSTRAP_SAMPLES,
    SCHEMA_VERSION,
    _index_paper_trades,
    _paired_bootstrap_delta,
    _parse_freqtrade_snapshot_utc,
    _parse_utc,
    _promotion_gate,
    build_qlib_v2_prospective_outcome_resolution_v1,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    build_qlib_v2_prospective_paper_confirmation_v1,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_signal_observer import (
    build_qlib_v2_prospective_signal_observer_v1,
)


MODULE_PATH = Path(
    "smartcrypto/learning/paper_autolearning/qlib_v2_prospective_outcome_resolver.py"
)


def _market_and_rows(count: int = 450) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle_count = 500 + count * 10 + 30
    candles: list[dict[str, object]] = []
    price = 50000.0
    closes: list[float] = []
    for index in range(candle_count):
        timestamp = base + timedelta(minutes=5 * index)
        wave = 20.0 * math.sin(index / 13.0) + 8.0 * math.sin(index / 5.0)
        close = max(100.0, price + wave)
        candles.append(
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "tf": "5m",
                "ts": timestamp,
                "open": price,
                "high": max(price, close) + 10.0,
                "low": min(price, close) - 10.0,
                "close": close,
                "volume": 1000.0 + 100.0 * math.sin(index / 7.0) + 10.0 * (index % 9),
            }
        )
        closes.append(close)
        price = close

    rows: list[dict[str, object]] = []
    for index in range(count):
        candle_index = 500 + index * 10
        open_time = base + timedelta(minutes=5 * (candle_index + 1))
        close_time = open_time + timedelta(minutes=20)
        side = "long" if index % 2 == 0 else "short"
        ret_5 = closes[candle_index] / closes[candle_index - 5] - 1.0
        net_pnl = (3.0 if ret_5 > 0 else -1.0) if side == "long" else -2.0
        rows.append(
            {
                "event_id": f"event-{index:04d}",
                "trade_id": f"trade-{index:04d}",
                "is_closed": True,
                "validation_status": "ok",
                "symbol_norm": "BTCUSDT",
                "side": side,
                "open_time_utc": open_time.isoformat(),
                "close_time_utc": close_time.isoformat(),
                "entry_price": 50000.0,
                "quantity": 0.002,
                "notional": 100.0,
                "leverage": 2.0,
                "net_pnl": net_pnl,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return pd.DataFrame(candles), rows


def _predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id

    def score(frame: pd.DataFrame) -> np.ndarray:
        signal = frame["feature_ret_5_side"].to_numpy(dtype=float)
        is_long = frame["feature_side_long"].to_numpy(dtype=float) >= 0.5
        return np.where(is_long, signal, 1_000_000.0)

    return score(calibration_x), score(test_x)


def _boundary(rows: list[dict[str, object]], index: int = 330) -> datetime:
    return datetime.fromisoformat(str(rows[index]["open_time_utc"]))


def _freeze(
    market: pd.DataFrame,
    rows: list[dict[str, object]],
    boundary: datetime,
) -> dict[str, Any]:
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=Path("."),
        rows=rows,
        market_rows=market,
        prospective_start_utc=boundary,
        certified_implementation_commit="a" * 40,
        certified_ci_run_id=123456789,
        certified_ci_completed_at_utc=boundary - timedelta(seconds=1),
        freeze_materialized_at_utc=boundary,
        additional_execution_stress_bps=5.0,
        predictor=_predictor,
    )
    freeze = report["freeze_spec"]
    assert isinstance(freeze, dict)
    return freeze


def _authoritative_signal(
    boundary: datetime,
) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    decision_time = boundary + timedelta(minutes=10)
    observed_at = decision_time + timedelta(seconds=1)
    generated_at = decision_time - timedelta(seconds=2)
    candidate_id = "candidate:phase13-signal-producer:resolver001"
    signal_id = "signal:phase13-signal-producer:resolver001"
    correlation_id = "correlation:resolver001"
    decision_event_id = "decision-event:resolver001"
    score = 0.42

    record = seal_decision_record(
        {
            "event_id": decision_event_id,
            "signal_id": signal_id,
            "candidate_id": candidate_id,
            "correlation_id": correlation_id,
            "idempotency_key": "idempotency:resolver001",
            "runtime_mode": "paper",
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": "long",
            "feature_timestamp": generated_at,
            "decision_timestamp": decision_time,
            "feature_contract_version": "paper-signal-observation-lineage-v1",
            "feature_hash": "1" * 64,
            "model_id": "qlib_lgbm_v1",
            "model_version": "qlib_lgbm_v1",
            "model_hash": "2" * 64,
            "qlib_score": score,
            "calibrated_probability": 0.71,
            "expected_net_pnl": None,
            "fast_stop_probability": None,
            "regime": "unknown",
            "alignment": "unknown",
            "ai_shadow_decision": "NOT_EVALUATED",
            "ai_shadow_reasons": (),
            "risk_decision": "APPROVED",
            "risk_reasons": (),
            "approved_stake_usdt": 50.0,
            "approved_leverage": 2.0,
            "final_decision": "ALLOW",
            "final_reasons": ("risk_manager_approved",),
            "operational_authority": False,
            "runtime_integration": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }
    ).model_dump(mode="json")

    signal = {
        "candidate_id": candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": score,
        "model_version": "qlib_lgbm_v1",
        "risk_approved": True,
        "generated_at": generated_at.isoformat(),
        "valid_until": (decision_time + timedelta(minutes=30)).isoformat(),
        DECISION_LEDGER_KEY: {
            "schema_version": "decision_ledger_active_signal_envelope_v1",
            "decision_event_id": decision_event_id,
            "decision_payload_sha256": record["payload_sha256"],
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "decision_timestamp": decision_time.isoformat(),
        },
    }
    return signal, record, observed_at

def _ledger_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    datetime,
]:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, decision_record, observed_at = _authoritative_signal(boundary)
    observer_report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        decision_ledger_records=[decision_record],
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
        score_completed_at_utc=observed_at + timedelta(milliseconds=100),
    )
    assert observer_report["status"] == "observing"
    ledger = _merge_ledger(
        existing=None,
        report=observer_report,
        recorded_at_utc=observed_at + timedelta(milliseconds=200),
    )
    return freeze, ledger, ledger["observations"][0], boundary


def _trade_and_outcome(
    observation: dict[str, Any],
    *,
    trade_id: int = 123,
    net_pnl: float = 2.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    recorded_at = datetime.fromisoformat(observation["ledger_recorded_at_utc"])
    open_time = recorded_at + timedelta(seconds=1)
    close_time = open_time + timedelta(minutes=20)
    trade = {
        "id": trade_id,
        "pair": "BTC/USDT:USDT",
        "is_short": 0,
        "is_open": 0,
        "open_date": open_time.isoformat(),
        "close_date": close_time.isoformat(),
        "enter_tag": (
            "smartcrypto_long|decision_event_id="
            + str(observation["decision_event_id"])
        ),
    }
    outcome = {
        "event_id": f"outcome-{trade_id}",
        "trade_id": trade_id,
        "is_closed": True,
        "validation_status": "ok",
        "symbol_norm": "BTCUSDT",
        "side": "long",
        "open_time_utc": open_time.isoformat(),
        "close_time_utc": close_time.isoformat(),
        "entry_price": 50000.0,
        "quantity": 0.002,
        "notional": 100.0,
        "leverage": 2.0,
        "net_pnl": net_pnl,
        "trading_fee": 0.0,
        "funding_fee": 0.0,
    }
    return trade, outcome


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-09 23:12:06.123456",
        "2026-09-09T23:12:06.123456",
        "2026-09-09T23:12:06.123456Z",
        "2026-09-09T23:12:06.123456+00:00",
    ],
)
def test_freqtrade_snapshot_timestamp_normalizes_supported_utc_forms(
    value: str,
) -> None:
    parsed = _parse_freqtrade_snapshot_utc(value, "paper_trade_open_date")

    assert parsed == datetime(2026, 9, 9, 23, 12, 6, 123456, tzinfo=UTC)
    assert parsed.tzinfo is UTC


def test_freqtrade_snapshot_timestamp_interprets_naive_datetime_as_utc() -> None:
    value = datetime(2026, 9, 9, 23, 12, 6, 123456)

    parsed = _parse_freqtrade_snapshot_utc(value, "paper_trade_open_date")

    assert parsed == value.replace(tzinfo=UTC)


def test_freqtrade_snapshot_timestamp_rejects_explicit_non_utc_offset() -> None:
    with pytest.raises(ValueError, match="timestamp_not_utc:paper_trade_open_date"):
        _parse_freqtrade_snapshot_utc(
            "2026-09-09T20:12:06.123456-03:00",
            "paper_trade_open_date",
        )


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp"])
def test_freqtrade_snapshot_timestamp_rejects_missing_or_invalid_values(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="^timestamp_(missing|invalid):"):
        _parse_freqtrade_snapshot_utc(value, "paper_trade_open_date")


def test_generic_utc_parser_remains_strict_for_naive_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="timestamp_not_timezone_aware:prospective_timestamp",
    ):
        _parse_utc("2026-09-09T23:12:06.123456", "prospective_timestamp")


def test_freqtrade_naive_trade_rows_are_indexed_without_timezone_blocker() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, _ = _trade_and_outcome(observation)
    trade["open_date"] = "2026-09-09 23:12:06.123456"
    trade["close_date"] = "2026-09-09 23:32:06.123456"

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[],
        paper_trade_source_sha256="d" * 64,
    )

    assert report["status"] == "collecting"
    assert report["linked_paper_trade_count"] == 1
    assert report["resolved_decision_count"] == 0
    assert report["unresolved_observations"][0]["reason"] == (
        "outcome_event_not_yet_available"
    )
    assert not any(
        "timestamp_not_timezone_aware:paper_trade" in blocker
        for blocker in report["blockers"]
    )


def test_freqtrade_snapshot_close_before_open_remains_blocked() -> None:
    _, _, observation, _ = _ledger_fixture()
    trade, _ = _trade_and_outcome(observation)
    trade["open_date"] = "2026-09-09 23:32:06.123456"
    trade["close_date"] = "2026-09-09 23:12:06.123456"

    _, blockers = _index_paper_trades([trade])

    assert blockers == ["paper_trade_invalid:0:paper_trade_close_before_open"]


def test_duplicate_paper_trade_id_remains_blocked() -> None:
    _, _, observation, _ = _ledger_fixture()
    trade, _ = _trade_and_outcome(observation)
    second = dict(trade)
    second["enter_tag"] = (
        "smartcrypto_long|decision_event_id=decision-event:resolver002"
    )

    _, blockers = _index_paper_trades([trade, second])

    assert blockers == ["duplicate_paper_trade_id:123"]


def test_exact_identity_chain_resolves_one_paper_outcome() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
        paper_trade_source_sha256="d" * 64,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "collecting"
    assert report["linked_paper_trade_count"] == 1
    assert report["resolved_decision_count"] == 1
    assert report["scorer_coverage"] == 1.0
    resolved = report["resolved_observations"][0]
    assert resolved["signal_id"] == observation["signal_id"]
    assert resolved["decision_event_id"] == observation["decision_event_id"]
    assert resolved["paper_trade_id"] == 123
    assert resolved["observation_precedes_trade_open"] is True
    assert resolved["score_completion_precedes_trade_open"] is True
    assert resolved["ledger_recording_precedes_trade_open"] is True
    assert report["promotion_allowed"] is False
    assert report["prospective_profit_certified"] is False
    assert report["writes_runtime"] is False


def test_trade_without_explicit_decision_event_does_not_resolve() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    trade["enter_tag"] = "smartcrypto_long"

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "collecting"
    assert report["linked_paper_trade_count"] == 0
    assert report["resolved_decision_count"] == 0
    assert report["unresolved_observations"][0]["reason"] == "paper_trade_not_yet_linked"


def test_ambiguous_decision_event_tag_blocks_resolution() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    event_id = observation["decision_event_id"]
    trade["enter_tag"] = (
        f"smartcrypto_long|decision_event_id={event_id}|decision_event_id={event_id}"
    )

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "blocked"
    assert "paper_trade_decision_event_id_invalid" in report["reason"]


def test_duplicate_trade_for_same_decision_event_blocks_resolution() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    second = dict(trade)
    second["id"] = 124

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade, second],
        outcome_rows=[outcome],
    )

    assert report["status"] == "blocked"
    assert report["reason"].startswith("duplicate_paper_trade_decision_event_id")


def test_outcome_semantic_mismatch_blocks_after_exact_identity_resolution() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    outcome["symbol_norm"] = "ETHUSDT"

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "outcome_symbol_mismatch:123"


def test_outcome_side_mismatch_still_blocks_after_exact_identity_resolution() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    outcome["side"] = "short"

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "outcome_side_mismatch:123"


def test_observation_after_trade_open_is_excluded_from_prospective_evidence() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    observed_at = datetime.fromisoformat(observation["observed_at_utc"])
    trade_open = observed_at - timedelta(milliseconds=1)
    trade_close = trade_open + timedelta(minutes=20)
    trade["open_date"] = trade_open.isoformat()
    trade["close_date"] = trade_close.isoformat()
    outcome["open_time_utc"] = trade_open.isoformat()
    outcome["close_time_utc"] = trade_close.isoformat()

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "collecting"
    assert report["linked_paper_trade_count"] == 1
    assert report["resolved_decision_count"] == 0
    assert report["late_observation_count"] == 1
    assert report["unresolved_observations"][0]["reason"] == "observation_after_trade_open"


def test_score_completion_after_trade_open_is_excluded_from_prospective_evidence() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    observed_at = datetime.fromisoformat(observation["observed_at_utc"])
    trade_open = observed_at + timedelta(milliseconds=50)
    trade_close = trade_open + timedelta(minutes=20)
    trade["open_date"] = trade_open.isoformat()
    trade["close_date"] = trade_close.isoformat()
    outcome["open_time_utc"] = trade_open.isoformat()
    outcome["close_time_utc"] = trade_close.isoformat()

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "collecting"
    assert report["resolved_decision_count"] == 0
    assert report["late_score_completion_count"] == 1
    assert report["late_ledger_recording_count"] == 0
    assert report["unresolved_observations"][0]["reason"] == (
        "score_completed_after_trade_open"
    )


def test_ledger_recording_after_trade_open_is_excluded_from_prospective_evidence() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    score_completed = datetime.fromisoformat(observation["score_completed_at_utc"])
    trade_open = score_completed + timedelta(milliseconds=50)
    trade_close = trade_open + timedelta(minutes=20)
    trade["open_date"] = trade_open.isoformat()
    trade["close_date"] = trade_close.isoformat()
    outcome["open_time_utc"] = trade_open.isoformat()
    outcome["close_time_utc"] = trade_close.isoformat()

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=ledger,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "collecting"
    assert report["resolved_decision_count"] == 0
    assert report["late_score_completion_count"] == 0
    assert report["late_ledger_recording_count"] == 1
    assert report["unresolved_observations"][0]["reason"] == (
        "ledger_recorded_after_trade_open"
    )


def test_observer_ledger_hash_mutation_blocks_resolution() -> None:
    freeze, ledger, observation, _ = _ledger_fixture()
    trade, outcome = _trade_and_outcome(observation)
    mutated = dict(ledger)
    mutated["ledger_sha256"] = "0" * 64

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        observer_ledger=mutated,
        paper_trade_rows=[trade],
        outcome_rows=[outcome],
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "prospective_signal_ledger_sha256_mismatch"


def test_promotion_gate_can_pass_economic_thresholds_but_never_promotes() -> None:
    resolved = [
        {"selected": True, "stressed_net_pnl": 2.0}
        for _ in range(50)
    ] + [
        {"selected": False, "stressed_net_pnl": -1.0}
        for _ in range(200)
    ]
    bootstrap = _paired_bootstrap_delta(
        resolved=resolved,
        samples=BOOTSTRAP_SAMPLES,
        seed=20260909,
    )
    gate = _promotion_gate(
        resolved_count=250,
        observation_days=46.0,
        selected_count=50,
        scorer_coverage=1.0,
        treatment={
            "net_pnl": 100.0,
            "expectancy": 2.0,
            "profit_factor": 2.0,
        },
        delta_net=200.0,
        bootstrap=bootstrap,
    )

    assert bootstrap["delta_net_pnl_ci95_lower"] > 0.0
    assert gate["all_economic_evidence_gates_passed"] is True
    assert gate["promotion_allowed"] is False
    assert gate["operational_authority"] is False


def test_bootstrap_is_deterministic_for_same_resolved_sample() -> None:
    resolved = [
        {"selected": False, "stressed_net_pnl": -1.0},
        {"selected": True, "stressed_net_pnl": 2.0},
        {"selected": False, "stressed_net_pnl": -0.5},
    ]
    first = _paired_bootstrap_delta(resolved=resolved, samples=200, seed=123)
    second = _paired_bootstrap_delta(resolved=resolved, samples=200, seed=123)
    assert first == second


def test_resolution_report_write_path_is_confined_to_research_namespace(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    valid = root / "data" / "research" / "qlib_v2" / "resolution.json"
    _validate_report_path(root, valid)

    with pytest.raises(RuntimeError, match="outside_research_root"):
        _validate_report_path(root, root / "data" / "runtime" / "resolution.json")
    with pytest.raises(RuntimeError, match="must_be_json"):
        _validate_report_path(
            root,
            root / "data" / "research" / "qlib_v2" / "resolution.txt",
        )


def test_resolver_module_is_read_only_and_has_no_private_exchange_or_order_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden_prefixes = (
        "freqtrade.exchange",
        "freqtrade.persistence",
        "smartcrypto.execution.order_manager",
        "smartcrypto.risk.risk_manager",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
    )
    assert not any(
        any(name.startswith(prefix) for prefix in forbidden_prefixes)
        for name in imports
    )
    assert "?mode=ro" in source
    assert "PRAGMA query_only = ON" in source
