"""Read-only checkpoint certification for Paper-B post-fix causal soak evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.learning.qlib_v3_prospective.activation import read_object
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    EvidenceError,
    digest,
    utc,
)
from smartcrypto.research.canonical_treatment import causal_epoch2_foundation as epoch2
from smartcrypto.research.canonical_treatment import causal_governance as governance
from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import (
    DEFAULT_BASELINE_RELATIVE_PATH,
    validate_baseline,
)
from smartcrypto.research.canonical_treatment.selector_economic_attribution import (
    SCHEMA_VERSION as ATTRIBUTION_SCHEMA_VERSION,
    run_attribution,
)
from smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor import (
    epoch2_identity,
    load_epoch2_registration,
    report_output_path,
)
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_json,
)

SCHEMA_VERSION = "paper_b_soak_checkpoint_certification_v1"
DEFAULT_REPORT = Path(
    "data/reports/canonical_treatment/"
    "paper_b_soak_checkpoint_certification_v1.json"
)

MIN_OBSERVATION_DAYS = 45.0
MIN_RESOLVED_DECISIONS = 200
MIN_SELECTED_CLOSED = 50
MIN_SCORER_COVERAGE = 0.99
MIN_POSTFIX_ELIGIBLE = 100

ELIGIBLE_CHECKPOINTS = (25, 50, 100)
RESOLVED_CHECKPOINTS = (50, 100, 200)
SELECTED_CLOSED_CHECKPOINTS = (10, 25, 50)

SAFETY: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only": True,
    "operational_authority": False,
    "economic_edge_certified": False,
    "promotion_allowed": False,
    "changes_model": False,
    "changes_threshold": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_leverage": False,
    "changes_stake": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "runs_training": False,
}


def _require_hex64(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise EvidenceError("checkpoint_sha256_invalid:" + field)
    return value


def _require_count(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise EvidenceError("checkpoint_count_invalid:" + field)
    return value


def _require_ratio(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("checkpoint_ratio_invalid:" + field)
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise EvidenceError("checkpoint_ratio_invalid:" + field)
    return result


def _validate_attribution_hash(attribution: Mapping[str, Any]) -> str:
    seal = _require_hex64(
        attribution.get("report_sha256"),
        field="attribution_report_sha256",
    )
    body = {
        key: value
        for key, value in attribution.items()
        if key != "report_sha256"
    }
    if digest(body) != seal:
        raise EvidenceError("checkpoint_attribution_hash_invalid")
    return seal


def _validate_attribution_safety(attribution: Mapping[str, Any]) -> None:
    safety = attribution.get("safety")
    if not isinstance(safety, Mapping):
        raise EvidenceError("checkpoint_attribution_safety_missing")

    required = {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "changes_model": False,
        "changes_threshold": False,
        "changes_strategy": False,
        "changes_risk": False,
        "sends_orders": False,
    }
    for field, expected in required.items():
        if safety.get(field) is not expected:
            raise EvidenceError(
                "checkpoint_attribution_safety_invalid:" + field
            )


def _checkpoint_state(observed: float | int, required: float | int) -> str:
    return "PASS" if observed >= required else "PENDING_SAMPLE"


def _postfix_summary(
    attribution: Mapping[str, Any], epoch_identity: Mapping[str, str] | None = None
) -> Mapping[str, Any]:
    by = attribution.get("by")
    if not isinstance(by, Mapping):
        raise EvidenceError("checkpoint_attribution_groups_invalid")
    epoch = by.get("epoch")
    if not isinstance(epoch, Mapping):
        raise EvidenceError("checkpoint_attribution_epoch_group_invalid")
    if epoch_identity is not None and set(epoch) != {"EPOCH_2"}:
        raise EvidenceError("checkpoint_cross_epoch_group_invalid")
    postfix = epoch.get("EPOCH_2" if epoch_identity is not None else "POST_FIX")
    if not isinstance(postfix, Mapping):
        raise EvidenceError("checkpoint_postfix_summary_missing")
    return postfix


def build_checkpoint_report(
    *,
    attribution: Mapping[str, Any],
    formal_activation_utc: str,
    fix_deployed_at_utc: str,
    baseline_sha256: str,
    epoch_identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Certify post-fix sample/soak maturity without declaring economic edge."""

    if attribution.get("schema_version") != ATTRIBUTION_SCHEMA_VERSION:
        raise EvidenceError("checkpoint_attribution_schema_invalid")
    if attribution.get("status") != "ok":
        raise EvidenceError(
            "checkpoint_upstream_attribution_blocked:"
            + str(attribution.get("reason", "unknown"))
        )

    attribution_sha256 = _validate_attribution_hash(attribution)
    _validate_attribution_safety(attribution)

    baseline_sha = _require_hex64(
        baseline_sha256,
        field="baseline_sha256",
    )
    if attribution.get("baseline_sha256") != baseline_sha:
        raise EvidenceError("checkpoint_baseline_identity_mismatch")
    if epoch_identity is not None:
        if (epoch_identity.get("epoch_id") != epoch2.EPOCH2_ID
                or attribution.get("epoch") != epoch_identity
                or epoch_identity.get("epoch_baseline_sha256") != baseline_sha
                or utc(epoch_identity["formal_activation_utc"]) != utc(formal_activation_utc)
                or utc(fix_deployed_at_utc) != utc(formal_activation_utc)):
            raise EvidenceError("checkpoint_epoch2_identity_or_boundary_invalid")
    elif attribution.get("epoch") is not None:
        raise EvidenceError("checkpoint_unexpected_epoch_identity")

    coverage_sha256 = _require_hex64(
        attribution.get("coverage_report_sha256"),
        field="coverage_report_sha256",
    )

    generated_at = utc(attribution.get("generated_at_utc"))
    formal_activation = utc(formal_activation_utc)
    fix_deployed_at = utc(fix_deployed_at_utc)

    if fix_deployed_at < formal_activation:
        raise EvidenceError("checkpoint_fix_before_formal_activation")
    if generated_at < fix_deployed_at:
        raise EvidenceError("checkpoint_generated_before_fix")

    observation_days = (
        generated_at - fix_deployed_at
    ).total_seconds() / 86400.0

    funnel = attribution.get("coverage_funnel")
    if not isinstance(funnel, Mapping):
        raise EvidenceError("checkpoint_coverage_funnel_invalid")

    postfix_summary = _postfix_summary(attribution, epoch_identity)
    if epoch_identity is not None and attribution.get("summary") != postfix_summary:
        raise EvidenceError("checkpoint_cross_epoch_summary_invalid")

    postfix_eligible = _require_count(
        funnel.get("eligible_decision_count"),
        field="postfix_eligible",
    )
    postfix_scored = _require_count(
        funnel.get("scored_decision_count"),
        field="postfix_scored",
    )
    postfix_selected = _require_count(
        funnel.get("selected_decision_count"),
        field="postfix_selected",
    )
    postfix_allow = _require_count(
        funnel.get("allow_decision_count"),
        field="postfix_allow",
    )
    postfix_executed = _require_count(
        funnel.get("executed_decision_count"),
        field="postfix_executed",
    )
    postfix_closed = _require_count(
        funnel.get("closed_decision_count"),
        field="postfix_closed",
    )
    resolved_count = _require_count(
        postfix_summary.get("resolved_count"),
        field="postfix_resolved_count",
    )
    selected_closed_count = _require_count(
        postfix_summary.get("selected_closed_count"),
        field="postfix_selected_closed_count",
    )

    if postfix_selected != postfix_allow:
        raise EvidenceError("checkpoint_selected_allow_mismatch")

    if not (
        postfix_eligible
        >= postfix_scored
        >= postfix_selected
        >= postfix_executed
        >= postfix_closed
    ):
        raise EvidenceError("checkpoint_causal_funnel_invalid")

    if resolved_count > postfix_eligible:
        raise EvidenceError("checkpoint_resolved_exceeds_postfix_eligible")
    if epoch_identity is not None and postfix_summary.get("eligible_count") != postfix_eligible:
        raise EvidenceError("checkpoint_epoch2_eligible_mismatch")
    if selected_closed_count > postfix_selected:
        raise EvidenceError("checkpoint_selected_closed_exceeds_selected")

    coverage_value = funnel.get("coverage")
    if postfix_eligible == 0:
        if coverage_value is not None:
            raise EvidenceError("checkpoint_zero_population_coverage_invalid")
        scorer_coverage = 0.0
    else:
        scorer_coverage = _require_ratio(
            coverage_value,
            field="scorer_coverage",
        )
        expected_coverage = postfix_scored / postfix_eligible
        if not math.isclose(
            scorer_coverage,
            expected_coverage,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EvidenceError("checkpoint_coverage_identity_mismatch")

    milestones: dict[str, str] = {}
    for value in ELIGIBLE_CHECKPOINTS:
        milestones[f"checkpoint_postfix_eligible_{value}"] = (
            _checkpoint_state(postfix_eligible, value)
        )
    for value in RESOLVED_CHECKPOINTS:
        milestones[f"checkpoint_postfix_resolved_{value}"] = (
            _checkpoint_state(resolved_count, value)
        )
    for value in SELECTED_CLOSED_CHECKPOINTS:
        milestones[f"checkpoint_postfix_selected_closed_{value}"] = (
            _checkpoint_state(selected_closed_count, value)
        )
    milestones["checkpoint_postfix_observation_days_45"] = _checkpoint_state(
        observation_days,
        MIN_OBSERVATION_DAYS,
    )
    milestones["checkpoint_postfix_scorer_coverage_0_99"] = _checkpoint_state(
        scorer_coverage,
        MIN_SCORER_COVERAGE,
    )

    sample_readiness_gates = {
        "postfix_observation_days_min_45": (
            observation_days >= MIN_OBSERVATION_DAYS
        ),
        "postfix_resolved_decisions_min_200": (
            resolved_count >= MIN_RESOLVED_DECISIONS
        ),
        "postfix_selected_closed_min_50": (
            selected_closed_count >= MIN_SELECTED_CLOSED
        ),
        "postfix_scorer_coverage_min_0_99": (
            scorer_coverage >= MIN_SCORER_COVERAGE
        ),
        "postfix_eligible_min_100": (
            postfix_eligible >= MIN_POSTFIX_ELIGIBLE
        ),
    }
    sample_ready = all(sample_readiness_gates.values())

    decision = (
        "READY_FOR_SEPARATE_ECONOMIC_DECISION"
        if sample_ready
        else "CONTINUE_NATURAL_COLLECTION"
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "status": "ok",
        "reason": (
            "postfix_sample_checkpoint_ready"
            if sample_ready
            else "postfix_sample_or_time_gate_pending"
        ),
        "decision": decision,
        "lineage": {
            "baseline_sha256": baseline_sha,
            "attribution_report_sha256": attribution_sha256,
            "coverage_report_sha256": coverage_sha256,
        },
        "observation": {
            "formal_activation_utc": formal_activation.isoformat().replace(
                "+00:00", "Z"
            ),
            "fix_deployed_at_utc": fix_deployed_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "postfix_observation_days": round(observation_days, 10),
            "postfix_eligible": postfix_eligible,
            "postfix_scored": postfix_scored,
            "postfix_selected": postfix_selected,
            "postfix_executed": postfix_executed,
            "postfix_closed": postfix_closed,
            "postfix_resolved_count": resolved_count,
            "postfix_selected_closed_count": selected_closed_count,
            "postfix_scorer_coverage": scorer_coverage,
        },
        "required_sample_gate": {
            "postfix_observation_days": MIN_OBSERVATION_DAYS,
            "postfix_resolved_decisions": MIN_RESOLVED_DECISIONS,
            "postfix_selected_closed": MIN_SELECTED_CLOSED,
            "postfix_scorer_coverage": MIN_SCORER_COVERAGE,
            "postfix_eligible": MIN_POSTFIX_ELIGIBLE,
        },
        "milestones": milestones,
        "sample_readiness_gates": sample_readiness_gates,
        "sample_readiness_passed": sample_ready,
        "ready_for_separate_economic_decision": sample_ready,
        "economic_gate": {
            "evaluated": False,
            "economic_edge_certified": False,
            "reason": (
                "cost_stressed_and_bootstrap_economic_gate_"
                "not_evaluated_by_br04"
            ),
        },
        "promotion_allowed": False,
        "operational_authority": False,
        "next_action": (
            "RUN_SEPARATE_ECONOMIC_DECISION_GATE"
            if sample_ready
            else "CONTINUE_NATURAL_COLLECTION"
        ),
        "safety": dict(SAFETY),
        "write_performed": False,
    }
    if epoch_identity is not None:
        report["epoch"] = dict(epoch_identity)
        report["observation"]["epoch_activation_utc"] = report["observation"].pop(
            "fix_deployed_at_utc"
        )
    report["report_sha256"] = digest(report)
    return report


def run_certification(
    *,
    project_root: Path,
    runtime_root: Path,
    write_report: bool = False,
    epoch_registration: Path | None = None,
) -> dict[str, Any]:
    """Build BR04 certification from the existing BR03 attribution."""

    project = project_root.resolve()
    runtime = runtime_root.resolve()
    output = report_output_path(project, DEFAULT_REPORT, epoch_registration)

    try:
        attribution = run_attribution(
            project_root=project,
            runtime_root=runtime,
            write_report=False,
            **({"epoch_registration": epoch_registration} if epoch_registration is not None else {}),
        )
        if attribution.get("status") != "ok":
            raise EvidenceError(
                "checkpoint_upstream_attribution_blocked:"
                + str(attribution.get("reason", "unknown"))
            )

        registration = None
        if epoch_registration is None:
            baseline = read_object(runtime / DEFAULT_BASELINE_RELATIVE_PATH)
            validate_baseline(baseline)
        else:
            current = governance.audit_snapshots(
                governance.collect_runtime(runtime), git=governance._git(project)
            )
            registration = load_epoch2_registration(epoch_registration, current)
            baseline = registration["epoch_baseline"]

        report = build_checkpoint_report(
            attribution=attribution,
            formal_activation_utc=str(baseline["formal_activation_utc"]),
            fix_deployed_at_utc=str(
                baseline["fix_deployed_at_utc"] if registration is None
                else baseline["formal_activation_utc"]
            ),
            baseline_sha256=str(
                baseline["baseline_sha256"] if registration is None
                else baseline["epoch_baseline_sha256"]
            ),
            epoch_identity=epoch2_identity(registration) if registration is not None else None,
        )
    except (
        EvidenceError,
        epoch2.CausalEpoch2FoundationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, (EvidenceError, epoch2.CausalEpoch2FoundationError))
            else "checkpoint_source_invalid:" + type(exc).__name__
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "BLOCKED_CHECKPOINT_CERTIFICATION",
            "ready_for_separate_economic_decision": False,
            "economic_gate": {
                "evaluated": False,
                "economic_edge_certified": False,
                "reason": "upstream_or_source_gate_blocked",
            },
            "promotion_allowed": False,
            "operational_authority": False,
            "next_action": "FIX_EVIDENCE_INTEGRITY_BEFORE_CONTINUING",
            "safety": dict(SAFETY),
            "write_performed": False,
        }

    report["write_requested"] = bool(write_report)
    if write_report:
        output.parent.mkdir(parents=True, exist_ok=True)
        report["write_performed"] = True

    report.pop("report_sha256", None)
    report["report_sha256"] = digest(report)

    if write_report:
        policy = AtomicWritePolicy.restricted(
            [output.parent],
            working_directory=project,
        )
        atomic_write_json(
            output,
            report,
            policy=policy,
            allow_nan=False,
        )

    return report
