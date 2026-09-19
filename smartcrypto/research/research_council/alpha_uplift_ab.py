"""Research-only economic A/B evaluation for Research Council context.

The evaluator compares the same quant-stack candidate population with and
without a frozen Council veto/context policy. It never sends orders, never
changes RiskManager, and never fabricates missing Council A/B evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "research_council_alpha_uplift_ab_v1"
EVIDENCE_SCHEMA_VERSION = "research_council_alpha_uplift_ab_evidence_v1"
SCORECARD_SCHEMA_VERSION = "economic_control_treatment_scorecard_v1"
EXPECTED_QUANT_COST_BASIS = "net_pnl_after_quant_execution_costs"

MIN_PAIRED_CANDIDATES = 30
MIN_DISTINCT_PERIODS = 3
MIN_POSITIVE_PERIODS = 2
MIN_POSITIVE_PERIOD_RATE = 0.50

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "decision_path_influence_allowed": False,
    "promotion_allowed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_leverage": False,
    "changes_stake": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
}


class ResearchCouncilAlphaUpliftError(RuntimeError):
    """Fail-closed validation error for Branch 16."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchCouncilAlphaUpliftError(f"mapping_required:{field}")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ResearchCouncilAlphaUpliftError(f"invalid_numeric:{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ResearchCouncilAlphaUpliftError(f"invalid_numeric:{field}") from exc
    if not math.isfinite(number):
        raise ResearchCouncilAlphaUpliftError(f"non_finite:{field}")
    return number


def _utc(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ResearchCouncilAlphaUpliftError(f"timestamp_missing:{field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchCouncilAlphaUpliftError(f"timestamp_invalid:{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchCouncilAlphaUpliftError(f"timestamp_not_timezone_aware:{field}")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _validate_sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ResearchCouncilAlphaUpliftError(f"invalid_sha256:{field}")
    return text


def _validate_scorecard(scorecard: Mapping[str, Any]) -> None:
    if scorecard.get("schema_version") != SCORECARD_SCHEMA_VERSION:
        raise ResearchCouncilAlphaUpliftError("scorecard_schema_mismatch")
    if scorecard.get("status") != "ok":
        raise ResearchCouncilAlphaUpliftError(
            f"scorecard_status_not_ok:{scorecard.get('status')}"
        )
    if scorecard.get("decision") != "SCORECARD_READY_RESEARCH_ONLY":
        raise ResearchCouncilAlphaUpliftError("scorecard_not_research_ready")
    for key in ("paper_only", "shadow_only", "research_only"):
        if scorecard.get(key) is not True:
            raise ResearchCouncilAlphaUpliftError(
                f"scorecard_safety_true_mismatch:{key}"
            )
    for key in (
        "operational_authority",
        "promotion_allowed",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "writes_runtime",
        "writes_data",
    ):
        if scorecard.get(key) is not False:
            raise ResearchCouncilAlphaUpliftError(
                f"scorecard_safety_false_mismatch:{key}"
            )
    source = _mapping(
        scorecard.get("source_of_financial_truth"),
        "scorecard.source_of_financial_truth",
    )
    if source.get("new_financial_source_created") is not False:
        raise ResearchCouncilAlphaUpliftError("scorecard_financial_truth_diverged")
    _validate_sha256(
        scorecard.get("scorecard_evidence_sha256"),
        "scorecard.scorecard_evidence_sha256",
    )


def _validate_evidence(
    evidence: Mapping[str, Any],
    *,
    scorecard_sha256: str,
) -> dict[str, Any]:
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ResearchCouncilAlphaUpliftError("council_ab_evidence_schema_mismatch")

    experiment_id = str(evidence.get("experiment_id") or "").strip()
    policy_id = str(evidence.get("policy_id") or "").strip()
    compute_cost_basis = str(evidence.get("compute_cost_basis") or "").strip()
    if not experiment_id:
        raise ResearchCouncilAlphaUpliftError("experiment_id_missing")
    if not policy_id:
        raise ResearchCouncilAlphaUpliftError("policy_id_missing")
    if not compute_cost_basis:
        raise ResearchCouncilAlphaUpliftError("compute_cost_basis_missing")

    policy_sha256 = _validate_sha256(
        evidence.get("policy_sha256"),
        "policy_sha256",
    )
    evidence_scorecard_sha = _validate_sha256(
        evidence.get("source_scorecard_evidence_sha256"),
        "source_scorecard_evidence_sha256",
    )
    if evidence_scorecard_sha != scorecard_sha256:
        raise ResearchCouncilAlphaUpliftError("source_scorecard_evidence_sha_mismatch")
    if evidence.get("quant_cost_basis") != EXPECTED_QUANT_COST_BASIS:
        raise ResearchCouncilAlphaUpliftError("quant_cost_basis_mismatch")
    if evidence.get("policy_frozen_before_oos") is not True:
        raise ResearchCouncilAlphaUpliftError("council_policy_not_frozen_before_oos")

    rows_raw = evidence.get("rows")
    if not isinstance(rows_raw, list):
        raise ResearchCouncilAlphaUpliftError("council_ab_rows_missing")

    rows: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for raw in rows_raw:
        row = _mapping(raw, "council_ab.row")
        candidate_id = str(row.get("candidate_id") or "").strip()
        period_id = str(row.get("period_id") or "").strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        side = str(row.get("side") or "").strip().lower()
        snapshot_id = str(row.get("council_snapshot_id") or "").strip()
        action = str(row.get("council_action") or "").strip().upper()

        if not candidate_id or candidate_id in seen_candidates:
            raise ResearchCouncilAlphaUpliftError(
                "duplicate_or_missing_candidate_id"
            )
        seen_candidates.add(candidate_id)
        if not period_id:
            raise ResearchCouncilAlphaUpliftError(
                f"period_id_missing:{candidate_id}"
            )
        if not symbol:
            raise ResearchCouncilAlphaUpliftError(
                f"symbol_missing:{candidate_id}"
            )
        if side not in {"long", "short"}:
            raise ResearchCouncilAlphaUpliftError(
                f"side_invalid:{candidate_id}:{side}"
            )
        if not snapshot_id:
            raise ResearchCouncilAlphaUpliftError(
                f"council_snapshot_id_missing:{candidate_id}"
            )
        if action not in {"ALLOW", "ABSTAIN"}:
            raise ResearchCouncilAlphaUpliftError(
                f"council_action_invalid:{candidate_id}:{action}"
            )

        decision_time = _utc(
            row.get("candidate_decision_time_utc"),
            f"{candidate_id}.candidate_decision_time_utc",
        )
        council_available = _utc(
            row.get("council_available_at_utc"),
            f"{candidate_id}.council_available_at_utc",
        )
        close_time = _utc(
            row.get("outcome_close_time_utc"),
            f"{candidate_id}.outcome_close_time_utc",
        )
        if council_available > decision_time:
            raise ResearchCouncilAlphaUpliftError(
                f"council_context_after_candidate_decision:{candidate_id}"
            )
        if close_time <= decision_time:
            raise ResearchCouncilAlphaUpliftError(
                f"outcome_not_after_candidate_decision:{candidate_id}"
            )

        base_pnl = _finite(
            row.get("quant_net_pnl_after_costs"),
            f"{candidate_id}.quant_net_pnl_after_costs",
        )
        latency_ms = _finite(
            row.get("council_latency_ms"),
            f"{candidate_id}.council_latency_ms",
        )
        compute_cost = _finite(
            row.get("council_compute_cost_usdt"),
            f"{candidate_id}.council_compute_cost_usdt",
        )
        if latency_ms < 0.0:
            raise ResearchCouncilAlphaUpliftError(
                f"council_latency_negative:{candidate_id}"
            )
        if compute_cost < 0.0:
            raise ResearchCouncilAlphaUpliftError(
                f"council_compute_cost_negative:{candidate_id}"
            )

        treatment_pnl = (
            base_pnl - compute_cost if action == "ALLOW" else -compute_cost
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "period_id": period_id,
                "symbol": symbol,
                "side": side,
                "candidate_decision_time_utc": decision_time.isoformat(),
                "council_available_at_utc": council_available.isoformat(),
                "outcome_close_time_utc": close_time.isoformat(),
                "quant_net_pnl_after_costs": base_pnl,
                "council_action": action,
                "council_latency_ms": latency_ms,
                "council_compute_cost_usdt": compute_cost,
                "council_snapshot_id": snapshot_id,
                "council_snapshot_sha256": _validate_sha256(
                    row.get("council_snapshot_sha256"),
                    f"{candidate_id}.council_snapshot_sha256",
                ),
                "treatment_net_pnl_after_costs": treatment_pnl,
                "delta_net_pnl": treatment_pnl - base_pnl,
            }
        )

    rows.sort(
        key=lambda item: (
            item["candidate_decision_time_utc"],
            item["candidate_id"],
        )
    )
    return {
        "experiment_id": experiment_id,
        "policy_id": policy_id,
        "policy_sha256": policy_sha256,
        "quant_cost_basis": EXPECTED_QUANT_COST_BASIS,
        "compute_cost_basis": compute_cost_basis,
        "rows": rows,
    }


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return float(worst)


def _metrics(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "candidate_count": 0,
            "net_pnl": 0.0,
            "ev_per_candidate": None,
            "profit_factor": None,
            "max_drawdown": None,
        }
    gross_profit = sum(value for value in values if value > 0.0)
    gross_loss = abs(sum(value for value in values if value < 0.0))
    return {
        "candidate_count": len(values),
        "net_pnl": float(sum(values)),
        "ev_per_candidate": float(sum(values) / len(values)),
        "profit_factor": (
            float(gross_profit / gross_loss) if gross_loss > 0.0 else None
        ),
        "max_drawdown": _max_drawdown(values),
    }


def _quantile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _period_reports(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["period_id"])].append(row)

    reports: list[dict[str, Any]] = []
    for period_id in sorted(grouped):
        period_rows = grouped[period_id]
        control = [float(row["quant_net_pnl_after_costs"]) for row in period_rows]
        treatment = [
            float(row["treatment_net_pnl_after_costs"]) for row in period_rows
        ]
        control_net = float(sum(control))
        treatment_net = float(sum(treatment))
        reports.append(
            {
                "period_id": period_id,
                "candidate_count": len(period_rows),
                "control_net_pnl": control_net,
                "treatment_net_pnl": treatment_net,
                "delta_net_pnl": treatment_net - control_net,
            }
        )
    return reports


def _rates(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [row for row in rows if float(row["quant_net_pnl_after_costs"]) < 0.0]
    winners = [row for row in rows if float(row["quant_net_pnl_after_costs"]) > 0.0]
    avoided_bad = sum(row["council_action"] == "ABSTAIN" for row in losers)
    false_abstentions = sum(row["council_action"] == "ABSTAIN" for row in winners)
    return {
        "control_loser_count": len(losers),
        "control_winner_count": len(winners),
        "avoided_bad_trade_count": avoided_bad,
        "false_abstention_count": false_abstentions,
        "bad_trade_avoidance_rate": (
            avoided_bad / len(losers) if losers else None
        ),
        "false_abstention_rate": (
            false_abstentions / len(winners) if winners else None
        ),
    }


def build_research_council_alpha_uplift_ab_from_scorecard(
    *,
    scorecard_report: Mapping[str, Any],
    council_ab_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate Council incremental economics on one paired OOS population."""

    _validate_scorecard(scorecard_report)
    scorecard_sha256 = str(scorecard_report["scorecard_evidence_sha256"])

    if council_ab_evidence is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "waiting",
            "reason": "paired_council_ab_evidence_not_materialized",
            "decision": "WAITING_FOR_COUNCIL_AB_EVIDENCE",
            "source_scorecard_evidence_sha256": scorecard_sha256,
            "paired_candidate_count": 0,
            "period_count": 0,
            "control_metrics": None,
            "treatment_metrics": None,
            "uplift": None,
            "period_reports": [],
            "repeatability": {
                "minimum_distinct_periods": MIN_DISTINCT_PERIODS,
                "minimum_positive_periods": MIN_POSITIVE_PERIODS,
                "minimum_positive_period_rate": MIN_POSITIVE_PERIOD_RATE,
                "repeatable_positive_uplift": False,
            },
            "council_effectiveness": None,
            "latency": None,
            "compute_cost": None,
            "ab_policy": {
                "same_candidate_population": True,
                "same_period_rows": True,
                "same_quant_cost_basis": True,
                "council_compute_cost_charged_to_treatment": True,
                "future_outcome_used_for_council_decision": False,
            },
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }
        payload["council_ab_evidence_sha256"] = _canonical_hash(payload)
        return payload

    evidence = _validate_evidence(
        council_ab_evidence,
        scorecard_sha256=scorecard_sha256,
    )
    rows = evidence["rows"]
    control_values = [float(row["quant_net_pnl_after_costs"]) for row in rows]
    treatment_values = [
        float(row["treatment_net_pnl_after_costs"]) for row in rows
    ]
    control = _metrics(control_values)
    treatment = _metrics(treatment_values)

    delta_net = float(treatment["net_pnl"] - control["net_pnl"])
    control_ev = control["ev_per_candidate"]
    treatment_ev = treatment["ev_per_candidate"]
    delta_ev = (
        float(treatment_ev - control_ev)
        if treatment_ev is not None and control_ev is not None
        else None
    )
    period_reports = _period_reports(rows)
    positive_period_count = sum(
        float(period["delta_net_pnl"]) > 0.0 for period in period_reports
    )
    period_count = len(period_reports)
    positive_period_rate = (
        positive_period_count / period_count if period_count else 0.0
    )
    repeatable = (
        period_count >= MIN_DISTINCT_PERIODS
        and positive_period_count >= MIN_POSITIVE_PERIODS
        and positive_period_rate > MIN_POSITIVE_PERIOD_RATE
    )
    sample_sufficient = len(rows) >= MIN_PAIRED_CANDIDATES

    latency_values = [float(row["council_latency_ms"]) for row in rows]
    compute_cost_values = [
        float(row["council_compute_cost_usdt"]) for row in rows
    ]
    total_compute_cost = float(sum(compute_cost_values))

    if not sample_sufficient or period_count < MIN_DISTINCT_PERIODS:
        decision = "COLLECT/INSUFFICIENT"
        reason = "council_ab_sample_or_period_coverage_insufficient"
    elif delta_net > 0.0 and delta_ev is not None and delta_ev > 0.0 and repeatable:
        decision = "COUNCIL_UPLIFT_RESEARCH_CANDIDATE"
        reason = "positive_repeatable_net_uplift_after_council_compute_cost"
    else:
        decision = "COUNCIL_ANALYTICAL_ONLY"
        reason = "positive_repeatable_net_uplift_not_proven"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": reason,
        "decision": decision,
        "source_scorecard_evidence_sha256": scorecard_sha256,
        "experiment_id": evidence["experiment_id"],
        "policy_id": evidence["policy_id"],
        "policy_sha256": evidence["policy_sha256"],
        "quant_cost_basis": evidence["quant_cost_basis"],
        "compute_cost_basis": evidence["compute_cost_basis"],
        "paired_candidate_count": len(rows),
        "period_count": period_count,
        "sample_sufficient": sample_sufficient,
        "minimum_paired_candidates": MIN_PAIRED_CANDIDATES,
        "control_metrics": control,
        "treatment_metrics": treatment,
        "uplift": {
            "delta_net_pnl": delta_net,
            "delta_ev_per_candidate": delta_ev,
            "delta_profit_factor": (
                float(treatment["profit_factor"] - control["profit_factor"])
                if treatment["profit_factor"] is not None
                and control["profit_factor"] is not None
                else None
            ),
            "compute_cost_included": True,
        },
        "period_reports": period_reports,
        "repeatability": {
            "minimum_distinct_periods": MIN_DISTINCT_PERIODS,
            "minimum_positive_periods": MIN_POSITIVE_PERIODS,
            "minimum_positive_period_rate": MIN_POSITIVE_PERIOD_RATE,
            "positive_period_count": positive_period_count,
            "positive_period_rate": positive_period_rate,
            "repeatable_positive_uplift": repeatable,
        },
        "council_effectiveness": _rates(rows),
        "latency": {
            "observation_count": len(latency_values),
            "mean_ms": (
                float(sum(latency_values) / len(latency_values))
                if latency_values
                else None
            ),
            "p50_ms": _quantile(latency_values, 0.50),
            "p95_ms": _quantile(latency_values, 0.95),
            "max_ms": max(latency_values) if latency_values else None,
        },
        "compute_cost": {
            "observation_count": len(compute_cost_values),
            "total_usdt": total_compute_cost,
            "mean_usdt_per_candidate": (
                total_compute_cost / len(compute_cost_values)
                if compute_cost_values
                else None
            ),
            "basis": evidence["compute_cost_basis"],
        },
        "ab_policy": {
            "same_candidate_population": True,
            "same_period_rows": True,
            "same_quant_cost_basis": True,
            "council_compute_cost_charged_to_treatment": True,
            "council_action_space": ["ALLOW", "ABSTAIN"],
            "future_outcome_used_for_council_decision": False,
            "policy_frozen_before_oos": True,
        },
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    payload["council_ab_evidence_sha256"] = _canonical_hash(payload)
    json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchCouncilAlphaUpliftError(
            f"invalid_council_ab_evidence_json:{path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchCouncilAlphaUpliftError(
            "council_ab_evidence_json_object_required"
        )
    return payload


def build_research_council_alpha_uplift_ab_v1(
    *,
    project_root: str | Path,
    master_path: str | Path,
    dataset_path: str | Path,
    feature_contract_path: str | Path,
    dataset_manifest_path: str | Path,
    split_manifest_path: str | Path,
    shadow_evidence_path: str | Path | None = None,
    council_ab_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute Branch 14 and evaluate optional paired Council A/B evidence."""

    evidence_path: Path | None = None
    evidence_sha256: str | None = None
    try:
        from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
            build_economic_control_treatment_scorecard_v1,
        )

        scorecard = build_economic_control_treatment_scorecard_v1(
            project_root=project_root,
            master_path=master_path,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
            shadow_evidence_path=shadow_evidence_path,
        )

        evidence = None
        if council_ab_evidence_path is not None:
            candidate = Path(council_ab_evidence_path)
            evidence_path = (
                candidate
                if candidate.is_absolute()
                else Path(project_root).resolve() / candidate
            ).resolve()
            if not evidence_path.is_file() or evidence_path.is_symlink():
                raise ResearchCouncilAlphaUpliftError(
                    f"council_ab_evidence_missing_or_invalid:{evidence_path}"
                )
            evidence_sha256 = _sha256(evidence_path)
            evidence = _read_json(evidence_path)

        report = build_research_council_alpha_uplift_ab_from_scorecard(
            scorecard_report=scorecard,
            council_ab_evidence=evidence,
        )
        report["source"] = {
            "council_ab_evidence_path": (
                str(evidence_path) if evidence_path is not None else None
            ),
            "council_ab_evidence_file_sha256": evidence_sha256,
            "council_ab_evidence_supplied": evidence_path is not None,
        }
        return report
    except (
        ResearchCouncilAlphaUpliftError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ResearchCouncilAlphaUpliftError)
            else f"research_council_alpha_uplift_ab_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "COUNCIL_AB_BLOCKED",
            "source": {
                "council_ab_evidence_path": (
                    str(evidence_path) if evidence_path is not None else None
                ),
                "council_ab_evidence_file_sha256": evidence_sha256,
                "council_ab_evidence_supplied": evidence_path is not None,
            },
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "MIN_DISTINCT_PERIODS",
    "MIN_PAIRED_CANDIDATES",
    "MIN_POSITIVE_PERIODS",
    "ResearchCouncilAlphaUpliftError",
    "SCHEMA_VERSION",
    "build_research_council_alpha_uplift_ab_from_scorecard",
    "build_research_council_alpha_uplift_ab_v1",
]
