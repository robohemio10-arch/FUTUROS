"""Deterministic assignment and treatment eligibility for Paper A/B research."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from .contracts import (
    REQUIRED_TREATMENT_GATES,
    AssignmentRecord,
    ExperimentConfig,
)


ALLOWED_CANDIDATE_EV_STATUSES = frozenset({"AVAILABLE"})


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def treatment_eligibility(
    estimate: Mapping[str, Any],
    global_gates: Mapping[str, Any],
    qlib_security_evidence: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Fail-closed eligibility contract for a Financial AI candidate estimate."""

    blockers: list[str] = []
    candidate_id = _text(estimate.get("candidate_id"))
    if candidate_id is None:
        blockers.append("CANDIDATE_ID_MISSING")

    if str(estimate.get("candidate_linkage_status") or "") != "LINKED":
        blockers.append("CANDIDATE_NOT_LINKED")
    if estimate.get("point_in_time_consumable") is not True:
        blockers.append("POINT_IN_TIME_NOT_CONSUMABLE")
    if estimate.get("branch2_compatible") is not True:
        blockers.append("BRANCH2_INCOMPATIBLE")
    if estimate.get("financial_estimate_trusted") is not True:
        blockers.append("FINANCIAL_ESTIMATE_NOT_TRUSTED")

    candidate_ev = _finite_float(estimate.get("candidate_ev"))
    if candidate_ev is None:
        blockers.append("CANDIDATE_EV_MISSING")

    candidate_ev_status = str(estimate.get("candidate_ev_status") or "")
    if candidate_ev_status not in ALLOWED_CANDIDATE_EV_STATUSES:
        blockers.append("CANDIDATE_EV_STATUS_NOT_AVAILABLE")

    for gate in REQUIRED_TREATMENT_GATES:
        if global_gates.get(gate) is not True:
            blockers.append(f"GLOBAL_GATE_FALSE:{gate}")

    if qlib_security_evidence.get("gate_passed") is not True:
        reason = str(qlib_security_evidence.get("reason") or "UNKNOWN")
        blockers.append(f"QLIB_DEPENDENCY_SECURITY_BLOCKED:{reason}")

    return not blockers, tuple(blockers)


def assign_candidate(
    config: ExperimentConfig,
    estimate: Mapping[str, Any],
    *,
    global_gates: Mapping[str, Any],
    qlib_security_evidence: Mapping[str, Any],
) -> AssignmentRecord:
    """Assign an eligible candidate 50/50 using canonical SHA256 material.

    The arm depends only on ``experiment_id`` and ``candidate_id``.  Outcome,
    PnL, exit reason and runtime execution timestamps are intentionally absent.
    """

    candidate_id = _text(estimate.get("candidate_id"))
    observed_at = _text(estimate.get("observed_at_utc"))
    candidate_ev = _finite_float(estimate.get("candidate_ev"))
    candidate_ev_status = str(estimate.get("candidate_ev_status") or "")

    eligible, blockers = treatment_eligibility(
        estimate,
        global_gates,
        qlib_security_evidence,
    )

    if candidate_id is None:
        return AssignmentRecord(
            assignment_id=None,
            experiment_id=config.experiment_id,
            assignment_version=config.assignment_salt_version,
            candidate_id=None,
            assignment_material_sha256=None,
            arm=None,
            status="INELIGIBLE_CANDIDATE_ID_MISSING",
            observed_at_utc=observed_at,
            candidate_linkage_status=str(
                estimate.get("candidate_linkage_status") or "CANDIDATE_UNLINKED"
            ),
            point_in_time_consumable=estimate.get("point_in_time_consumable") is True,
            branch2_compatible=estimate.get("branch2_compatible") is True,
            financial_estimate_trusted=estimate.get("financial_estimate_trusted") is True,
            candidate_ev=candidate_ev,
            candidate_ev_status=candidate_ev_status,
            blockers=blockers,
        )

    if not eligible:
        material = f"{config.experiment_id}|{candidate_id}".encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()
        return AssignmentRecord(
            assignment_id=None,
            experiment_id=config.experiment_id,
            assignment_version=config.assignment_salt_version,
            candidate_id=candidate_id,
            assignment_material_sha256=digest,
            arm=None,
            status="INELIGIBLE_TREATMENT_GATES",
            observed_at_utc=observed_at,
            candidate_linkage_status=str(
                estimate.get("candidate_linkage_status") or "CANDIDATE_UNLINKED"
            ),
            point_in_time_consumable=estimate.get("point_in_time_consumable") is True,
            branch2_compatible=estimate.get("branch2_compatible") is True,
            financial_estimate_trusted=estimate.get("financial_estimate_trusted") is True,
            candidate_ev=candidate_ev,
            candidate_ev_status=candidate_ev_status,
            blockers=blockers,
        )

    material = f"{config.experiment_id}|{candidate_id}".encode("utf-8")
    digest_bytes = hashlib.sha256(material).digest()
    digest = digest_bytes.hex()
    arm = "CONTROL" if digest_bytes[0] < 128 else "TREATMENT"

    return AssignmentRecord(
        assignment_id=f"ab-{digest}",
        experiment_id=config.experiment_id,
        assignment_version=config.assignment_salt_version,
        candidate_id=candidate_id,
        assignment_material_sha256=digest,
        arm=arm,
        status="ASSIGNED",
        observed_at_utc=observed_at,
        candidate_linkage_status="LINKED",
        point_in_time_consumable=True,
        branch2_compatible=True,
        financial_estimate_trusted=True,
        candidate_ev=candidate_ev,
        candidate_ev_status=candidate_ev_status,
        blockers=(),
    )
