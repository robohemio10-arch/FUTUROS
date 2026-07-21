from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    ACTIVATION_STATE,
    FIELD_SOURCE_REGISTRY,
    PROFILE_VERSION,
    REQUIRED_TARGET_FIELDS,
    RuntimeDecisionInputV1,
    RuntimeDecisionProjectionV1,
    RuntimeTradeLinkProjectionV1,
    RuntimeTradeObservationInputV1,
    build_runtime_profile_schema,
    map_runtime_decision,
    map_runtime_trade_link,
    normalize_symbol,
    registry_payload,
    registry_sha256,
    validate_registry,
)


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "decision_ledger_runtime_profile_v1"
)


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def build_decision() -> RuntimeDecisionProjectionV1:
    return map_runtime_decision(
        load_fixture("valid_runtime_decision_input.json")
    )


def test_registry_covers_exact_40_fields() -> None:
    validate_registry()
    assert len(FIELD_SOURCE_REGISTRY) == 40
    assert tuple(item.field for item in FIELD_SOURCE_REGISTRY) == REQUIRED_TARGET_FIELDS


def test_registry_hash_is_deterministic() -> None:
    assert registry_sha256() == registry_sha256()
    assert len(registry_sha256()) == 64


def test_registry_payload_is_machine_readable() -> None:
    payload = registry_payload()
    assert payload["required_target_field_count"] == 40
    assert len(payload["fields"]) == 40


def test_decision_mapping_is_deterministic() -> None:
    first = build_decision()
    second = build_decision()
    assert first == second
    assert first.target_payload.payload_sha256 == second.target_payload.payload_sha256


def test_decision_projection_remains_design_only() -> None:
    projection = build_decision()
    assert projection.profile_version == PROFILE_VERSION
    assert projection.activation_state == ACTIVATION_STATE
    assert projection.lineage.writer_invoked is False
    assert projection.lineage.runtime_integration is False
    assert projection.target_payload.runtime_integration is False
    assert projection.target_payload.operational_authority is False
    assert projection.target_payload.sends_orders is False
    assert projection.target_payload.exchange_private_access is False


def test_risk_approved_maps_to_approved() -> None:
    projection = build_decision()
    assert projection.target_payload.risk_decision.value == "APPROVED"
    assert projection.target_payload.final_decision.value == "ALLOW"


def test_risk_rejected_maps_to_block() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload.update(
        {
            "risk_approved": False,
            "risk_reasons": ["max_positions_reached"],
            "approved_stake_usdt": 0.0,
            "approved_leverage": 0.0,
            "final_decision": "BLOCK",
            "final_reasons": ["risk_manager_rejected"],
        }
    )
    projection = map_runtime_decision(payload)
    assert projection.target_payload.risk_decision.value == "REJECTED"
    assert projection.target_payload.final_decision.value == "BLOCK"


def test_risk_rejected_cannot_allow() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload.update(
        {
            "risk_approved": False,
            "risk_reasons": ["risk_rejected"],
            "approved_stake_usdt": 0.0,
            "approved_leverage": 0.0,
        }
    )
    with pytest.raises(ValidationError, match="risk_rejected_requires_final_block"):
        RuntimeDecisionInputV1.model_validate(payload)


def test_ai_shadow_block_cannot_final_allow() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload["ai_shadow_decision"] = "BLOCK"
    payload["ai_shadow_reasons"] = ["shadow_block"]
    with pytest.raises(ValidationError, match="allow_conflicts_with_ai_shadow_block"):
        RuntimeDecisionInputV1.model_validate(payload)


def test_symbol_must_match_pair() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError, match="symbol_pair_mismatch"):
        map_runtime_decision(payload)


def test_symbol_normalization() -> None:
    assert normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT") == "ETHUSDT"


def test_unknown_input_field_rejected() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        RuntimeDecisionInputV1.model_validate(payload)


def test_timestamp_order_is_fail_closed() -> None:
    payload = load_fixture("valid_runtime_decision_input.json")
    payload["decision_timestamp"] = "2026-07-20T17:59:59.000000Z"
    with pytest.raises(ValidationError, match="decision_timestamp_before_risk_check"):
        RuntimeDecisionInputV1.model_validate(payload)


def test_trade_link_mapping_is_deterministic() -> None:
    decision = build_decision()
    observation = load_fixture("valid_runtime_trade_observation.json")
    first = map_runtime_trade_link(decision, observation)
    second = map_runtime_trade_link(decision, observation)
    assert first == second
    assert first.target_payload.parent_event_id == decision.target_payload.event_id
    assert first.target_payload.decision_payload_sha256 == (
        decision.target_payload.payload_sha256
    )


def test_trade_link_projection_remains_design_only() -> None:
    projection = map_runtime_trade_link(
        build_decision(),
        load_fixture("valid_runtime_trade_observation.json"),
    )
    assert isinstance(projection, RuntimeTradeLinkProjectionV1)
    assert projection.lineage.writer_invoked is False
    assert projection.target_payload.runtime_integration is False
    assert projection.target_payload.sends_orders is False


def test_trade_link_pair_mismatch_rejected() -> None:
    observation = load_fixture("valid_runtime_trade_observation.json")
    observation["observed_pair"] = "ETH/USDT:USDT"
    with pytest.raises(ValueError, match="trade_pair_mismatch"):
        map_runtime_trade_link(build_decision(), observation)


def test_trade_id_must_be_positive() -> None:
    observation = load_fixture("valid_runtime_trade_observation.json")
    observation["trade_id"] = 0
    with pytest.raises(ValidationError):
        RuntimeTradeObservationInputV1.model_validate(observation)


def test_schema_is_draft_2020_12_and_blocked() -> None:
    schema = build_runtime_profile_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-runtime-integration-allowed"] is False
    assert schema["x-activation-state"] == "sandbox_mapping_only"


def test_mapping_module_has_no_writer_network_or_sqlite_imports() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "execution"
        / "decision_ledger_runtime_profile_v1"
        / "mapping.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".", 1)[0])
    assert not (imports & {"ccxt", "requests", "httpx", "aiohttp", "redis", "sqlite3"})
    assert "writer" not in module_path.read_text(encoding="utf-8").casefold()


def test_projection_round_trip_validation() -> None:
    decision = build_decision()
    validated = RuntimeDecisionProjectionV1.model_validate(
        decision.model_dump(mode="python")
    )
    assert validated == decision


def test_trade_projection_round_trip_validation() -> None:
    trade = map_runtime_trade_link(
        build_decision(),
        load_fixture("valid_runtime_trade_observation.json"),
    )
    validated = RuntimeTradeLinkProjectionV1.model_validate(
        trade.model_dump(mode="python")
    )
    assert validated == trade


def test_decision_projection_matches_expected_fixture() -> None:
    expected = load_fixture("expected_decision_projection.json")
    assert build_decision().model_dump(mode="json") == expected


def test_trade_projection_matches_expected_fixture() -> None:
    expected = load_fixture("expected_trade_link_projection.json")
    actual = map_runtime_trade_link(
        build_decision(),
        load_fixture("valid_runtime_trade_observation.json"),
    )
    assert actual.model_dump(mode="json") == expected
