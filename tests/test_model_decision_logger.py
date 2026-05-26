from __future__ import annotations

import json

from smartcrypto.ml.model_decision_logger import ModelDecisionLogger


def test_model_decision_logger_writes_jsonl_append_only(tmp_path) -> None:
    path = tmp_path / "decisions.jsonl"
    logger = ModelDecisionLogger(path)

    first = logger.record(
        model_id="qlib-lgbm",
        model_version="v1",
        feature_contract_version="features-v1",
        symbol="btcusdt",
        score=0.42,
        confidence=0.8,
        drift_status="OK",
        risk_action="ALLOW_SHADOW",
        payload={"note": "shadow only"},
    )
    second = logger.record(
        model_id="qlib-lgbm",
        model_version="v1",
        feature_contract_version="features-v1",
        symbol="ETHUSDT",
        score=-0.2,
        confidence=0.7,
        drift_status="WARNING",
        risk_action="REDUCE_CONFIDENCE",
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision_id"] == first.decision_id
    assert json.loads(lines[1])["decision_id"] == second.decision_id


def test_model_decision_logger_strips_secret_payload_fields(tmp_path) -> None:
    logger = ModelDecisionLogger(tmp_path / "decisions.jsonl")

    decision = logger.record(
        model_id="qlib-lgbm",
        model_version="v1",
        feature_contract_version="features-v1",
        symbol="BTCUSDT",
        score=0.1,
        confidence=0.5,
        drift_status="OK",
        risk_action="NO_ACTION",
        payload={"api_key": "never", "nested": {"secret_token": "never", "safe": 1}},
    )

    assert "api_key" not in decision.payload
    assert "secret_token" not in decision.payload["nested"]
    assert decision.payload["nested"]["safe"] == 1
