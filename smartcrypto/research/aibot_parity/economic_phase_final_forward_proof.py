"""Final forward economic proof for the SMART FUTUROS research phase.

This module is deliberately fail-closed.  It consolidates the current prospective
Qlib V3 shadow epoch and the economic outputs from Branches 14-16, but it never
changes the frozen epoch, retrains, promotes a model, changes risk, or sends orders.

The historical Branch 17 DoD was written for the Qlib V2 prospective epoch.  V2 was
later superseded by the reproducible V3 epoch in the current repository.  The sample
and economic thresholds remain the closing criteria; V3 is the only admissible
prospective identity for new evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    parse_payload_record,
)
from smartcrypto.learning.qlib_v3_prospective.activation import Activation, parse_json, read_object
from smartcrypto.learning.qlib_v3_prospective.admission import signal as admit_signal
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    EvidenceError,
    check_identity,
    digest,
    utc,
)
from smartcrypto.research.canonical_treatment import causal_governance as governance
from smartcrypto.research.canonical_treatment.economic_monitor import _decision_id, _time
from smartcrypto.research.canonical_treatment.publisher import (
    LEDGER_SCHEMA_VERSION,
    _crosswalk_index,
)

SCHEMA_VERSION = "economic_phase_final_forward_proof_v1"
FORWARD_GATE_EVIDENCE_SCHEMA = "economic_phase_forward_gate_evidence_v1"
SCORECARD_SCHEMA = "economic_control_treatment_scorecard_v1"
PORTFOLIO_SCHEMA = "portfolio_of_alphas_oos_economic_selection_v1"
COUNCIL_SCHEMA = "research_council_alpha_uplift_ab_v1"
SHADOW_SCHEMA = "ai_shadow_economic_challenger_sync_v1"

MIN_OBSERVATION_DAYS = 45.0
MIN_RESOLVED_DECISIONS = 200
MIN_SELECTED_TRADES = 50
MIN_SCORER_COVERAGE = 0.99
MIN_TREATMENT_PROFIT_FACTOR = 1.10
CANONICAL_ADDITIONAL_EXECUTION_STRESS_BPS = 5.0
BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 42

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
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
    "runs_training": False,
    "recalibrates_current_epoch": False,
}


class EconomicPhaseFinalForwardProofError(RuntimeError):
    """Stable fail-closed validation error for Branch 17."""


def _metrics(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "max_drawdown": None,
        }
    gross_profit = sum(value for value in values if value > 0.0)
    gross_loss = abs(sum(value for value in values if value < 0.0))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "trade_count": len(values),
        "net_pnl": float(sum(values)),
        "expectancy": float(sum(values) / len(values)),
        "profit_factor": (float(gross_profit / gross_loss) if gross_loss > 0.0 else None),
        "max_drawdown": float(max_drawdown),
    }


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise EconomicPhaseFinalForwardProofError("quantile_empty")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _paired_bootstrap(deltas: Sequence[float]) -> dict[str, Any]:
    if not deltas:
        return {
            "available": False,
            "reason": "paired_forward_rows_missing",
            "resamples": 0,
        }
    # Reproducible statistical bootstrap; this generator never protects secrets.
    rng = random.Random(BOOTSTRAP_SEED)  # nosec B311
    n = len(deltas)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = 0.0
        for _index in range(n):
            total += float(deltas[rng.randrange(n)])
        samples.append(total / n)
    lower = _quantile(samples, 0.025)
    median = _quantile(samples, 0.50)
    upper = _quantile(samples, 0.975)
    return {
        "available": True,
        "metric": "paired_mean_delta_stressed_net_pnl_per_decision",
        "confidence_level": 0.95,
        "lower": lower,
        "median": median,
        "upper": upper,
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise EvidenceError("financial_value_invalid")
    return float(value)


def _base(now: datetime) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.isoformat(),
        "status": "waiting",
        "decision": "WAITING_FORWARD_EVIDENCE",
        "certification_attempted": False,
        "formal_activation_utc": None,
        "blockers": [],
        "upstream": {},
        "sample_gates": {
            name: {"value": 0, "required": threshold, "passed": False}
            for name, threshold in (
                ("observation_days", MIN_OBSERVATION_DAYS),
                ("resolved_decisions", MIN_RESOLVED_DECISIONS),
                ("selected_closed_trades", MIN_SELECTED_TRADES),
                ("scorer_coverage", MIN_SCORER_COVERAGE),
            )
        },
        "economic_gates": {
            k: {"value": None, "passed": False}
            for k in (
                "treatment_stressed_net_pnl_positive",
                "expectancy_positive",
                "profit_factor_gte_1_10",
                "delta_net_pnl_positive",
                "paired_bootstrap_ci95_lower_positive",
                "economic_dod_no_unexplained_gap",
            )
        },
        "causal_population": {"eligible_decisions": 0, "scored_decisions": 0, "coverage_misses": 0},
        **SAFETY_FLAGS,
    }


def _sealed(report: dict[str, Any]) -> dict[str, Any]:
    report["forward_proof_sha256"] = digest(report)
    return report


def _upstream(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Consume existing materialized B15/B16 reports, never synthesize inputs."""
    schemas = {
        "branch15": (PORTFOLIO_SCHEMA, "selection_evidence_sha256"),
        "branch16": (COUNCIL_SCHEMA, "council_ab_evidence_sha256"),
    }
    result = {}
    for name, (schema, seal) in schemas.items():
        report = reports.get(name)
        if report is None:
            result[name] = {
                "status": "waiting",
                "reason": "materialized_evidence_missing",
                "ready": False,
            }
            continue
        if report.get("schema_version") != schema or report.get("status") not in {
            "ok",
            "waiting",
            "blocked",
        }:
            raise EvidenceError(name + "_schema_or_status_invalid")
        # The existing B15/B16 hash excludes optional CLI source metadata.
        raw = {k: v for k, v in report.items() if k not in {seal, "source"}}
        sha = hashlib.sha256(
            json.dumps(
                raw,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
                default=str,
            ).encode()
        ).hexdigest()
        if report.get(seal) != sha:
            raise EvidenceError(name + "_hash_invalid")
        required_safety = {
            key: expected
            for key, expected in SAFETY_FLAGS.items()
            if key
            not in {
                "live_release_allowed",
                "canary_release_allowed",
                "runs_training",
                "recalibrates_current_epoch",
            }
        }
        if any(report.get(key) is not expected for key, expected in required_safety.items()):
            raise EvidenceError(name + "_required_safety_missing_or_invalid")
        if any(report.get(k) is not v for k, v in SAFETY_FLAGS.items() if k in report):
            raise EvidenceError(name + "_safety_invalid")
        ready = report["status"] == "ok"
        if ready and (
            not report.get("source_scorecard_evidence_sha256")
            or (name == "branch15" and not report.get("sleeve_count"))
            or (name == "branch16" and not report.get("period_reports"))
        ):
            raise EvidenceError(name + "_materialized_evidence_unproven")
        result[name] = {
            "status": report["status"],
            "reason": report.get("reason"),
            "ready": ready,
            "decision": report.get("decision"),
            "evidence_sha256": report[seal],
        }
    return result


def _operational_population(
    records: Sequence[Mapping[str, Any]], boundary: datetime, now: datetime
) -> dict[str, DecisionRecordV42]:
    population: dict[str, DecisionRecordV42] = {}
    seen: dict[str, str] = {}
    for raw in records:
        record = parse_payload_record(dict(raw))
        if record.event_id in seen:
            if seen[record.event_id] != record.payload_sha256:
                raise EvidenceError("operational_event_identity_collision")
            continue
        seen[record.event_id] = record.payload_sha256
        if not isinstance(record, DecisionRecordV42):
            continue
        if record.decision_timestamp > now:
            raise EvidenceError("operational_decision_in_future")
        if record.decision_timestamp < boundary or record.final_decision != "ALLOW":
            continue
        if not record.signal_id.startswith("signal:phase13-signal-producer:"):
            continue
        if not record.candidate_id.startswith("candidate:phase13-signal-producer:"):
            raise EvidenceError("phase13_candidate_lineage_invalid")
        population[record.event_id] = record
    return population


def _causal_rows(
    population: Mapping[str, DecisionRecordV42],
    evidence: Mapping[str, Any],
    ledger: Mapping[str, Any],
    boundary: datetime,
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    check_identity(evidence.get("identity"), CANONICAL)
    if evidence.get("schema_version") != "qlib_v3_prospective_evidence_store_v1" or evidence.get(
        "store_sha256"
    ) != digest({k: v for k, v in evidence.items() if k != "store_sha256"}):
        raise EvidenceError("v3_store_hash_or_schema_invalid")
    if (
        ledger.get("schema_version") != LEDGER_SCHEMA_VERSION
        or ledger.get("experiment_id") != "canonical-treatment-paper-v1"
    ):
        raise EvidenceError("treatment_ledger_schema_or_experiment_invalid")
    if not isinstance(ledger.get("rows"), list) or not isinstance(evidence.get("signals"), list):
        raise EvidenceError("causal_rows_list_required")
    index = _crosswalk_index(evidence)
    v3_by_op = {}
    activation = Activation(
        CANONICAL,
        utc(CANONICAL.prospective_start_utc),
        utc(CANONICAL.prospective_start_utc),
        "",
        "",
    )
    for row in evidence["signals"]:
        op_id = row.get("operational_crosswalk", {}).get("operational_decision_event_id")
        if op_id not in population:
            continue
        if op_id in v3_by_op:
            raise EvidenceError("duplicate_causal_v3_signal")
        admit_signal(row, activation, now)
        v3_by_op[op_id] = row
    treatment = {}
    for row in ledger["rows"]:
        if not isinstance(row, dict):
            raise EvidenceError("treatment_row_not_object")
        event = row.get("operational_decision_event_id")
        if event not in population:
            if utc(row.get("first_observed_at_utc")) >= boundary:
                # An old decision can still be observed after registration: it is excluded,
                # not assigned a new causal timestamp.
                continue
            continue
        if event in treatment:
            raise EvidenceError("duplicate_treatment_causal_event")
        treatment[event] = row
    scored, misses = {}, []
    for event, op in population.items():
        row = treatment.get(event)
        if row is None:
            misses.append(event)
            continue
        if any(
            row.get(k) != v
            for k, v in {
                "signal_id": op.signal_id,
                "operational_decision_payload_sha256": op.payload_sha256,
                "pair": op.pair,
                "symbol": op.symbol,
                "side": op.side.value,
            }.items()
        ):
            raise EvidenceError("treatment_operational_identity_mismatch")
        observed = utc(row.get("first_observed_at_utc"))
        valid_until = utc(row.get("valid_until"))
        if not op.decision_timestamp <= observed <= now or valid_until <= observed:
            raise EvidenceError("treatment_causal_time_invalid")
        if row.get("status") == "unmatched":
            if row.get("selected") is not None:
                raise EvidenceError("unmatched_treatment_selected")
            misses.append(event)
            continue
        if row.get("status") != "scored" or event not in v3_by_op:
            raise EvidenceError("treatment_without_exact_v3_parent")
        crosswalk, signal = index[event], v3_by_op[event]
        if (
            any(
                row.get(k) != v
                for k, v in crosswalk.items()
                if k
                in (
                    "signal_id",
                    "operational_decision_event_id",
                    "operational_decision_payload_sha256",
                    "v3_decision_event_id",
                    "v3_decision_payload_sha256",
                    "crosswalk_sha256",
                    "qlib_score",
                    "ai_shadow_decision",
                )
            )
            or type(row.get("selected")) is not bool
            or row["selected"] != (crosswalk["ai_shadow_decision"] == "ALLOW")
        ):
            raise EvidenceError("treatment_crosswalk_identity_or_selection_mismatch")
        decision = signal["decision"]
        if any(
            decision[k] != getattr(op, k)
            for k in ("signal_id", "candidate_id", "correlation_id", "pair", "symbol", "side")
        ):
            raise EvidenceError("v3_operational_identity_mismatch")
        signal_at = utc(signal["signal_timestamp_utc"])
        if (
            not op.decision_timestamp
            <= utc(decision["decision_timestamp"])
            <= signal_at
            < valid_until
        ):
            raise EvidenceError("v3_operational_causal_time_invalid")
        scored[event] = dict(row, signal_timestamp_utc=signal_at.isoformat())
    return scored, misses


def _financial_rows(
    rows: Sequence[Mapping[str, Any]],
    population: Mapping[str, DecisionRecordV42],
    scored: Mapping[str, Mapping[str, Any]],
    boundary: datetime,
    now: datetime,
    *,
    treatment: bool,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[int] = set()
    for row in rows:
        opened = _time(row.get("open_date"))
        if opened is None:
            raise EvidenceError("trade_open_timestamp_invalid")
        if opened < boundary:
            continue
        event = _decision_id(row.get("enter_tag"))
        if event is None or event not in population:
            raise EvidenceError("post_activation_trade_without_exact_operational_parent")
        op = population[event]
        trade_id = row.get("id")
        if type(trade_id) is not int or trade_id <= 0 or trade_id in seen:
            raise EvidenceError("trade_id_invalid_or_duplicate")
        seen.add(trade_id)
        if (
            not op.decision_timestamp <= opened <= now
            or row.get("pair") != op.pair
            or row.get("is_short") not in (0, 1)
        ):
            raise EvidenceError("trade_identity_or_time_invalid")
        if bool(row["is_short"]) != (op.side == "short") or row.get("is_open") not in (0, 1):
            raise EvidenceError("trade_side_or_state_invalid")
        if treatment:
            selection = scored.get(event)
            if not selection or selection["selected"] is not True:
                raise EvidenceError("treatment_trade_without_selected_causal_parent")
            if (
                not max(
                    utc(selection["first_observed_at_utc"]), utc(selection["signal_timestamp_utc"])
                )
                <= opened
                < utc(selection["valid_until"])
            ):
                raise EvidenceError("treatment_selection_not_ex_ante")
        closed = _time(row.get("close_date"))
        pnl = None
        stake, leverage = _number(row.get("stake_amount")), _number(row.get("leverage"))
        if stake <= 0 or leverage <= 0:
            raise EvidenceError("trade_stake_or_leverage_invalid")
        if not row["is_open"]:
            if closed is None or not opened < closed <= now:
                raise EvidenceError("trade_close_time_invalid")
            pnl = (
                _number(row.get("close_profit_abs"))
                - stake * leverage * CANONICAL_ADDITIONAL_EXECUTION_STRESS_BPS / 10000
            )
        elif closed is not None:
            raise EvidenceError("open_trade_has_close_timestamp")
        grouped.setdefault(event, []).append(
            {
                "trade_id": trade_id,
                "is_open": bool(row["is_open"]),
                "stressed_net_pnl": pnl,
                "closed_at": closed.isoformat() if closed else None,
            }
        )
    return grouped


def build_economic_phase_final_forward_proof_from_reports(
    *,
    causal_manifest: Mapping[str, Any] | None,
    parity_report: Mapping[str, Any],
    operational_records: Sequence[Mapping[str, Any]],
    v3_evidence: Mapping[str, Any],
    treatment_ledger: Mapping[str, Any],
    control_trades: Sequence[Mapping[str, Any]],
    treatment_trades: Sequence[Mapping[str, Any]],
    upstream_reports: Mapping[str, Mapping[str, Any]],
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    now = utc(as_of_utc) if as_of_utc else governance.now_utc()
    result = _base(now)
    try:
        result["upstream"] = _upstream(upstream_reports)
        governance.validate_audit(parity_report, now)
        result["parity_audit_sha256"] = parity_report["audit_sha256"]
        if causal_manifest is None:
            result["blockers"].append("causal_activation_not_registered")
            return _sealed(result)
        governance.validate_manifest(causal_manifest, parity_report, now)
        boundary = utc(causal_manifest["formal_activation_utc"])
        result["formal_activation_utc"] = boundary.isoformat()
        result["causal_manifest_sha256"] = causal_manifest["manifest_sha256"]
        population = _operational_population(operational_records, boundary, now)
        scored, misses = _causal_rows(population, v3_evidence, treatment_ledger, boundary, now)
        control = _financial_rows(
            control_trades, population, scored, boundary, now, treatment=False
        )
        treatment = _financial_rows(
            treatment_trades, population, scored, boundary, now, treatment=True
        )
        resolved = []
        for event, row in scored.items():
            a, b = control.get(event, []), treatment.get(event, [])
            if (
                utc(row["valid_until"]) > now
                or not a
                or (row["selected"] and not b)
                or any(t["is_open"] for t in a + b)
            ):
                continue
            resolved.append(
                {
                    "operational_decision_event_id": event,
                    "signal_id": row["signal_id"],
                    "control_trade_ids": [t["trade_id"] for t in a],
                    "treatment_trade_ids": [t["trade_id"] for t in b],
                    "control_stressed_net_pnl": sum(t["stressed_net_pnl"] for t in a),
                    "treatment_stressed_net_pnl": sum(t["stressed_net_pnl"] for t in b),
                }
            )
        closed_b = sorted(
            [t for items in treatment.values() for t in items if not t["is_open"]],
            key=lambda t: (t["closed_at"], t["trade_id"]),
        )
        deltas = [r["treatment_stressed_net_pnl"] - r["control_stressed_net_pnl"] for r in resolved]
        metrics = _metrics([t["stressed_net_pnl"] for t in closed_b])
        bootstrap = _paired_bootstrap(deltas)
        values = {
            "observation_days": (now - boundary).total_seconds() / 86400,
            "resolved_decisions": len(resolved),
            "selected_closed_trades": len(closed_b),
            "scorer_coverage": len(scored) / len(population) if population else 0.0,
        }
        for name, value in values.items():
            result["sample_gates"][name].update(
                value=value, passed=value >= result["sample_gates"][name]["required"]
            )
        economic = {
            "treatment_stressed_net_pnl_positive": (metrics["net_pnl"], metrics["net_pnl"] > 0),
            "expectancy_positive": (metrics["expectancy"], (metrics["expectancy"] or 0) > 0),
            "profit_factor_gte_1_10": (
                metrics["profit_factor"],
                (metrics["profit_factor"] or 0) >= MIN_TREATMENT_PROFIT_FACTOR,
            ),
            "delta_net_pnl_positive": (sum(deltas), sum(deltas) > 0),
            "paired_bootstrap_ci95_lower_positive": (
                bootstrap.get("lower"),
                (bootstrap.get("lower") or 0) > 0,
            ),
            "economic_dod_no_unexplained_gap": (len(misses), not misses),
        }
        result["economic_gates"] = {
            k: {"value": value, "passed": passed} for k, (value, passed) in economic.items()
        }
        result.update(
            causal_population={
                "eligible_decisions": len(population),
                "scored_decisions": len(scored),
                "coverage_misses": len(misses),
                "missing_operational_event_ids": misses,
            },
            treatment_metrics=metrics,
            paired_bootstrap=bootstrap,
            resolved_rows=resolved,
            cost_basis={
                "source": "actual_separate_freqtrade_close_profit_abs",
                "additional_stress_bps_on_leveraged_stake": CANONICAL_ADDITIONAL_EXECUTION_STRESS_BPS,
            },
        )
        result["blockers"] = [k for k, gate in result["sample_gates"].items() if not gate["passed"]]
        result["blockers"] += [
            name for name, state in result["upstream"].items() if not state["ready"]
        ]
        if any(s["status"] == "blocked" for s in result["upstream"].values()):
            result.update(status="blocked", decision="BLOCKED_FORWARD_EVIDENCE")
        elif not result["blockers"]:
            result["certification_attempted"] = True
            result["blockers"] = [
                k for k, gate in result["economic_gates"].items() if not gate["passed"]
            ]
            result.update(
                status="blocked" if result["blockers"] else "ok",
                decision="ECONOMIC_GATES_FAILED"
                if result["blockers"]
                else "FORWARD_PROOF_RESEARCH_ONLY",
            )
        return _sealed(result)
    except (EvidenceError, ValueError, KeyError, TypeError) as exc:
        result.update(
            status="blocked", decision="BLOCKED_FORWARD_EVIDENCE", certification_attempted=False
        )
        result["blockers"].append(
            str(exc)
            if isinstance(exc, EvidenceError)
            else "causal_integrity_invalid:" + type(exc).__name__
        )
        return _sealed(result)


def build_economic_phase_final_forward_proof_v1(
    *,
    project_root: str | Path,
    runtime_root: str | Path,
) -> dict[str, Any]:
    """Read current canonical sources in their active containers, without writes."""
    project = Path(project_root).resolve()
    now = governance.now_utc()
    result = _base(now)
    try:
        publisher = governance.CONTAINERS["publisher"]
        upstream = {
            stage: report
            for stage, filename in {
                "branch15": "branch15_portfolio_of_alphas_oos_economic_selection_v1.json",
                "branch16": "branch16_research_council_alpha_uplift_ab_v1.json",
            }.items()
            if (
                report := governance.container_optional_json(
                    publisher, "/app/data/reports/canonical_economic_plane/" + filename
                )
            )
            is not None
        }
        result["upstream"] = _upstream(upstream)
        parity = read_object(project / governance.AUDIT_PATH)
        governance.validate_audit(parity, now)
        current = governance.audit_snapshots(
            governance.collect_runtime(Path(runtime_root)), git=parity["git"]
        )
        governance.validate_audit(current, governance.now_utc())
        if current["fingerprints_sha256"] != parity["fingerprints_sha256"]:
            raise EvidenceError("runtime_changed_since_materialized_audit")
        manifest_path = project / governance.MANIFEST_PATH
        if not manifest_path.exists():
            result["blockers"].append("causal_activation_not_registered")
            return _sealed(result)
        manifest = read_object(manifest_path)
        governance.validate_manifest(manifest, current, governance.now_utc())
        raw = governance.container_read(
            publisher, "/app/data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
        )
        records = [parse_json(line) for line in raw.splitlines() if line.strip()]
        evidence = governance.container_json(
            publisher,
            "/app/data/research/qlib_v3/prospective_evidence/"
            + CANONICAL.epoch_id
            + "/"
            + CANONICAL.activation_sha256
            + "/evidence.json",
        )
        ledger = governance.container_json(
            publisher, "/app/data/research/canonical_treatment/decision_ledger_v1.json"
        )
        trades = {}
        for role in ("control", "treatment"):
            db_url = current["fingerprints"][role]["db_identity"]["url"]
            if not db_url.startswith("sqlite:////"):
                raise EvidenceError("non_sqlite_trade_source")
            trades[role] = parse_json(
                governance.container_read(
                    governance.CONTAINERS[role], db_url[len("sqlite:///") :], "sqlite"
                )
            )
        result = build_economic_phase_final_forward_proof_from_reports(
            causal_manifest=manifest,
            parity_report=current,
            operational_records=records,
            v3_evidence=evidence,
            treatment_ledger=ledger,
            control_trades=trades["control"],
            treatment_trades=trades["treatment"],
            upstream_reports=upstream,
            as_of_utc=governance.now_utc().isoformat(),
        )
        # Audit again after reads to detect process/source/DB-mount changes during collection.
        after = governance.audit_snapshots(
            governance.collect_runtime(Path(runtime_root)), git=parity["git"]
        )
        governance.validate_manifest(manifest, after, governance.now_utc())
        return result
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
        result.update(
            status="blocked", decision="BLOCKED_FORWARD_EVIDENCE", certification_attempted=False
        )
        result.pop("forward_proof_sha256", None)
        result["blockers"].append(
            str(exc)
            if isinstance(exc, EvidenceError)
            else "runtime_evidence_read_failed:" + type(exc).__name__
        )
        return _sealed(result)
