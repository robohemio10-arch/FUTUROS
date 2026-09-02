from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.research.ensemble_abstention import (
    AIShadowDecision,
    AibotParityResearchConfig,
    EnsembleAbstentionRequest,
    EnsembleStatus,
    QlibDirectionalEvidence,
    RegimeAlignment,
    RegimeLabel,
    ResearchAction,
    evaluate_ensemble_abstention,
    load_aibot_parity_config,
    run_ensemble_abstention,
)
from smartcrypto.research.market_intelligence.contracts import (
    FeatureFamilyHealth,
    FreshnessStatus,
    MarketIntelligenceSnapshot,
)
from smartcrypto.research.research_council.contracts import ContextIntelligenceSnapshot

UTC = timezone.utc
DECISION = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def qlib(*, side: str = "long", score: float = 0.6, regime: str = "trend_up", confidence: float = 0.8, available_at: datetime | None = None) -> dict:
    prob_up = (score + 1.0) / 2.0
    return {
        "evidence_id": "qlib-1",
        "source_id": "qlib-research",
        "model_version": "qlib-wrapper-test",
        "symbol": "BTCUSDT",
        "generated_at_utc": DECISION - timedelta(seconds=2),
        "available_at_utc": available_at or DECISION - timedelta(seconds=1),
        "valid_until_utc": DECISION + timedelta(minutes=5),
        "proposed_side": side,
        "score": score,
        "prob_up": prob_up,
        "confidence": confidence,
        "market_regime": regime,
        "market_regime_status": "point_in_time",
        "market_regime_confidence": 0.8,
        "source_hash": HASH_A,
    }


def council(*, consensus: float = 0.55, disagreement: float = 0.1, uncertainty: float = 0.1, quality: float = 0.8, regime: str = "trend_up", regime_confidence: float = 0.9, regime_uncertainty: float = 0.1) -> dict:
    debate = {
        "BULL": {"stance": "BULL", "score": 0.8, "evidence_ids": ["e1"], "reasoning_summary": "structured"},
        "BEAR": {"stance": "BEAR", "score": 0.2, "evidence_ids": ["e1"], "reasoning_summary": "structured"},
        "NEUTRAL": {"stance": "NEUTRAL", "score": 0.2, "evidence_ids": ["e1"], "reasoning_summary": "structured"},
    }
    return {
        "snapshot_id": "council-snapshot-1",
        "status": "SUCCESS",
        "reason": None,
        "symbol": "BTCUSDT",
        "decision_time_utc": DECISION,
        "created_at_utc": DECISION,
        "available_at_utc": DECISION,
        "valid_until_utc": DECISION + timedelta(minutes=10),
        "ttl_seconds": 600,
        "market_context": {
            "trend_strength": 0.6,
            "momentum_score": 0.5,
            "volatility_state": "normal",
            "support_pressure": 0.7,
            "resistance_pressure": 0.3,
            "uncertainty": 0.1,
        },
        "microstructure_context": None,
        "news_context": None,
        "macro_context": None,
        "regime_context": {
            "regime_label": regime,
            "regime_confidence": regime_confidence,
            "trend_score": 0.8 if "trend" in regime else 0.1,
            "range_score": 0.9 if "range" in regime else 0.1,
            "volatility_score": 0.3,
            "uncertainty": regime_uncertainty,
        },
        "bull_case": debate["BULL"],
        "bear_case": debate["BEAR"],
        "neutral_case": debate["NEUTRAL"],
        "consensus_score": consensus,
        "disagreement_score": disagreement,
        "uncertainty_score": uncertainty,
        "context_quality": quality,
        "provider_provenance": [],
        "source_provenance": [],
        "evidence_ids": ["e1"],
        "agent_statuses": {},
    }


def market(*, flow: float = 0.5, coverage: float = 0.4) -> dict:
    statuses = {
        "flow": {
            "family": "flow",
            "status": "FRESH",
            "latest_event_time_utc": DECISION - timedelta(seconds=1),
            "latest_available_at_utc": DECISION - timedelta(milliseconds=500),
            "age_seconds": 0.5,
            "max_age_seconds": 15.0,
            "event_count": 3,
            "reason": "fresh",
        },
        "spread": {
            "family": "spread",
            "status": "MISSING",
            "max_age_seconds": 15.0,
            "event_count": 0,
            "reason": "missing",
        },
        "basis_funding": {
            "family": "basis_funding",
            "status": "SOURCE_UNAVAILABLE",
            "max_age_seconds": 300.0,
            "event_count": 0,
            "reason": "unavailable",
        },
        "open_interest": {
            "family": "open_interest",
            "status": "SOURCE_UNAVAILABLE",
            "max_age_seconds": 300.0,
            "event_count": 0,
            "reason": "unavailable",
        },
        "liquidations": {
            "family": "liquidations",
            "status": "SOURCE_UNAVAILABLE",
            "max_age_seconds": 60.0,
            "event_count": 0,
            "reason": "unavailable",
        },
    }
    return {
        "snapshot_id": "market-snapshot-1",
        "status": "PARTIAL",
        "reason": "partial_sources",
        "exchange": "binance_usdm_public",
        "symbol": "BTCUSDT",
        "decision_time_utc": DECISION,
        "created_at_utc": DECISION,
        "source_watermarks": [],
        "flow_features": {"flow_imbalance_15s": flow},
        "spread_features": {},
        "basis_funding_features": {},
        "open_interest_features": {},
        "liquidation_features": {},
        "research_council_context": None,
        "feature_family_statuses": statuses,
        "feature_manifest": [],
        "coverage": coverage,
        "available_feature_families": ["flow"],
        "missing_feature_families": ["spread", "basis_funding", "open_interest", "liquidations"],
        "point_in_time_valid": True,
    }


def request(**overrides: object) -> EnsembleAbstentionRequest:
    payload: dict[str, object] = {
        "request_id": "ensemble-request-1",
        "symbol": "BTCUSDT",
        "decision_time_utc": DECISION,
        "qlib": qlib(),
        "research_council_snapshot": council(),
        "market_intelligence_snapshot": market(),
    }
    payload.update(overrides)
    return EnsembleAbstentionRequest.model_validate(payload)


def config() -> AibotParityResearchConfig:
    return AibotParityResearchConfig()


def test_aligned_low_disagreement_proceeds_research() -> None:
    result = evaluate_ensemble_abstention(request(), config().ensemble_abstention)
    assert result.status is EnsembleStatus.SUCCESS
    assert result.research_action is ResearchAction.PROCEED_RESEARCH
    assert result.regime_route.regime_label is RegimeLabel.TREND_UP
    assert result.regime_alignment is RegimeAlignment.ALIGNED
    assert result.operational_authority is False
    assert result.sends_orders is False
    assert {point.source for point in result.directional_evidence} == {
        "qlib",
        "research_council",
        "market_intelligence",
    }


def test_high_disagreement_abstains() -> None:
    req = request(research_council_snapshot=council(consensus=-0.7, disagreement=0.8))
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.research_action is ResearchAction.ABSTAIN
    assert "ensemble_high_disagreement" in result.reasons


def test_counter_trend_high_confidence_abstains() -> None:
    req = request(qlib=qlib(side="long", regime="trend_down", score=0.6), research_council_snapshot=council(regime="trend_down"))
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.regime_alignment is RegimeAlignment.COUNTER_TREND
    assert result.research_action is ResearchAction.ABSTAIN
    assert "counter_trend_high_confidence_regime" in result.reasons


def test_range_high_confidence_abstains() -> None:
    req = request(qlib=qlib(regime="range"), research_council_snapshot=council(regime="range", regime_confidence=0.95))
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.regime_alignment is RegimeAlignment.RANGE
    assert result.research_action is ResearchAction.ABSTAIN
    assert "range_high_confidence_regime" in result.reasons


def test_ai_shadow_veto_abstains_without_becoming_directional_authority() -> None:
    req = request(ai_shadow={
        "evidence_id": "shadow-1",
        "source_id": "ai-shadow",
        "model_version": "shadow-v1",
        "symbol": "BTCUSDT",
        "generated_at_utc": DECISION - timedelta(seconds=2),
        "available_at_utc": DECISION - timedelta(seconds=1),
        "valid_until_utc": DECISION + timedelta(minutes=5),
        "decision": "VETO",
        "veto_score": 0.9,
        "confidence": 0.9,
        "source_hash": HASH_B,
    })
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.research_action is ResearchAction.ABSTAIN
    assert result.ai_shadow_decision is AIShadowDecision.VETO
    assert all(point.source != "ai_shadow" for point in result.directional_evidence)


def test_future_qlib_evidence_fail_closed() -> None:
    req = request(qlib=qlib(available_at=DECISION + timedelta(seconds=1)))
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.status is EnsembleStatus.BLOCKED
    assert result.research_action is ResearchAction.ABSTAIN
    assert result.regime_route.point_in_time_valid is False
    assert result.directional_evidence_count == 0
    assert result.ensemble_score == 0.0


def test_deterministic_decision_id() -> None:
    first = evaluate_ensemble_abstention(request(), config().ensemble_abstention)
    second = evaluate_ensemble_abstention(request(), config().ensemble_abstention)
    assert first.decision_id == second.decision_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_missing_regime_deprioritizes_when_other_evidence_is_sufficient() -> None:
    req = request(qlib=qlib(regime="unknown"), research_council_snapshot=None)
    result = evaluate_ensemble_abstention(req, config().ensemble_abstention)
    assert result.regime_route.regime_label is RegimeLabel.UNKNOWN
    assert result.research_action in {ResearchAction.DEPRIORITIZE_RESEARCH, ResearchAction.ABSTAIN}
    assert result.sends_orders is False


def test_config_is_fail_closed_and_loadable_from_repo() -> None:
    root = Path(__file__).resolve().parents[1]
    loaded = load_aibot_parity_config(root, "config/research/aibot_parity.yaml")
    assert loaded.paper_only is True
    assert loaded.shadow_only is True
    assert loaded.research_only is True
    assert loaded.operational_authority is False
    assert loaded.sends_orders is False
    assert loaded.exchange_private_access is False
    assert loaded.writes_active_signals is False


def test_service_no_write_default() -> None:
    root = Path(__file__).resolve().parents[1]
    report = run_ensemble_abstention(
        project_root=root,
        config=config(),
        request_payload=request(),
        write_report=False,
    )
    assert report.status is EnsembleStatus.SUCCESS
    assert report.write_requested is False
    assert report.write_performed is False
    assert report.model_training_performed is False
    assert report.model_promotion_performed is False
    assert report.registry_write_performed is False


def test_static_no_order_or_risk_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        *sorted((root / "smartcrypto" / "research" / "ensemble_abstention").glob("*.py")),
        root / "scripts" / "run_ensemble_abstention_shadow_v1.py",
    ]
    forbidden_import_prefixes = (
        "smartcrypto.execution",
        "smartcrypto.risk",
        "freqtrade",
        "ccxt",
    )
    forbidden_calls = {
        "create_order",
        "cancel_order",
        "submit_order",
        "apply_risk_manager_gate",
        "write_active_signals",
        "promote_model",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden_import_prefixes) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden_import_prefixes)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in forbidden_calls
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in forbidden_calls


def test_qlib_score_probability_consistency_is_enforced() -> None:
    payload = qlib(score=0.6)
    payload["prob_up"] = 0.51
    with pytest.raises(ValueError, match="qlib_score_prob_up_inconsistent"):
        QlibDirectionalEvidence.model_validate(payload)


def test_context_contracts_accept_w2_w3_snapshots() -> None:
    assert ContextIntelligenceSnapshot.model_validate(council()).snapshot_id == "council-snapshot-1"
    snapshot = MarketIntelligenceSnapshot.model_validate(market())
    assert snapshot.feature_family_statuses["flow"] == FeatureFamilyHealth.model_validate(
        market()["feature_family_statuses"]["flow"]
    )
    assert snapshot.feature_family_statuses["flow"].status is FreshnessStatus.FRESH


def test_research_write_is_restricted_and_idempotent(tmp_path: Path) -> None:
    first = run_ensemble_abstention(
        project_root=tmp_path,
        config=config(),
        request_payload=request(),
        write_report=True,
    )
    second = run_ensemble_abstention(
        project_root=tmp_path,
        config=config(),
        request_payload=request(),
        write_report=True,
    )

    assert first.status is EnsembleStatus.SUCCESS
    assert first.write_requested is True
    assert first.write_performed is True
    assert second.status is EnsembleStatus.SUCCESS
    assert second.write_requested is True
    assert second.write_performed is False
    assert first.output_paths == second.output_paths
    assert first.output_paths["decision"].startswith(
        "data/research/aibot_parity/ensemble_abstention/"
    )
    assert first.output_paths["audit"].startswith(
        "data/reports/aibot_parity/ensemble_abstention/"
    )


def test_research_write_outside_authorized_root_fails_closed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "w4-forbidden-output.json"
    if outside.exists():
        outside.unlink()

    report = run_ensemble_abstention(
        project_root=tmp_path,
        config=config(),
        request_payload=request(),
        write_report=True,
        output_json=outside,
    )

    assert report.status is EnsembleStatus.BLOCKED
    assert report.write_requested is True
    assert report.write_performed is False
    assert report.reason is not None
    assert report.reason.startswith("persistence_failed:")
    assert outside.exists() is False
