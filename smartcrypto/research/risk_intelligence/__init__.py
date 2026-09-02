"""W9 Risk Intelligence + Treasury research-only package."""

from __future__ import annotations

from .contracts import (
    CircuitConfig,
    CircuitDecision,
    CurrentRiskObservation,
    DailyBudgetConfig,
    DailyBudgetDecision,
    HistoricalReturnObservation,
    ProtectionState,
    RiskIntelligenceRequest,
    RiskIntelligenceSnapshot,
    SafetyContract,
    SnapshotStatus,
    StressConfig,
    StressReport,
    StressScenarioMetrics,
    TreasuryScenario,
    TreasurySimulation,
    canonical_sha256,
)
from .daily_budget import evaluate_daily_budget
from .stress import build_stress_report
from .treasury_reserve import simulate_treasury_reserve


def build_snapshot(request: RiskIntelligenceRequest) -> RiskIntelligenceSnapshot:
    stress_report = build_stress_report(request)
    if stress_report.status == SnapshotStatus.BLOCKED:
        semantic = {
            "request_id": request.request_id,
            "decision_time_utc": request.decision_time_utc,
            "status": SnapshotStatus.BLOCKED,
            "stress_report": stress_report,
        }
        return RiskIntelligenceSnapshot(
            snapshot_id=f"risk-intelligence-{canonical_sha256(semantic)}",
            request_id=request.request_id,
            decision_time_utc=request.decision_time_utc,
            created_at_utc=request.decision_time_utc,
            status=SnapshotStatus.BLOCKED,
            reason=stress_report.reason,
            stress_report=stress_report,
            circuit_decision=None,
            daily_budget=None,
            treasury=None,
        )

    daily_budget, circuit = evaluate_daily_budget(request, stress_report)
    treasury = (
        simulate_treasury_reserve(request.treasury)
        if request.treasury is not None
        else None
    )
    semantic = {
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "stress_report": stress_report,
        "circuit_decision": circuit,
        "daily_budget": daily_budget,
        "treasury": treasury,
    }
    return RiskIntelligenceSnapshot(
        snapshot_id=f"risk-intelligence-{canonical_sha256(semantic)}",
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=SnapshotStatus.READY,
        reason="risk_budget_treasury_shadow_simulation_ready",
        stress_report=stress_report,
        circuit_decision=circuit,
        daily_budget=daily_budget,
        treasury=treasury,
    )


__all__ = [
    "CircuitConfig",
    "CircuitDecision",
    "CurrentRiskObservation",
    "DailyBudgetConfig",
    "DailyBudgetDecision",
    "HistoricalReturnObservation",
    "ProtectionState",
    "RiskIntelligenceRequest",
    "RiskIntelligenceSnapshot",
    "SafetyContract",
    "SnapshotStatus",
    "StressConfig",
    "StressReport",
    "StressScenarioMetrics",
    "TreasuryScenario",
    "TreasurySimulation",
    "build_snapshot",
    "build_stress_report",
    "canonical_sha256",
    "evaluate_daily_budget",
    "simulate_treasury_reserve",
]
