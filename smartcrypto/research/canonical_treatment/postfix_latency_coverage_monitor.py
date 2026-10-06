"""Read-only Paper-B post-fix coverage and causal latency monitor."""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from smartcrypto.learning.qlib_v3_prospective.activation import parse_json, read_object
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, EvidenceError, digest, utc
from smartcrypto.research.aibot_parity.economic_phase_final_forward_proof import (
    _causal_rows,
    _operational_population,
)
from smartcrypto.research.canonical_treatment import causal_governance as governance
from smartcrypto.research.canonical_treatment import causal_epoch2_foundation as epoch2
from smartcrypto.research.canonical_treatment.economic_monitor import _decision_id, _time
from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import (
    DEFAULT_BASELINE_RELATIVE_PATH,
    SAFETY_FLAGS,
    Snapshot,
    build_report as build_foundation_report,
    validate_baseline,
)
from smartcrypto.runtime.integrity_traceability_v2 import AtomicWritePolicy, atomic_write_json


SCHEMA_VERSION = "paper_b_postfix_latency_coverage_monitor_v1"
DEFAULT_REPORT = Path("data/reports/canonical_treatment/paper_b_postfix_latency_coverage_monitor_v1.json")
BRANCH01_BASELINE_SHA256 = "bd0d44440b6b7f8f2db6f3fc200ee32d74d3612a813f792e40e2c8a69b75eabc"
BRANCH01_BASELINE_COUNTS = (109, 98, 11)


def report_output_path(project: Path, relative: Path, epoch_registration: Path | None) -> Path:
    if epoch_registration is None:
        return project / relative
    return project / relative.parent / epoch2.EPOCH2_ID / relative.name


def validate_historical_baseline(baseline: Mapping[str, Any]) -> None:
    """Require the create-once Branch01 seal, not just a self-consistent replacement."""
    validate_baseline(baseline)
    population = baseline["baseline_population"]
    if (baseline["baseline_sha256"] != BRANCH01_BASELINE_SHA256
            or (population["eligible"], population["scored"], population["misses"])
            != BRANCH01_BASELINE_COUNTS):
        raise EvidenceError("branch01_baseline_identity_or_counts_changed")


def load_epoch2_registration(
    path: Path, current_audit: Mapping[str, Any]
) -> dict[str, Any]:
    registration = read_object(path)
    try:
        epoch2.validate_registration(registration)
        epoch2.validate_current_epoch2_runtime(registration, current_audit)
    except epoch2.CausalEpoch2FoundationError as exc:
        raise EvidenceError(str(exc)) from exc
    return registration


def epoch2_identity(registration: Mapping[str, Any]) -> dict[str, str]:
    manifest = registration["epoch_manifest"]
    baseline = registration["epoch_baseline"]
    return {
        "epoch_id": registration["epoch_id"],
        "formal_activation_utc": manifest["formal_activation_utc"],
        "registration_sha256": registration["registration_sha256"],
        "epoch_manifest_sha256": manifest["epoch_manifest_sha256"],
        "epoch_baseline_sha256": baseline["epoch_baseline_sha256"],
    }


def build_epoch2_foundation(
    registration: Mapping[str, Any], snapshot: Snapshot
) -> dict[str, Any]:
    identity = epoch2_identity(registration)
    boundary = utc(identity["formal_activation_utc"])
    eligible = set(snapshot.eligible_event_times)
    scored = set(snapshot.scored_rows)
    missing = set(snapshot.current_missing_event_ids)
    if utc(snapshot.formal_activation_utc) != boundary or any(
        timestamp < boundary for timestamp in snapshot.eligible_event_times.values()
    ):
        raise EvidenceError("epoch2_pre_activation_population")
    if scored - eligible or missing != eligible - scored:
        raise EvidenceError("epoch2_population_partition_invalid")
    post = {
        "eligible": len(eligible),
        "scored": len(scored),
        "misses": len(missing),
        "eligible_event_ids": sorted(eligible),
        "scored_event_ids": sorted(scored),
        "missing_event_ids": sorted(missing),
        "coverage": len(scored) / len(eligible) if eligible else None,
    }
    return {
        "status": "ok",
        "blockers": [],
        "baseline_sha256": identity["epoch_baseline_sha256"],
        "epoch": identity,
        "post_fix_counters": post,
        "historical_debt": {
            "eligible": 0,
            "scored_at_fix": 0,
            "misses_at_fix": 0,
            "missing_event_ids": [],
            "late_resolved_after_fix_event_ids": [],
            "root_cause_summary": {},
        },
    }


def _latency(values: Sequence[float], *, eligible: int, formula: str, sources: list[str]) -> dict[str, Any]:
    if not values:
        return {
            "status": "unavailable",
            "reason": "required_causal_timestamps_not_persisted_or_no_scored_rows",
            "formula": formula,
            "timestamp_sources": sources,
            "eligible_count": eligible,
            "count": 0,
            "min_seconds": None,
            "median_seconds": None,
            "p95_seconds": None,
            "max_seconds": None,
            "negative_interval_count": 0,
        }
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.95
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    p95 = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {
        "status": "available",
        "reason": None,
        "formula": formula,
        "timestamp_sources": sources,
        "eligible_count": eligible,
        "count": len(values),
        "min_seconds": ordered[0],
        "median_seconds": statistics.median(ordered),
        "p95_seconds": p95,
        "max_seconds": ordered[-1],
        "negative_interval_count": sum(value < 0 for value in values),
    }


def _unavailable_latency(reason: str, formula: str) -> dict[str, Any]:
    result = _latency([], eligible=0, formula=formula, sources=[])
    result["reason"] = reason
    return result


def _trade_funnel(
    trades: Sequence[Mapping[str, Any]],
    eligible: set[str],
    selected: set[str],
    scored_rows: Mapping[str, Mapping[str, Any]],
    operational_rows: Mapping[str, Mapping[str, Any]],
    fix: datetime,
    as_of: datetime,
    pre_activation_event_ids: set[str] | None = None,
) -> dict[str, Any]:
    executed: set[str] = set()
    events_with_open_trade: set[str] = set()
    orphan: list[int] = []
    inconsistent: list[int] = []
    orphan_events: set[str] = set()
    inconsistent_events: set[str] = set()
    seen_trade_ids: set[int] = set()
    for row in trades:
        opened = _time(row.get("open_date"))
        if opened is None:
            raise EvidenceError("treatment_trade_open_time_invalid")
        if opened < fix:
            continue
        trade_id = row.get("id")
        if type(trade_id) is not int or trade_id <= 0 or trade_id in seen_trade_ids:
            raise EvidenceError("treatment_trade_id_invalid_or_duplicate")
        seen_trade_ids.add(trade_id)
        event = _decision_id(row.get("enter_tag"))
        if pre_activation_event_ids and event in pre_activation_event_ids:
            continue
        if event is None or event not in eligible:
            orphan.append(trade_id)
            if event is not None:
                orphan_events.add(event)
            continue
        if event not in selected:
            inconsistent.append(trade_id)
            inconsistent_events.add(event)
            continue
        operational = operational_rows.get(event)
        selection = scored_rows[event]
        if (operational is None or row.get("pair") != operational.get("pair")
                or row.get("is_short") not in (0, 1)
                or bool(row["is_short"]) != (operational.get("side") == "short")
                or not utc(operational["decision_timestamp"]) <= opened <= as_of
                or not max(utc(selection["first_observed_at_utc"]),
                           utc(selection["signal_timestamp_utc"])) <= opened
                < utc(selection["valid_until"])):
            inconsistent.append(trade_id)
            inconsistent_events.add(event)
            continue
        executed.add(event)
        closed_at = _time(row.get("close_date"))
        if row.get("is_open") == 1 and closed_at is None:
            events_with_open_trade.add(event)
        elif (row.get("is_open") != 0 or closed_at is None
              or not opened < closed_at <= as_of):
            inconsistent.append(trade_id)
            inconsistent_events.add(event)
            events_with_open_trade.add(event)
    closed = executed - events_with_open_trade
    return {
        "selected_decision_count": len(selected),
        "executed_decision_count": len(executed),
        "closed_decision_count": len(closed),
        "selected_event_ids": sorted(selected),
        "executed_event_ids": sorted(executed),
        "closed_event_ids": sorted(closed),
        "orphan_trade_ids": sorted(orphan),
        "orphan_decision_event_ids": sorted(orphan_events),
        "inconsistent_trade_ids": sorted(inconsistent),
        "inconsistent_decision_event_ids": sorted(inconsistent_events),
        "selected_to_executed_rate": len(executed) / len(selected) if selected else None,
        "executed_to_closed_rate": len(closed) / len(executed) if executed else None,
    }


def build_monitor_report(
    *,
    foundation: Mapping[str, Any],
    scored_rows: Mapping[str, Mapping[str, Any]],
    treatment_rows: Sequence[Mapping[str, Any]],
    treatment_trades: Sequence[Mapping[str, Any]],
    operational_rows: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any],
    runtime_fingerprints: Mapping[str, Any],
    source_hashes: Mapping[str, str],
    generated_at_utc: str,
    pre_activation_event_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Summarize already validated exact causal joins; never repair identities."""
    post = foundation["post_fix_counters"]
    epoch = foundation.get("epoch")
    cumulative = None if epoch else foundation["b17_cumulative_counters"]
    fix = utc(epoch["formal_activation_utc"] if epoch else foundation["fix_deployed_at_utc"])
    eligible_ids = set(post["eligible_event_ids"])
    scored_ids = set(post["scored_event_ids"])
    missing_ids = set(post["missing_event_ids"])
    if scored_ids & missing_ids or scored_ids | missing_ids != eligible_ids:
        raise EvidenceError("postfix_population_partition_invalid")
    if (len(eligible_ids), len(scored_ids), len(missing_ids)) != (
        post["eligible"], post["scored"], post["misses"]
    ):
        raise EvidenceError("postfix_population_counts_invalid")
    if epoch:
        if (epoch.get("epoch_id") != epoch2.EPOCH2_ID
                or source_hashes.get("epoch_registration_sha256") != epoch.get("registration_sha256")
                or source_hashes.get("baseline_sha256") != epoch.get("epoch_baseline_sha256")
                or source_hashes.get("causal_manifest_sha256") != epoch.get("epoch_manifest_sha256")
                or foundation["historical_debt"]["eligible"] != 0
                or foundation["historical_debt"]["scored_at_fix"] != 0
                or foundation["historical_debt"]["misses_at_fix"] != 0
                or foundation.get("b17_cumulative_counters") is not None):
            raise EvidenceError("epoch2_cross_epoch_identity_or_carryover")
        if any(utc(operational_rows[event]["decision_timestamp"]) < fix for event in eligible_ids):
            raise EvidenceError("epoch2_pre_activation_population")
        if pre_activation_event_ids and pre_activation_event_ids & eligible_ids:
            raise EvidenceError("epoch2_population_overlap")

    by_event: dict[str, Mapping[str, Any]] = {}
    for row in treatment_rows:
        event = row.get("operational_decision_event_id")
        if event in eligible_ids:
            if event in by_event:
                raise EvidenceError("duplicate_postfix_treatment_event")
            by_event[str(event)] = row
    missing_detail = []
    for event in sorted(missing_ids):
        missing_row = by_event.get(event)
        classification = "unexplained_no_publisher_row"
        if missing_row is not None and missing_row.get("status") == "unmatched":
            classification = "exact_publisher_row_unmatched_cause_unproven"
        missing_detail.append({"decision_event_id": event, "classification": classification})

    selected = {event for event in scored_ids if scored_rows[event].get("selected") is True}
    funnel = _trade_funnel(
        treatment_trades, eligible_ids, selected, scored_rows, operational_rows,
        fix, utc(generated_at_utc), pre_activation_event_ids,
    )
    funnel.update(
        eligible_decision_count=len(eligible_ids),
        scored_decision_count=len(scored_ids),
        allow_decision_count=len(selected),
        coverage=len(scored_ids) / len(eligible_ids) if eligible_ids else None,
    )
    funnel_valid = (
        len(eligible_ids) >= len(scored_ids) >= len(selected)
        >= funnel["executed_decision_count"] >= funnel["closed_decision_count"]
        and not funnel["orphan_trade_ids"] and not funnel["inconsistent_trade_ids"]
    )

    publisher_values: list[float] = []
    end_to_end_values: list[float] = []
    timed_samples: list[tuple[datetime, float]] = []
    for event in sorted(scored_ids):
        row = scored_rows[event]
        signal_time = utc(row["signal_timestamp_utc"])
        observed = utc(row["first_observed_at_utc"])
        if event not in by_event:
            raise EvidenceError("scored_event_missing_publisher_row")
        operational = operational_rows.get(event)
        if operational is None:
            raise EvidenceError("postfix_decision_timestamp_missing")
        publisher_values.append((observed - signal_time).total_seconds())
        decision_at = utc(operational["decision_timestamp"])
        elapsed = (observed - decision_at).total_seconds()
        end_to_end_values.append(elapsed)
        timed_samples.append((decision_at, elapsed))

    latency = {
        "scheduler_delay": _unavailable_latency(
            "scheduler_due_and_run_start_not_persisted", "scheduler_run_start - scheduler_due"
        ),
        "feature_latency": _unavailable_latency(
            "feature_build_start_and_finish_not_persisted", "feature_build_finish - feature_build_start"
        ),
        "score_latency": _unavailable_latency(
            "score_start_and_finish_not_persisted", "score_finish - score_start"
        ),
        "publisher_observation_latency": _latency(
            publisher_values, eligible=len(scored_ids),
            formula="first_observed_at_utc - signal_timestamp_utc",
            sources=["treatment_ledger.first_observed_at_utc", "v3_evidence.signal_timestamp_utc"],
        ),
        "eligible_to_observed_latency": _latency(
            end_to_end_values, eligible=len(scored_ids),
            formula="first_observed_at_utc - operational_decision.decision_timestamp",
            sources=["treatment_ledger.first_observed_at_utc", "operational_ledger.decision_timestamp"],
        ),
    }
    latency_violations = sum(
        latency[key]["negative_interval_count"]
        for key in ("publisher_observation_latency", "eligible_to_observed_latency")
    )
    timed_samples.sort(key=lambda item: item[0])
    midpoint = len(timed_samples) // 2
    latency_comparison = {
        "status": "available" if midpoint and len(timed_samples) - midpoint else "unavailable",
        "method": "chronological_postfix_halves_no_stability_threshold",
        "early": _latency(
            [value for _, value in timed_samples[:midpoint]], eligible=midpoint,
            formula="first_observed_at_utc - operational_decision.decision_timestamp",
            sources=["validated_postfix_scored_rows"],
        ),
        "late": _latency(
            [value for _, value in timed_samples[midpoint:]],
            eligible=len(timed_samples) - midpoint,
            formula="first_observed_at_utc - operational_decision.decision_timestamp",
            sources=["validated_postfix_scored_rows"],
        ),
    }
    restarts = []
    registered = baseline.get("causal_runtime_registered_started_at_utc", {})
    for role in ("control", "treatment", "publisher", "monitor"):
        current_start = runtime_fingerprints.get(role, {}).get("started_at")
        if (current_start and registered.get(role)
                and governance._docker_start_key(current_start)
                > governance._docker_start_key(registered[role])):
            previous_key = governance._docker_start_key(registered[role])
            current_key = governance._docker_start_key(current_start)
            between_starts = sorted(
                event for event in eligible_ids
                if event in operational_rows
                and previous_key <= governance._docker_start_key(
                    operational_rows[event]["decision_timestamp"]
                ) < current_key
            )
            restarts.append({
                "role": role,
                "registered_started_at_utc": registered[role],
                "current_started_at_utc": current_start,
                "between_known_starts": {
                    "semantics": "between_container_start_timestamps_not_downtime",
                    "eligible_event_ids": between_starts,
                    "eligible_count": len(between_starts),
                    "scored_count": len(set(between_starts) & scored_ids),
                    "missing_count": len(set(between_starts) & missing_ids),
                },
                "gap_start_utc": None,
                "gap_end_utc": None,
                "eligible_in_gap": None,
                "scored_in_gap": None,
                "missing_in_gap": None,
                "reason": "stop_and_recovery_boundaries_not_persisted",
            })
    restart_gap = {
        "observed_restarts": restarts,
        "continuity_proven": False,
        "reason": "restart_identity_does_not_prove_observation_continuity",
    }
    blockers = list(foundation.get("blockers", []))
    if not funnel_valid:
        blockers.append("causal_funnel_inconsistent")
    if latency_violations:
        blockers.append("negative_causal_latency")
    unexplained = [row for row in missing_detail if row["classification"].startswith("unexplained")]
    soak_a_failures = list(blockers)
    if post["coverage"] != 1.0:
        soak_a_failures.append("postfix_coverage_below_1")
    if missing_detail:
        soak_a_failures.append("postfix_missing_events")
    soak_a_unknown = []
    if post["eligible"] < 50:
        soak_a_unknown.append("insufficient_postfix_eligible")
    if latency["eligible_to_observed_latency"]["status"] != "available":
        soak_a_unknown.append("latency_evidence_unavailable")
    # Coverage of generated decisions cannot prove that the scheduler generated
    # every decision due; independent due/run evidence is not persisted.
    soak_a_unknown.append("scheduler_miss_evidence_unavailable")

    soak_b_failures = list(blockers)
    if post["coverage"] is None or post["coverage"] < 0.99:
        soak_b_failures.append("postfix_coverage_below_0_99")
    if unexplained:
        soak_b_failures.append("unexplained_lineage_misses")
    soak_b_unknown = []
    if post["eligible"] < 100:
        soak_b_unknown.append("insufficient_postfix_eligible")
    if latency_comparison["status"] != "available":
        soak_b_unknown.append("latency_comparison_unavailable")

    def gate(failures: list[str], unknown: list[str]) -> dict[str, Any]:
        if blockers:
            state = "FAIL"
        elif "insufficient_postfix_eligible" in unknown:
            state = "INSUFFICIENT_EVIDENCE"
        else:
            state = "FAIL" if failures else ("INSUFFICIENT_EVIDENCE" if unknown else "PASS")
        return {"status": state, "blockers": failures + unknown}

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "status": "blocked" if blockers else "ok",
        "reason": blockers[0] if blockers else "postfix_monitor_computed",
        "decision": "RESEARCH_ONLY_MONITOR",
        "baseline_historical_debt": foundation["historical_debt"],
        "postfix_population": {
            **post, "missing_details": missing_detail,
            "late_resolved_event_ids": foundation["historical_debt"]["late_resolved_after_fix_event_ids"],
        },
        "causal_funnel": funnel,
        "latency": latency,
        "latency_comparison": latency_comparison,
        "restart_gap_evidence": restart_gap,
        "soak_a": gate(soak_a_failures, soak_a_unknown),
        "soak_b": gate(soak_b_failures, soak_b_unknown),
        "source_hashes": dict(source_hashes),
        "blockers": blockers,
        "safety": dict(SAFETY_FLAGS),
        "write_requested": False,
        "write_performed": False,
    }
    if epoch:
        report["epoch"] = dict(epoch)
    else:
        report["b17_cumulative"] = cumulative
    report["report_sha256"] = digest(report)
    return report


def run_monitor(
    *, project_root: Path, runtime_root: Path, write_report: bool = False,
    epoch_registration: Path | None = None,
) -> dict[str, Any]:
    """Collect canonical read-only evidence; fail closed on any source mismatch."""
    project = project_root.resolve()
    runtime = runtime_root.resolve()
    output = report_output_path(project, DEFAULT_REPORT, epoch_registration)
    now = governance.now_utc()
    try:
        registration = None
        if epoch_registration is None:
            baseline = read_object(runtime / DEFAULT_BASELINE_RELATIVE_PATH)
            validate_historical_baseline(baseline)
        current = governance.audit_snapshots(
            governance.collect_runtime(runtime), git=governance._git(project)
        )
        governance.validate_audit(current, governance.now_utc())
        if epoch_registration is None:
            manifest = read_object(project / governance.MANIFEST_PATH)
            governance.validate_manifest(manifest, current, governance.now_utc())
            boundary = utc(manifest["formal_activation_utc"])
        else:
            registration = load_epoch2_registration(epoch_registration, current)
            identity = epoch2_identity(registration)
            manifest = registration["epoch_manifest"]
            baseline = registration["epoch_baseline"]
            boundary = utc(identity["formal_activation_utc"])
        publisher = governance.CONTAINERS["publisher"]
        raw = governance.container_read(
            publisher, "/app/data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
        )
        records = [parse_json(line) for line in raw.splitlines() if line.strip()]
        population = _operational_population(records, boundary, governance.now_utc())
        pre_activation_event_ids: set[str] | None = None
        if registration is not None:
            predecessor_boundary = utc(registration["predecessor_closeout"]["formal_activation_utc"])
            preceding = _operational_population(records, predecessor_boundary, governance.now_utc())
            pre_activation_event_ids = set(preceding) - set(population)
        evidence = governance.container_json(
            publisher, "/app/data/research/qlib_v3/prospective_evidence/"
            + CANONICAL.epoch_id + "/" + CANONICAL.activation_sha256 + "/evidence.json",
        )
        ledger = governance.container_json(
            publisher, "/app/data/research/canonical_treatment/decision_ledger_v1.json"
        )
        scored, missing = _causal_rows(population, evidence, ledger, boundary, governance.now_utc())
        snapshot = Snapshot(
            generated_at_utc=now.isoformat(),
            formal_activation_utc=boundary.isoformat(),
            causal_manifest_sha256=(manifest["manifest_sha256"] if registration is None
                                    else manifest["epoch_manifest_sha256"]),
            parity_audit_sha256=current["audit_sha256"],
            eligible_event_times={event: record.decision_timestamp for event, record in population.items()},
            scored_rows=scored,
            current_missing_event_ids=tuple(sorted(missing)),
            decision_ledger_row_count=len(ledger["rows"]),
            v3_signal_count=len(evidence["signals"]),
            v3_outcome_count=len(evidence["outcomes"]),
        )
        foundation = (build_foundation_report(baseline, snapshot) if registration is None
                      else build_epoch2_foundation(registration, snapshot))
        db_url = current["fingerprints"]["treatment"]["db_identity"]["url"]
        if not isinstance(db_url, str) or not db_url.startswith("sqlite:////"):
            raise EvidenceError("treatment_db_not_sqlite")
        trades = parse_json(governance.container_read(
            governance.CONTAINERS["treatment"], db_url[len("sqlite:///"):], "sqlite"
        ))
        if not isinstance(trades, list):
            raise EvidenceError("treatment_trades_not_list")
        source_hashes = {
            "baseline_sha256": (baseline["baseline_sha256"] if registration is None
                                else baseline["epoch_baseline_sha256"]),
            "causal_manifest_sha256": (manifest["manifest_sha256"] if registration is None
                                       else manifest["epoch_manifest_sha256"]),
            "operational_ledger_sha256": hashlib.sha256(raw).hexdigest(),
            "v3_store_sha256": evidence["store_sha256"],
            "treatment_ledger_sha256": digest(ledger),
            "runtime_fingerprints_sha256": current["fingerprints_sha256"],
        }
        if registration is not None:
            source_hashes["epoch_registration_sha256"] = registration["registration_sha256"]
        registration_starts = {
            role: manifest["fingerprints"][role]["started_at"]
            for role in ("control", "treatment", "publisher", "monitor")
        }
        report = build_monitor_report(
            foundation=foundation, scored_rows=scored, treatment_rows=ledger["rows"],
            treatment_trades=trades,
            operational_rows={
                event: {"decision_timestamp": record.decision_timestamp.isoformat(),
                        "pair": record.pair, "side": record.side.value}
                for event, record in population.items()
            },
            baseline={**baseline, "causal_runtime_registered_started_at_utc": registration_starts},
            runtime_fingerprints=current["fingerprints"], source_hashes=source_hashes,
            generated_at_utc=now.isoformat(),
            pre_activation_event_ids=pre_activation_event_ids,
        )
        after = governance.audit_snapshots(
            governance.collect_runtime(runtime), git=governance._git(project)
        )
        if registration is None:
            governance.validate_manifest(manifest, after, governance.now_utc())
        else:
            epoch2.validate_current_epoch2_runtime(registration, after)
        if current["fingerprints"] != after["fingerprints"]:
            raise EvidenceError("runtime_changed_during_monitor_read")
    except (EvidenceError, epoch2.CausalEpoch2FoundationError, OSError, ValueError,
            KeyError, TypeError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": now.isoformat(),
            "status": "blocked",
            "decision": "RESEARCH_ONLY_MONITOR",
            "reason": str(exc) if isinstance(exc, (EvidenceError, epoch2.CausalEpoch2FoundationError))
            else "monitor_source_invalid:" + type(exc).__name__,
            "blockers": [str(exc) if isinstance(exc, (EvidenceError, epoch2.CausalEpoch2FoundationError))
                         else "monitor_source_invalid:" + type(exc).__name__],
            "safety": dict(SAFETY_FLAGS),
            "write_performed": False,
        }
    report["write_requested"] = write_report
    if write_report:
        output.parent.mkdir(parents=True, exist_ok=True)
        report["write_performed"] = True
        policy = AtomicWritePolicy.restricted([output.parent], working_directory=project)
    report.pop("report_sha256", None)
    report["report_sha256"] = digest(report)
    if write_report:
        atomic_write_json(output, report, policy=policy, allow_nan=False)
    return report
