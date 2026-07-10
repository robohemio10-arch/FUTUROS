"""Read-only synthesis of paper autotrain readiness gates."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartcrypto.learning.ai_qlib_drift_regime_monitor import build_ai_qlib_drift_regime_monitor_v1
from smartcrypto.learning.event_driven_backtest_execution_cost_gate import (
    build_event_driven_backtest_execution_cost_gate_v1,
)
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_microbatch_freshness_and_watermark import (
    build_paper_autotrain_microbatch_freshness_and_watermark_v1,
)
from smartcrypto.learning.paper_autotrain_microbatch_sync_planner import (
    build_paper_autotrain_microbatch_sync_planner_v1,
)
from smartcrypto.learning.paper_model_candidate_registry_gate import (
    build_paper_model_candidate_registry_gate_v1,
)
from smartcrypto.learning.qlib_trainer import build_qlib_institutional_ranking_trainer_report
from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report


def evaluate_autotrain_readiness(
    *,
    project_root: str | Path,
    continuity_report: Mapping[str, Any],
    report_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate existing gates without writes, training, promotion or registry mutation."""

    root = Path(project_root).resolve()
    overrides = dict(report_overrides or {})

    def supplied_or_build(key: str, builder: Any, **kwargs: Any) -> dict[str, Any]:
        supplied = overrides.get(key)
        return dict(supplied) if supplied is not None else dict(builder(project_root=root, **kwargs))

    watermark = supplied_or_build(
        "watermark",
        build_paper_autotrain_incremental_watermark_fix_v1,
    )
    freshness = supplied_or_build(
        "freshness",
        build_paper_autotrain_microbatch_freshness_and_watermark_v1,
    )
    microbatch = supplied_or_build(
        "microbatch",
        build_paper_autotrain_microbatch_sync_planner_v1,
        allow_paper_db_read=False,
    )
    qlib = supplied_or_build(
        "qlib",
        build_qlib_institutional_ranking_trainer_report,
        train=False,
        write_report=False,
        write_challenger_artifact=False,
        registry_write_requested=False,
        model_promotion_requested=False,
    )
    walkforward = supplied_or_build("walkforward", build_walkforward_anti_leakage_report, write=False)
    execution_cost = supplied_or_build(
        "execution_cost",
        build_event_driven_backtest_execution_cost_gate_v1,
        write_report=False,
    )
    drift = supplied_or_build("drift", build_ai_qlib_drift_regime_monitor_v1, write_report=False)
    registry = supplied_or_build(
        "registry",
        build_paper_model_candidate_registry_gate_v1,
        write=False,
    )

    statuses = {
        "continuity_status": "ok"
        if int(continuity_report.get("missing_in_feedback_count", 1) or 0) == 0
        and int(continuity_report.get("conflicting_group_count", 1) or 0) == 0
        else "blocked",
        "watermark_status": str(watermark.get("watermark_status") or watermark.get("status") or "blocked"),
        "microbatch_readiness_status": str(microbatch.get("status") or "blocked"),
        "qlib_backend_status": str(qlib.get("qlib_backend_status") or "unavailable"),
        "walkforward_gate_status": str(walkforward.get("status") or "blocked"),
        "execution_cost_gate_status": str(execution_cost.get("status") or "blocked"),
        "drift_gate_status": str(drift.get("status") or "blocked"),
        "registry_gate_status": str(registry.get("registry_gate_status") or registry.get("status") or "blocked"),
    }
    leakage_blocked = (
        str(walkforward.get("leakage_status") or "blocked") != "ok"
        or int(walkforward.get("future_columns_in_features_count", 0) or 0) > 0
        or int(walkforward.get("target_columns_in_features_count", 0) or 0) > 0
        or int(walkforward.get("outcome_columns_in_features_count", 0) or 0) > 0
    )
    required_ok = (
        statuses["continuity_status"] == "ok"
        and statuses["watermark_status"] == "ok"
        and statuses["microbatch_readiness_status"] == "ok"
        and statuses["qlib_backend_status"] == "available"
        and statuses["walkforward_gate_status"] == "ok"
        and statuses["execution_cost_gate_status"] == "ok"
        and statuses["drift_gate_status"] == "ok"
        and statuses["registry_gate_status"] == "ok"
        and str(freshness.get("status") or "blocked") == "ok"
        and not leakage_blocked
    )
    blockers = [key for key, value in statuses.items() if value not in {"ok", "available"}]
    if leakage_blocked:
        blockers.append("critical_leakage_blocker")

    return {
        **statuses,
        "freshness_status": str(freshness.get("status") or "blocked"),
        "leakage_blocked": leakage_blocked,
        "automatic_promotion_enabled": False,
        "promotion_allowed": False,
        "training_allowed": False,
        "creates_microbatch": False,
        "runs_training": False,
        "promotes_model": False,
        "blockers": sorted(set(blockers)),
        "final_readiness_decision": "READY_FOR_PAPER_OBSERVATION" if required_ok else "MANTER_EM_RESEARCH",
    }
