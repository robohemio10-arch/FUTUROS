from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.research.aibot_parity_orchestrator import (
    AibotParityPipelinePersistenceError,
    AibotParityPipelineRequest,
    PipelineStatus,
    build_aibot_parity_pipeline,
    persist_pipeline_snapshot,
)


UTC = timezone.utc
DECISION_TIME = datetime(2026, 9, 3, 16, 30, tzinfo=UTC)


def _base_sources() -> dict[str, dict[str, object]]:
    stamp = DECISION_TIME.isoformat().replace("+00:00", "Z")
    return {
        "research_council": {
            "status": "SUCCESS",
            "snapshot": {
                "status": "SUCCESS",
                "decision_time_utc": stamp,
                "available_at_utc": stamp,
            },
        },
        "market_intelligence": {
            "status": "SUCCESS",
            "snapshot": {
                "status": "SUCCESS",
                "decision_time_utc": stamp,
                "available_at_utc": stamp,
            },
        },
        "ensemble_abstention": {
            "status": "READY",
            "decision": {
                "status": "READY",
                "decision_time_utc": stamp,
                "research_action": "PROCEED_RESEARCH",
            },
        },
        "opportunity_book": {
            "status": "READY",
            "decision_time_utc": stamp,
            "book_id": "book-1",
        },
        "portfolio_allocator": {
            "status": "READY",
            "decision_time_utc": stamp,
            "selected": [{"candidate_id": "candidate-1"}],
            "selected_count": 1,
        },
    }


def _request(
    sources: dict[str, dict[str, object]] | None = None,
) -> AibotParityPipelineRequest:
    return AibotParityPipelineRequest(
        request_id="cycle-request-1",
        decision_time_utc=DECISION_TIME,
        sources=sources or _base_sources(),
    )


def test_pipeline_is_deterministic_and_qlib_blocks_only_itself() -> None:
    first = build_aibot_parity_pipeline(_request())
    second = build_aibot_parity_pipeline(_request())

    assert first == second
    assert first.cycle_id == second.cycle_id
    assert first.qlib_status == "BLOCKED_EXTERNAL"
    assert first.qlib_blocked_external is True
    assert first.status is PipelineStatus.ABSTAIN
    assert first.reason == "riskmanager_shadow_allow_not_proven"
    assert first.blocking_reasons == ()
    assert first.writes_active_signals is False
    assert first.signal_published is False
    assert first.operational_authority is False


def test_missing_required_source_fails_closed() -> None:
    sources = _base_sources()
    sources.pop("market_intelligence")

    snapshot = build_aibot_parity_pipeline(_request(sources))

    assert snapshot.status is PipelineStatus.BLOCKED
    assert snapshot.final_action == "ABSTAIN"
    assert snapshot.would_signal is False
    assert "market_intelligence" in snapshot.missing_required_sources


def test_future_point_in_time_source_fails_closed() -> None:
    sources = _base_sources()
    future = DECISION_TIME + timedelta(seconds=1)
    sources["market_intelligence"]["snapshot"]["available_at_utc"] = (
        future.isoformat().replace("+00:00", "Z")
    )

    snapshot = build_aibot_parity_pipeline(_request(sources))

    assert snapshot.status is PipelineStatus.BLOCKED
    assert any(
        reason.startswith("required_source_point_in_time_invalid:market_intelligence")
        for reason in snapshot.blocking_reasons
    )


def test_ensemble_abstain_remains_first_class() -> None:
    sources = _base_sources()
    sources["ensemble_abstention"]["decision"]["research_action"] = "ABSTAIN"

    snapshot = build_aibot_parity_pipeline(_request(sources))

    assert snapshot.status is PipelineStatus.ABSTAIN
    assert snapshot.final_action == "ABSTAIN"
    assert snapshot.reason == "ensemble_abstain"


def test_counterfactual_would_signal_requires_explicit_shadow_riskmanager_allow() -> None:
    sources = _base_sources()
    stamp = DECISION_TIME.isoformat().replace("+00:00", "Z")
    sources["risk_budget"] = {
        "status": "SUCCESS",
        "decision_time_utc": stamp,
        "daily_budget": {"new_risk_allowed": True},
    }
    sources["riskmanager_shadow"] = {
        "status": "SUCCESS",
        "decision_time_utc": stamp,
        "decision": "ALLOW",
    }

    snapshot = build_aibot_parity_pipeline(_request(sources))

    assert snapshot.status is PipelineStatus.READY_SHADOW
    assert snapshot.final_action == "WOULD_SIGNAL"
    assert snapshot.would_signal is True
    assert snapshot.signal_published is False
    assert snapshot.writes_active_signals is False
    assert snapshot.riskmanager_final_authority is True


def test_risk_budget_false_forces_abstain() -> None:
    sources = _base_sources()
    stamp = DECISION_TIME.isoformat().replace("+00:00", "Z")
    sources["risk_budget"] = {
        "status": "SUCCESS",
        "decision_time_utc": stamp,
        "daily_budget": {"new_risk_allowed": False},
    }
    sources["riskmanager_shadow"] = {
        "status": "SUCCESS",
        "decision_time_utc": stamp,
        "decision": "ALLOW",
    }

    snapshot = build_aibot_parity_pipeline(_request(sources))

    assert snapshot.status is PipelineStatus.ABSTAIN
    assert snapshot.reason == "risk_budget_disallows_new_risk"
    assert snapshot.would_signal is False


def test_persistence_is_path_restricted_and_idempotent(tmp_path: Path) -> None:
    snapshot = build_aibot_parity_pipeline(_request())
    first = persist_pipeline_snapshot(project_root=tmp_path, snapshot=snapshot)
    second = persist_pipeline_snapshot(project_root=tmp_path, snapshot=snapshot)

    assert first["write_performed"] is True
    assert first["lock_serialized"] is True
    assert second["write_performed"] is False
    target = tmp_path / str(first["output_path"])
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["cycle_id"] == snapshot.cycle_id

    with pytest.raises(AibotParityPipelinePersistenceError) as exc:
        persist_pipeline_snapshot(
            project_root=tmp_path,
            snapshot=snapshot,
            output_json="data/runtime/forbidden.json",
        )
    assert exc.value.reason == "target_outside_authorized_roots"


def test_concurrent_persistence_produces_one_valid_snapshot(tmp_path: Path) -> None:
    snapshot = build_aibot_parity_pipeline(_request())

    def persist() -> dict[str, object]:
        return persist_pipeline_snapshot(project_root=tmp_path, snapshot=snapshot)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: persist(), range(16)))

    target = tmp_path / "data/reports/aibot_parity/aibot_parity_e2e_snapshot_v1.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["cycle_id"] == snapshot.cycle_id
    assert all(result["lock_serialized"] is True for result in results)
