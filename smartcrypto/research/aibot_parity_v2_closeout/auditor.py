"""Deterministic, read-only software DoD audit for AIBOT Parity V2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aibot_parity_v2_final_dod_v1"

# Evidence is deliberately static and local. The auditor does not import or execute
# any subsystem, provider, model, exchange adapter, runtime service or builder.
WAVE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "W1": (
        "smartcrypto/research/aibot_parity/trader_master_benchmark.py",
        "smartcrypto/research/aibot_parity/metrics.py",
        "smartcrypto/research/aibot_parity/schemas.py",
    ),
    "W2": (
        "smartcrypto/research/research_council/contracts.py",
        "smartcrypto/research/research_council/engine.py",
        "smartcrypto/research/research_council/persistence.py",
    ),
    "W3": (
        "smartcrypto/research/market_intelligence/contracts.py",
        "smartcrypto/research/market_intelligence/engine.py",
        "smartcrypto/research/market_intelligence/snapshot_store.py",
        "smartcrypto/research/market_features_rematerialization_research_v2/engine.py",
    ),
    "W4": (
        "smartcrypto/research/aibot_parity/market_segmentation.py",
        "smartcrypto/research/ensemble_abstention/contracts.py",
        "smartcrypto/research/ensemble_abstention/ensemble.py",
        "smartcrypto/research/ensemble_abstention/regime_router.py",
    ),
    "W5": (
        "smartcrypto/research/portfolio_intelligence/opportunity_book.py",
        "smartcrypto/research/portfolio_intelligence/allocator.py",
        "smartcrypto/research/portfolio_intelligence/remaining_edge.py",
        "smartcrypto/research/portfolio_intelligence/replacement_policy.py",
    ),
    "W6": (
        "smartcrypto/research/portfolio_of_alphas/contracts.py",
        "smartcrypto/research/portfolio_of_alphas/portfolio.py",
        "smartcrypto/research/portfolio_of_alphas/fleet.py",
    ),
    "W7": (
        "smartcrypto/research/relative_value/contracts.py",
        "smartcrypto/research/relative_value/evaluator.py",
    ),
    "W8": (
        "smartcrypto/research/execution_intelligence/contracts.py",
        "smartcrypto/research/execution_intelligence/simulator.py",
        "smartcrypto/research/execution_intelligence/market_impact.py",
    ),
    "W9": (
        "smartcrypto/research/risk_intelligence/contracts.py",
        "smartcrypto/research/risk_intelligence/daily_budget.py",
        "smartcrypto/research/risk_intelligence/stress.py",
        "smartcrypto/research/risk_intelligence/treasury_reserve.py",
    ),
    "W12": (
        "smartcrypto/ops/dashboard_snapshots/aibot_parity_integration.py",
        "smartcrypto/ops/dashboard_snapshots/ai_governance_snapshot_builder.py",
        "smartcrypto/ops/dashboard_snapshots/opportunity_scanner_snapshot_builder.py",
        "smartcrypto/ops/dashboard_snapshots/quantitative_reports_snapshot_builder.py",
        "smartcrypto/ops/dashboard_snapshots/source_catalog.py",
        "smartcrypto/dashboard/components/read_only.py",
    ),
    "W13": (
        "smartcrypto/research/aibot_parity_orchestrator/contracts.py",
        "smartcrypto/research/aibot_parity_orchestrator/orchestrator.py",
        "smartcrypto/research/aibot_parity_orchestrator/persistence.py",
        "scripts/run_aibot_parity_local_pipeline_v1.py",
    ),
}

# W10 is intentionally not promoted from filesystem presence. The current security
# gate is external and fail-closed; a clean Qlib tree requires a separate decision.
W10_STATUS = "BLOCKED_EXTERNAL"
W11_STATUS = "CONDITIONAL_NOT_RUN"

ORCHESTRATOR_CONTRACT = "smartcrypto/research/aibot_parity_orchestrator/contracts.py"
REQUIRED_FALSE_MARKERS = (
    '"operational_authority": False',
    '"writes_active_signals": False',
    '"signal_published": False',
    '"sends_orders": False',
    '"exchange_private_access": False',
    '"changes_risk": False',
    '"changes_model": False',
    '"live_release_allowed": False',
    '"canary_release_allowed": False',
)
REQUIRED_TRUE_MARKERS = (
    '"paper_only": True',
    '"shadow_only": True',
    '"research_only": True',
)
FORBIDDEN_AUTHORITY_MARKERS = (
    '"operational_authority": True',
    '"writes_active_signals": True',
    '"signal_published": True',
    '"sends_orders": True',
    '"exchange_private_access": True',
    '"changes_risk": True',
    '"changes_model": True',
    '"live_release_allowed": True',
    '"canary_release_allowed": True',
)


def _stable_sha256(payload: Any) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _wave_result(root: Path, wave: str, required: tuple[str, ...]) -> dict[str, Any]:
    present = tuple(path for path in required if (root / path).is_file())
    missing = tuple(path for path in required if not (root / path).is_file())
    return {
        "wave": wave,
        "status": "PASS" if not missing else "BLOCKED",
        "required_evidence": required,
        "present_evidence": present,
        "missing_evidence": missing,
    }


def _safety_result(root: Path) -> dict[str, Any]:
    path = root / ORCHESTRATOR_CONTRACT
    text = _read_text(path)
    missing_false = tuple(marker for marker in REQUIRED_FALSE_MARKERS if marker not in text)
    missing_true = tuple(marker for marker in REQUIRED_TRUE_MARKERS if marker not in text)
    forbidden = tuple(marker for marker in FORBIDDEN_AUTHORITY_MARKERS if marker in text)
    ok = path.is_file() and not missing_false and not missing_true and not forbidden
    return {
        "status": "PASS" if ok else "BLOCKED",
        "contract_path": ORCHESTRATOR_CONTRACT,
        "missing_required_false_markers": missing_false,
        "missing_required_true_markers": missing_true,
        "forbidden_authority_markers": forbidden,
        "operational_authority": False,
        "writes_active_signals": False,
        "signal_published": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "exchange_private_access": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "paper_treatment_release_allowed": False,
    }


def audit_aibot_parity_v2(project_root: str | Path) -> dict[str, Any]:
    """Audit software DoD evidence without executing any project subsystem."""

    root = Path(project_root).resolve(strict=False)
    wave_results = {
        wave: _wave_result(root, wave, required)
        for wave, required in sorted(WAVE_EVIDENCE.items())
    }
    wave_results["W10"] = {
        "wave": "W10",
        "status": W10_STATUS,
        "reason": "qlib_security_gate_blocked_external_no_bypass",
    }
    wave_results["W11"] = {
        "wave": "W11",
        "status": W11_STATUS,
        "reason": "rl_ppo_is_conditional_and_not_required_for_initial_paper_candidate",
    }

    safety = _safety_result(root)
    required_waves = tuple(f"W{index}" for index in range(1, 10)) + ("W12", "W13")
    required_pass = all(wave_results[wave]["status"] == "PASS" for wave in required_waves)
    qlib_isolated = wave_results["W10"]["status"] in {"PASS", "BLOCKED_EXTERNAL"}
    rl_resolved = wave_results["W11"]["status"] in {"PASS", "CONDITIONAL_NOT_RUN"}
    software_dod_pass = required_pass and qlib_isolated and rl_resolved and safety["status"] == "PASS"

    wave_results["W14"] = {
        "wave": "W14",
        "status": "PASS" if software_dod_pass else "BLOCKED",
        "reason": "software_dod_evidence_complete" if software_dod_pass else "software_dod_evidence_incomplete",
    }

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if software_dod_pass else "BLOCKED",
        "decision": "SOFTWARE_DOD_PASS" if software_dod_pass else "SOFTWARE_DOD_BLOCKED",
        "waves": wave_results,
        "safety": safety,
        "aibot_parity_v2_software_dod": "PASS" if software_dod_pass else "BLOCKED",
        "ready_for_paper_candidate_evaluation": software_dod_pass,
        "paper_treatment_release_allowed": False,
        "paper_activation_performed": False,
        "qlib_security_gate_bypassed": False,
        "operational_authority": False,
        "next_step": (
            "aibot_parity_paper_ab_soak_candidate_evaluation"
            if software_dod_pass
            else "remediate_missing_software_dod_evidence"
        ),
    }
    result["audit_sha256"] = _stable_sha256(result)
    return result


__all__ = [
    "SCHEMA_VERSION",
    "WAVE_EVIDENCE",
    "audit_aibot_parity_v2",
]
