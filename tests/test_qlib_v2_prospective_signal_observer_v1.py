from __future__ import annotations

import ast
import hashlib
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from scripts.observe_qlib_v2_prospective_signals_v1 import (
    _merge_ledger,
    _validate_ledger_path,
)
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    build_qlib_v2_prospective_paper_confirmation_v1,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_signal_observer import (
    SCHEMA_VERSION,
    build_qlib_v2_prospective_signal_observer_v1,
)


MODULE_PATH = Path(
    "smartcrypto/learning/paper_autolearning/qlib_v2_prospective_signal_observer.py"
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
        additional_execution_stress_bps=0.0,
        predictor=_predictor,
    )
    assert report["freeze_provenance_complete"] is True
    freeze = report["freeze_spec"]
    assert isinstance(freeze, dict)
    return freeze


def _authoritative_signal(
    *,
    boundary: datetime,
    signal_id: str = "signal:phase13-signal-producer:observer001",
    decision_event_id: str = "decision-event:observer001",
    side: str = "long",
) -> tuple[dict[str, Any], datetime]:
    decision_time = boundary + timedelta(minutes=10)
    generated_at = decision_time - timedelta(seconds=2)
    valid_until = decision_time + timedelta(minutes=30)
    candidate_id = "candidate-observer-1"
    correlation_id = "correlation:observer001"
    signal_candidate_id = "signal_candidate_observer001"
    signal_instance_id = "signal-instance:observer001"

    signal = {
        "candidate_id": candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": side,
        "risk_approved": True,
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        ATTESTATION_KEY: {
            "schema_version": ATTESTATION_SCHEMA,
            "materialization_sha256": "1" * 64,
            "source_signal_sha256": "2" * 64,
            "research_candidate_sha256": "3" * 64,
            "registry_candidate_sha256": "4" * 64,
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "research_signal_candidate_id": signal_candidate_id,
            "signal_instance_id": signal_instance_id,
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
    return signal, decision_time + timedelta(seconds=1)


def _observe(
    *,
    market: pd.DataFrame,
    rows: list[dict[str, object]],
    freeze: dict[str, Any],
    signal: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return build_qlib_v2_prospective_signal_observer_v1(
        project_root=Path("."),
        freeze_spec=freeze,
        signal_payload={"signals": [signal]},
        rows=rows,
        market_rows=market,
        predictor=_predictor,
        observed_at_utc=observed_at,
    )


def test_observer_scores_authoritative_signal_with_ex_ante_identity() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert report["status"] == "observing"
    assert report["freeze_spec_verified"] is True
    assert report["model_reconstruction_verified"] is True
    assert report["observation_count"] == 1
    observation = report["observations"][0]
    assert observation["schema_version"] == SCHEMA_VERSION
    assert observation["signal_id"] == signal["signal_id"]
    assert observation["decision_event_id"] == signal[DECISION_LEDGER_KEY]["decision_event_id"]
    assert observation["observed_at_utc"] == observed_at.isoformat()
    assert observation["signal_active_at_observation"] is True
    assert observation["post_outcome_fields_present"] is False
    assert len(observation["signal_snapshot_sha256"]) == 64
    assert len(observation["feature_vector_sha256"]) == 64
    assert len(observation["observation_sha256"]) == 64
    assert "trade_id" not in observation
    assert "paper_trade_id" not in observation
    assert "realized_net_pnl" not in observation
    assert report["writes_runtime"] is False
    assert report["sends_orders"] is False


def test_post_boundary_outcome_mutation_cannot_change_signal_observation() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)

    first = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    mutated = [dict(row) for row in rows]
    for row in mutated[331:]:
        row["net_pnl"] = float(row["net_pnl"]) * -1000.0

    second = _observe(
        market=market,
        rows=mutated,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert first["status"] == second["status"] == "observing"
    assert first["observations"] == second["observations"]
    assert first["calibration_score_fingerprint_sha256"] == second[
        "calibration_score_fingerprint_sha256"
    ]


def test_pre_boundary_outcome_mutation_blocks_frozen_reconstruction() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)
    mutated = [dict(row) for row in rows]
    mutated[100]["net_pnl"] = float(mutated[100]["net_pnl"]) + 0.5

    report = _observe(
        market=market,
        rows=mutated,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "frozen_dataset_reconstruction_mismatch"


def test_identity_mismatch_blocks_entire_evidence_batch() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)
    signal[ATTESTATION_KEY] = dict(signal[ATTESTATION_KEY])
    signal[ATTESTATION_KEY]["signal_id"] = "signal:other"

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "lineage_attestation_identity_mismatch:signal_id" in report["reason"]
    assert report["observation_count"] == 0


def test_post_outcome_field_in_active_signal_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)
    signal["trade_id"] = 123

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "post_outcome_fields_forbidden:trade_id" in report["reason"]


def test_observer_cannot_backdate_before_decision() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _ = _authoritative_signal(boundary=boundary)
    decision_time = datetime.fromisoformat(signal[DECISION_LEDGER_KEY]["decision_timestamp"])

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=decision_time - timedelta(milliseconds=1),
    )

    assert report["status"] == "blocked"
    assert "observer_timestamp_before_decision" in report["reason"]


def test_expired_signal_is_not_accepted_as_ex_ante_observation() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, _ = _authoritative_signal(boundary=boundary)
    valid_until = datetime.fromisoformat(signal["valid_until"])

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=valid_until + timedelta(microseconds=1),
    )

    assert report["status"] == "blocked"
    assert "signal_expired_before_observation" in report["reason"]


def test_non_utc_authoritative_timestamp_is_rejected() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)
    decision_time = datetime.fromisoformat(signal[DECISION_LEDGER_KEY]["decision_timestamp"])
    local_time = decision_time.astimezone(timezone(timedelta(hours=-3)))
    signal[DECISION_LEDGER_KEY] = dict(signal[DECISION_LEDGER_KEY])
    signal[DECISION_LEDGER_KEY]["decision_timestamp"] = local_time.isoformat()

    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    assert report["status"] == "blocked"
    assert "timestamp_not_utc:decision_timestamp" in report["reason"]


def test_ledger_merge_is_idempotent_and_identity_mutation_fails_closed() -> None:
    market, rows = _market_and_rows()
    boundary = _boundary(rows)
    freeze = _freeze(market, rows, boundary)
    signal, observed_at = _authoritative_signal(boundary=boundary)
    report = _observe(
        market=market,
        rows=rows,
        freeze=freeze,
        signal=signal,
        observed_at=observed_at,
    )

    first = _merge_ledger(existing=None, report=report)
    second = _merge_ledger(existing=first, report=report)
    assert first["observation_count"] == 1
    assert second["observation_count"] == 1
    assert second["new_observation_count"] == 0
    assert second["idempotent_observation_count"] == 1

    mutated_report = dict(report)
    changed = dict(report["observations"][0])
    changed["observation_sha256"] = hashlib.sha256(b"mutation").hexdigest()
    mutated_report["observations"] = [changed]
    with pytest.raises(RuntimeError, match="observer_signal_identity_mutation"):
        _merge_ledger(existing=first, report=mutated_report)


def test_ledger_write_path_is_confined_to_research_namespace(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    valid = root / "data" / "research" / "qlib_v2" / "ledger.json"
    _validate_ledger_path(root, valid)

    with pytest.raises(RuntimeError, match="outside_research_root"):
        _validate_ledger_path(root, root / "data" / "runtime" / "ledger.json")
    with pytest.raises(RuntimeError, match="must_be_json"):
        _validate_ledger_path(root, root / "data" / "research" / "qlib_v2" / "ledger.txt")


def test_observer_module_has_no_execution_writer_or_private_exchange_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden_prefixes = (
        "freqtrade.persistence",
        "freqtrade.exchange",
        "smartcrypto.execution.order_manager",
        "smartcrypto.risk.risk_manager",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
    )
    assert not any(
        any(name.startswith(prefix) for prefix in forbidden_prefixes)
        for name in imports
    )
