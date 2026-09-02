from datetime import datetime, timezone

import pytest

from smartcrypto.research.portfolio_intelligence import AlphaDefinition, build_alpha_registry

T = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)
H1 = "c" * 64
H2 = "d" * 64


def _alpha(strategy_id: str, feature_hash: str) -> AlphaDefinition:
    return AlphaDefinition(
        strategy_id=strategy_id,
        sleeve="directional",
        version="v1",
        feature_set_hash=feature_hash,
        hypothesis="Directional edge conditioned on causal regime evidence.",
        supported_regimes=("TREND",),
    )


def test_registry_is_deterministic_independent_of_input_order() -> None:
    a = _alpha("trend-v1", H1)
    b = _alpha("breakout-v1", H2)
    one = build_alpha_registry([a, b], created_at_utc=T)
    two = build_alpha_registry([b, a], created_at_utc=T)
    assert one.registry_id == two.registry_id
    assert [item.strategy_id for item in one.definitions] == ["breakout-v1", "trend-v1"]
    assert one.operational_authority is False


def test_duplicate_strategy_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate_strategy_id"):
        build_alpha_registry(
            [_alpha("trend-v1", H1), _alpha("trend-v1", H2)],
            created_at_utc=T,
        )
