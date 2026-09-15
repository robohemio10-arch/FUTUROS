from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    AIShadowDecision,
    FinalDecision,
    seal_decision_record,
)
from smartcrypto.learning.qlib_v3_prospective import (
    economic_shadow_decision_producer as shadow,
)
from smartcrypto.learning.qlib_v3_prospective.activation import Activation
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    EvidenceError,
    Identity,
)


BOUNDARY = datetime(
    2026,
    9,
    12,
    13,
    17,
    28,
    798985,
    tzinfo=UTC,
)
DECISION = BOUNDARY + timedelta(minutes=5)


def identity() -> Identity:
    return Identity(
        "qlib-v3-test-epoch",
        "1" * 64,
        "2" * 64,
        BOUNDARY.isoformat(),
        "a" * 40,
        "b" * 40,
        "3" * 64,
        "4" * 64,
        "5" * 64,
    )


def activation() -> Activation:
    return Activation(
        identity(),
        BOUNDARY,
        BOUNDARY - timedelta(minutes=1),
        "6" * 64,
        "7" * 64,
    )


def signal(
    side: str = "long",
) -> dict[str, object]:
    return {
        "signal_id": "signal-1",
        "candidate_id": "candidate-1",
        "correlation_id": "correlation-1",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": side,
        "risk_approved": True,
        "approved_stake_usdt": 25.0,
        "approved_leverage": 2.0,
        "risk_reasons": [
            "risk_manager_approved",
        ],
    }


def record(
    model_hash: str,
    source: dict[str, object],
):
    return seal_decision_record(
        {
            "event_id": "existing-v3-decision",
            "signal_id": source["signal_id"],
            "candidate_id": source["candidate_id"],
            "correlation_id": source[
                "correlation_id"
            ],
            "idempotency_key": (
                "existing-v3-decision"
            ),
            "runtime_mode": "paper",
            "pair": source["pair"],
            "symbol": source["symbol"],
            "side": source["side"],
            "feature_timestamp": (
                BOUNDARY
                + timedelta(minutes=1)
            ),
            "decision_timestamp": (
                BOUNDARY
                + timedelta(minutes=2)
            ),
            "feature_contract_version": (
                "test_contract_v1"
            ),
            "feature_hash": "8" * 64,
            "model_id": "test-model",
            "model_version": "test-v1",
            "model_hash": model_hash,
            "qlib_score": 0.2,
            "calibrated_probability": None,
            "expected_net_pnl": 0.2,
            "fast_stop_probability": None,
            "regime": "trend_up",
            "alignment": "unknown",
            "ai_shadow_decision": "ALLOW",
            "ai_shadow_reasons": ["test"],
            "risk_decision": "APPROVED",
            "risk_reasons": [
                "risk_manager_approved"
            ],
            "approved_stake_usdt": 25.0,
            "approved_leverage": 2.0,
            "final_decision": "ALLOW",
            "final_reasons": [
                "paper_approved"
            ],
            "operational_authority": False,
            "runtime_integration": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }
    )


class _NoopBooster:
    def predict(
        self,
        matrix: np.ndarray,
    ) -> np.ndarray:
        del matrix
        return np.asarray(
            [0.0],
            dtype=np.float64,
        )


def assets() -> shadow._Assets:
    return shadow._Assets(
        ("feature_side_long",),
        (0.0,),
        -0.1,
        "long",
        _NoopBooster(),
    )


def context(
    source: dict[str, object],
) -> shadow._Context:
    return shadow._Context(
        dict(source),
        BOUNDARY + timedelta(minutes=4),
        "9" * 64,
        "trend_up",
        (1.0,),
        "unit_test_canonical_cache",
    )


def test_existing_certified_decision_is_reused_without_scoring(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    active = activation()
    source = signal()
    existing = record(
        active.identity.model_artifact_sha256,
        source,
    )
    source["decision_ledger"] = {
        "decision_event_id": existing.event_id,
        "decision_payload_sha256": (
            existing.payload_sha256
        ),
    }
    baseline = copy.deepcopy(source)

    monkeypatch.setattr(
        shadow,
        "_load_assets",
        lambda *args, **kwargs: pytest.fail(
            "scorer must not be loaded"
        ),
    )

    result = shadow.resolve_shadow_decision_batch(
        project_root=tmp_path,
        signals=[source],
        existing_decisions=[existing],
        decision_timestamp_utc=DECISION,
        activation=active,
        config_source={
            "enabled": True,
            "freeze": "unused.json",
        },
    )

    assert result.report.status == "ok"
    assert (
        result.report.decision_source
        == "existing_certified_v3_decisions"
    )
    assert result.decisions == (existing,)
    assert source == baseline


def test_legacy_decision_is_replaced_by_certified_shadow_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    active = activation()
    source = signal()
    legacy = record(
        "f" * 64,
        source,
    )
    baseline = copy.deepcopy(source)

    monkeypatch.setattr(
        shadow,
        "_resolve_freeze_path",
        lambda *args, **kwargs: (
            tmp_path / "freeze.json"
        ),
    )
    monkeypatch.setattr(
        shadow,
        "_load_assets",
        lambda *args, **kwargs: assets(),
    )
    monkeypatch.setattr(
        shadow,
        "_build_contexts",
        lambda **kwargs: (
            context(source),
        ),
    )
    monkeypatch.setattr(
        shadow,
        "_predict_scores",
        lambda active_assets, contexts: (
            0.25,
        ),
    )

    result = shadow.resolve_shadow_decision_batch(
        project_root=tmp_path,
        signals=[source],
        existing_decisions=[legacy],
        decision_timestamp_utc=DECISION,
        activation=active,
        config_source={
            "enabled": True,
            "freeze": "freeze.json",
        },
    )

    assert result.report.status == "ok"
    assert result.report.selected_count == 1

    decision = result.decisions[0]
    assert (
        decision.model_hash
        == active.identity.model_artifact_sha256
    )
    assert decision.qlib_score == pytest.approx(
        0.25
    )
    assert decision.expected_net_pnl is None
    assert (
        decision.ai_shadow_decision
        is AIShadowDecision.ALLOW
    )
    assert (
        decision.final_decision
        is FinalDecision.ALLOW
    )
    assert (
        decision.operational_authority
        is False
    )
    assert (
        decision.runtime_integration
        is False
    )
    assert decision.sends_orders is False
    assert source == baseline


@pytest.mark.parametrize(
    ("side", "score", "reason"),
    [
        (
            "long",
            -0.2,
            "v3_score_lt_frozen_threshold",
        ),
        (
            "short",
            0.5,
            "v3_selection_side_ineligible",
        ),
    ],
)
def test_control_is_shadow_abstain_but_final_paper_allow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    side: str,
    score: float,
    reason: str,
) -> None:
    active = activation()
    source = signal(side)

    monkeypatch.setattr(
        shadow,
        "_resolve_freeze_path",
        lambda *args, **kwargs: (
            tmp_path / "freeze.json"
        ),
    )
    monkeypatch.setattr(
        shadow,
        "_load_assets",
        lambda *args, **kwargs: assets(),
    )
    monkeypatch.setattr(
        shadow,
        "_build_contexts",
        lambda **kwargs: (
            context(source),
        ),
    )
    monkeypatch.setattr(
        shadow,
        "_predict_scores",
        lambda active_assets, contexts: (
            score,
        ),
    )

    result = shadow.resolve_shadow_decision_batch(
        project_root=tmp_path,
        signals=[source],
        existing_decisions=[],
        decision_timestamp_utc=DECISION,
        activation=active,
        config_source={
            "enabled": True,
            "freeze": "freeze.json",
        },
    )

    decision = result.decisions[0]
    assert result.report.control_count == 1
    assert (
        decision.ai_shadow_decision
        is AIShadowDecision.ABSTAIN
    )
    assert decision.ai_shadow_reasons == (
        reason,
    )
    assert (
        decision.final_decision
        is FinalDecision.ALLOW
    )


def test_failure_blocks_only_shadow_evidence_and_returns_no_partial_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    active = activation()
    source = signal()

    monkeypatch.setattr(
        shadow,
        "_resolve_freeze_path",
        lambda *args, **kwargs: (
            tmp_path / "freeze.json"
        ),
    )
    monkeypatch.setattr(
        shadow,
        "_load_assets",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(RuntimeError("boom")),
    )

    result = shadow.resolve_shadow_decision_batch(
        project_root=tmp_path,
        signals=[source],
        existing_decisions=[],
        decision_timestamp_utc=DECISION,
        activation=active,
        config_source={
            "enabled": True,
            "freeze": "freeze.json",
        },
    )

    assert result.report.status == "blocked"
    assert (
        result.report.reason
        == "shadow_decision_boundary_failed:"
        "RuntimeError"
    )
    assert result.signals == ()
    assert result.decisions == ()


class _PositionalBooster:
    def __init__(
        self,
        expected: np.ndarray,
    ) -> None:
        self.expected = expected
        self.seen: np.ndarray | None = None

    def predict(
        self,
        matrix: Any,
    ) -> np.ndarray:
        assert isinstance(matrix, np.ndarray)
        assert matrix.dtype == np.float64
        np.testing.assert_array_equal(
            matrix,
            self.expected,
        )
        self.seen = matrix.copy()
        return np.asarray(
            [0.125],
            dtype=np.float64,
        )


def test_predict_scores_uses_frozen_positional_contract_not_dataframe_names() -> None:
    expected = np.asarray(
        [[0.25, 0.75]],
        dtype=np.float64,
    )
    booster = _PositionalBooster(expected)
    active_assets = shadow._Assets(
        (
            "semantic_feature_a",
            "semantic_feature_b",
        ),
        (0.0, 0.0),
        -0.1,
        "long",
        booster,
    )
    probe_context = shadow._Context(
        signal={},
        feature_timestamp=BOUNDARY,
        feature_hash="a" * 64,
        regime="unknown",
        vector=(0.25, 0.75),
        market_source="unit_test_canonical_cache",
    )

    scores = shadow._predict_scores(
        active_assets,
        [probe_context],
    )

    assert scores == (0.125,)
    assert booster.seen is not None


def test_predict_scores_blocks_feature_width_drift() -> None:
    active_assets = shadow._Assets(
        (
            "semantic_feature_a",
            "semantic_feature_b",
        ),
        (0.0, 0.0),
        -0.1,
        "long",
        _NoopBooster(),
    )
    bad_context = shadow._Context(
        signal={},
        feature_timestamp=BOUNDARY,
        feature_hash="a" * 64,
        regime="unknown",
        vector=(0.25,),
        market_source="unit_test_canonical_cache",
    )

    with pytest.raises(
        EvidenceError,
        match="shadow_feature_matrix_width_mismatch",
    ):
        shadow._predict_scores(
            active_assets,
            [bad_context],
        )


def _canonical_snapshot_row() -> pd.DataFrame:
    row: dict[str, object] = {
        "symbol": "BTCUSDT",
        "tf": "5m",
        "ts": BOUNDARY,
    }
    for column in shadow.market_context.MARKET_SOURCE_COLUMNS:
        row[column] = (
            "trend_up_normal_vol"
            if column == "market_regime"
            else 0.1
        )
    row["close"] = 100.0
    return pd.DataFrame([row])


def test_canonical_market_snapshot_uses_only_refresh_time_full_history_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    market_path = tmp_path / "market_features_60d.parquet"
    market_path.write_bytes(b"cache-key-only")
    snapshot = _canonical_snapshot_row()

    monkeypatch.setattr(
        shadow,
        "get_canonical_rebuilt_feature_snapshot",
        lambda path: snapshot,
    )

    prepared, source = shadow._canonical_market_snapshot(
        market_path,
        required_symbols={"BTCUSDT"},
    )

    assert source == "refresh_full_history_canonical_cache"
    assert len(prepared) == 1
    assert prepared.iloc[0]["symbol"] == "BTCUSDT"
    assert (
        prepared.iloc[0]["available_at_utc"].to_pydatetime()
        == BOUNDARY + timedelta(minutes=5)
    )


def test_canonical_market_snapshot_never_falls_back_to_synchronous_rematerialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    market_path = tmp_path / "market_features_60d.parquet"
    market_path.write_bytes(b"cache-key-only")

    monkeypatch.setattr(
        shadow,
        "get_canonical_rebuilt_feature_snapshot",
        lambda path: None,
    )

    with pytest.raises(
        EvidenceError,
        match="shadow_canonical_refresh_snapshot_unavailable",
    ):
        shadow._canonical_market_snapshot(
            market_path,
            required_symbols={"BTCUSDT"},
        )
