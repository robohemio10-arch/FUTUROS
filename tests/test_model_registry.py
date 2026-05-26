from __future__ import annotations

import pytest

from smartcrypto.ml.model_registry import ModelRegistry, ModelRegistryError


def test_model_registry_registers_candidate(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")

    record = registry.register(
        model_id="qlib-lgbm",
        model_version="v1",
        status="CANDIDATE",
        risk_status="PENDING",
    )

    assert record.status == "CANDIDATE"
    assert registry.list_models()[0].model_id == "qlib-lgbm"


def test_model_registry_approves_only_with_feature_contract_version(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")

    with pytest.raises(ModelRegistryError, match="feature_contract_version_required"):
        registry.register(
            model_id="qlib-lgbm",
            model_version="v1",
            status="APPROVED_FOR_SHADOW",
            risk_status="PASSED",
        )

    approved = registry.register(
        model_id="qlib-lgbm",
        model_version="v2",
        status="APPROVED_FOR_SHADOW",
        feature_contract_version="features-v1",
        risk_status="PASSED",
    )
    assert approved.status == "APPROVED_FOR_SHADOW"


def test_model_registry_blocks_invalid_risk_status_for_approval(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")

    with pytest.raises(ModelRegistryError, match="risk_status_invalid"):
        registry.register(
            model_id="qlib-lgbm",
            model_version="v1",
            status="APPROVED_FOR_SHADOW",
            feature_contract_version="features-v1",
            risk_status="FAILED",
        )


def test_model_registry_blocks_live_promotion(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")

    with pytest.raises(ModelRegistryError, match="live_model_promotion_forbidden"):
        registry.register(model_id="qlib-lgbm", model_version="v1", status="LIVE")


def test_model_registry_allows_metadata_only_rollback(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")
    registry.register(model_id="qlib-lgbm", model_version="v1", status="CANDIDATE")

    rolled_back = registry.rollback("qlib-lgbm", "v1", reason="bad_shadow_metrics")

    assert rolled_back.status == "ROLLED_BACK"
    assert rolled_back.metadata["status_reason"] == "bad_shadow_metrics"
