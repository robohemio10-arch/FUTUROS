from __future__ import annotations

import pandas as pd

from smartcrypto.learning.quality_gated_v5_contract.eligibility import (
    build_eligibility,
)
from smartcrypto.learning.quality_gated_v5_contract.nonregression import (
    compare_official_projection,
)
from smartcrypto.learning.quality_gated_v5_contract.reporting import (
    render_markdown,
)


def projection_frame(
    *,
    trade_ids: list[str],
    order_ids: list[str] | None = None,
    eligible: list[bool] | None = None,
    block_reasons: list[list[str]] | None = None,
) -> pd.DataFrame:
    row_count = len(trade_ids)
    payload: dict[str, object] = {
        "trade_id": trade_ids,
        "eligible_for_model_training": eligible or [False] * row_count,
        "block_reasons": block_reasons or [[] for _ in range(row_count)],
    }
    if order_ids is not None:
        payload["order_id"] = order_ids
    return pd.DataFrame(payload)


def test_zero_trade_id_overlap_does_not_prove_identity_loss() -> None:
    official = pd.DataFrame(
        {
            "trade_id": ["official-a", "official-b"],
            "order_id": ["order-1", "order-2"],
        }
    )
    projection = projection_frame(
        trade_ids=["universe-a", "universe-b"],
        order_ids=["order-1", "order-2"],
    )

    result = compare_official_projection(official, projection)

    assert result["status"] == "blocked"
    assert result["reason"] == "canonical_identity_unavailable_for_official_artifact"
    assert result["artifact_trade_id_namespace_compatible"] is False
    assert result["canonical_identity_computable"] is False
    assert result["canonical_nonregression_evaluable"] is False
    assert result["official_identity_loss_proven"] is False
    assert result["official_identity_retention_proven"] is False
    assert result["unexplained_removed_official_ids"] is None
    assert result["unexplained_removed_official_trade_ids"] is None


def test_partial_order_id_overlap_is_diagnostic_only() -> None:
    official = pd.DataFrame(
        {
            "trade_id": ["official-a", "official-b", "official-c"],
            "order_id": ["order-1", "order-2", "order-3"],
        }
    )
    projection = projection_frame(
        trade_ids=["universe-a", "universe-b", "universe-c"],
        order_ids=["order-1", "order-2", "order-x"],
    )

    result = compare_official_projection(official, projection)

    assert result["order_id_diagnostic_overlap_unique_keys"] == 2
    assert result["order_id_diagnostic_missing_official_keys"] == 1
    assert result["order_id_has_identity_authority"] is False
    assert result["canonical_nonregression_evaluable"] is False


def test_duplicate_order_id_is_reported_without_granting_authority() -> None:
    official = pd.DataFrame(
        {
            "trade_id": ["official-a", "official-b"],
            "order_id": ["order-1", "order-2"],
        }
    )
    projection = projection_frame(
        trade_ids=["universe-a", "universe-b", "universe-c"],
        order_ids=["order-1", "order-1", "order-2"],
    )

    result = compare_official_projection(official, projection)

    assert result["order_id_diagnostic_overlap_unique_keys"] == 2
    assert result["order_id_diagnostic_universe_duplicate_rows"] == 2
    assert result["order_id_has_identity_authority"] is False
    assert result["official_identity_retention_proven"] is False


def test_missing_canonical_identity_columns_returns_not_evaluable() -> None:
    official = pd.DataFrame({"order_id": ["order-1"]})
    projection = pd.DataFrame(
        {
            "order_id": ["order-1"],
            "eligible_for_model_training": [False],
            "block_reasons": [["BLOCKED_STALE_1M_SNAPSHOT"]],
        }
    )

    result = compare_official_projection(official, projection)

    assert result["status"] == "blocked"
    assert result["canonical_nonregression_evaluable"] is False
    assert "official_trade_id_column_missing" in result[
        "canonical_identity_unavailability_reasons"
    ]
    assert "universe_trade_id_column_missing" in result[
        "canonical_identity_unavailability_reasons"
    ]
    assert result["unexplained_removed_official_ids"] is None


def test_shared_unique_trade_id_namespace_preserves_set_based_gate() -> None:
    official = pd.DataFrame({"trade_id": ["a", "b"]})
    projection = projection_frame(
        trade_ids=["a", "b", "c"],
        eligible=[True, False, True],
        block_reasons=[[], ["BLOCKED_STALE_1M_SNAPSHOT"], []],
    )

    result = compare_official_projection(official, projection)

    assert result["artifact_trade_id_namespace_compatible"] is True
    assert result["canonical_identity_computable"] is True
    assert result["canonical_nonregression_evaluable"] is True
    assert result["status"] == "review_required"
    assert result["reason"] == "explained_quality_or_temporal_reduction"
    assert result["unexplained_removed_official_ids"] == 0
    assert result["official_identity_loss_proven"] is False


def test_shared_namespace_unexplained_removal_still_blocks() -> None:
    official = pd.DataFrame({"trade_id": ["a", "b"]})
    projection = projection_frame(
        trade_ids=["a", "b"],
        eligible=[True, False],
        block_reasons=[[], []],
    )

    result = compare_official_projection(official, projection)

    assert result["canonical_nonregression_evaluable"] is True
    assert result["status"] == "blocked"
    assert result["reason"] == "unexplained_official_identity_loss"
    assert result["official_identity_loss_proven"] is True
    assert result["unexplained_removed_official_trade_ids"] == ["b"]


def test_nonregression_audit_does_not_mutate_projection_block_reasons() -> None:
    official = pd.DataFrame({"trade_id": ["official-a"]})
    projection = projection_frame(
        trade_ids=["universe-a"],
        eligible=[False],
        block_reasons=[
            [
                "BLOCKED_IN_PROGRESS_5M_SNAPSHOT",
                "BLOCKED_MISSING_PRIOR_5M_FEATURES",
            ]
        ],
    )
    before = projection.copy(deep=True)

    compare_official_projection(official, projection)

    pd.testing.assert_frame_equal(projection, before)


def test_eligibility_carries_order_id_as_diagnostic_only() -> None:
    trades = pd.DataFrame(
        {
            "trade_id": ["trade-a"],
            "order_id": ["order-a"],
            "open_ts": [pd.Timestamp("2026-07-01T12:00:00Z")],
        }
    )
    provenance = pd.DataFrame({"provenance_block_reasons": [[]]})
    freshness = pd.DataFrame(
        {
            "snapshot_1m_block_reasons": [[]],
            "snapshot_5m_block_reasons": [[]],
        }
    )
    feature_quality = pd.DataFrame({"feature_block_reasons": [[]]})
    temporal = pd.DataFrame({"temporal_leakage_block_reasons": [[]]})

    result = build_eligibility(
        trades,
        provenance,
        freshness,
        feature_quality,
        temporal,
        feature_name_audit={"block_reasons": []},
    )

    assert result.loc[0, "trade_id"] == "trade-a"
    assert result.loc[0, "order_id"] == "order-a"
    assert bool(result.loc[0, "eligible_for_model_training"]) is True
    assert result.loc[0, "block_reasons"] == []


def test_markdown_distinguishes_not_evaluable_from_zero() -> None:
    markdown = render_markdown(
        {
            "status": "blocked",
            "reason": "nonregression_identity_gate_blocked",
            "decision": "MANTER_EM_RESEARCH",
            "nonregression": {
                "status": "blocked",
                "reason": "canonical_identity_unavailable_for_official_artifact",
                "canonical_nonregression_evaluable": False,
                "artifact_trade_id_namespace_compatible": False,
                "unexplained_removed_official_ids": None,
                "canonical_identity_unavailability_reasons": [
                    "artifact_trade_id_zero_overlap_namespace_unproven"
                ],
            },
            "safety_flags": {},
        }
    )

    assert "Unexplained removed official IDs: `None`" in markdown
    assert "gate was not evaluable" in markdown
    assert "artifact_trade_id_zero_overlap_namespace_unproven" in markdown
