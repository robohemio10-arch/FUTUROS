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
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_outcome_resolver import (
    BOOTSTRAP_SAMPLES,
    SCHEMA_VERSION,
    _paired_bootstrap_delta,
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


def _authoritative_signal(boundary: datetime) -> tuple[dict[str, Any], datetime]:
    decision_time = boundary + timedelta(minutes=10)
    observed_at = decision_time + timedelta(seconds=1)
    candidate_id = "candidate-resolver-1"
    signal_id = "signal:phase13-signal-producer:resolver001"
    correlation_id = "correlation:resolver001"
    decision_event_id = "decision-event:resolver001"
    signal = {
        "candidate_id": candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "risk_approved": True,
        "generated_at": (decision_time - timedelta(seconds=2)).isoformat(),
        "valid_until": (decision_time + timedelta(minutes=30)).isoformat(),
        ATTESTATION_KEY: {
            "schema_version": ATTESTATION_SCHEMA,
            "materialization_sha256": "1" * 64,
            "source_signal_sha256": "2" * 64,
            "research_candidate_sha256": "3" * 64,
            "registry_candidate_sha256": "4" * 64,
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "research_signal_candidate_id": "signal_candidate_resolver001",
            "signal_instance_id": "signal-instance:resolver001",
            "producer_id": "phase13-signal-producer",
            "prospective_only": True,
            "authoritative_identity": True,
            "synthetic_identity": False,
            "trade_id_used_as_candidate_id": False,
        },
        DECISION_LEDGER_KEY: {
            "schema_version": "paper_candidate_trade_lineage_strict_decision_in_memory_v1",
            "decision_event_id": decision_event_id,
            "decision_payload_sha256": "5" * 64,
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "decision_timestamp": decision_time.isoformat(),
            "projection_type": "strict_in_memory",
            "writer_invoked": False,
            "writes_runtime": False,
            "operational_authority": False,
        },
    }
    return signal, observed_at


def _ledger_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    datetime,
]:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary)
    observer_report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
    )
    assert observer_report["status"] == "observing"
    ledger = _merge_ledger(existing=None, report=observer_report)
    return freeze, ledger, observer_report["observations"][0], boundary


def _trade_and_outcome(
    observation: dict[str, Any],
    *,
    trade_id: int = 123,
    net_pnl: float = 2.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    observed_at = datetime.fromisoformat(observation["observed_at_utc"])
    open_time = observed_at + timedelta(seconds=1)
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
