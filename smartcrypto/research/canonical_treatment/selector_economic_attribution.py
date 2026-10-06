"""Read-only economic attribution for exact Paper-B selector opportunities."""

from __future__ import annotations

import hashlib
import json
import math
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
    Snapshot,
    build_report as build_foundation_report,
    validate_baseline,
)
from smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor import (
    build_epoch2_foundation,
    build_monitor_report,
    load_epoch2_registration,
    report_output_path,
    validate_historical_baseline,
)
from smartcrypto.runtime.integrity_traceability_v2 import AtomicWritePolicy, atomic_write_json

SCHEMA_VERSION = "paper_b_selector_economic_attribution_v1"
DEFAULT_REPORT = Path("data/reports/canonical_treatment/paper_b_selector_economic_attribution_v1.json")
SAFETY = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "changes_model": False,
    "changes_threshold": False,
    "changes_strategy": False,
    "changes_risk": False,
    "sends_orders": False,
    "replay": False,
    "backfill": False,
}


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bucket(value: object) -> str:
    number = _finite(value)
    if number is None:
        return "UNAVAILABLE"
    return "NEGATIVE" if number < 0 else "POSITIVE" if number > 0 else "ZERO"


def _trades_by_event(
    trades: Sequence[Mapping[str, Any]],
    operational: Mapping[str, Mapping[str, Any]],
    scored: Mapping[str, Mapping[str, Any]],
    *,
    treatment: bool,
    as_of: datetime,
) -> dict[str, list[float] | None]:
    grouped: dict[str, list[float] | None] = {}
    seen: set[int] = set()
    for row in trades:
        event = _decision_id(row.get("enter_tag"))
        if event not in operational:
            continue
        trade_id = row.get("id")
        if type(trade_id) is not int or trade_id <= 0 or trade_id in seen:
            raise EvidenceError("attribution_trade_id_invalid_or_duplicate")
        seen.add(trade_id)
        op = operational[event]
        opened = _time(row.get("open_date"))
        closed = _time(row.get("close_date"))
        if (opened is None or opened < utc(op["decision_timestamp"]) or opened > as_of
                or row.get("pair") != op["pair"] or row.get("is_short") not in (0, 1)
                or bool(row["is_short"]) != (op["side"] == "short")):
            raise EvidenceError("attribution_trade_causal_identity_invalid")
        if treatment:
            selection = scored.get(event)
            if (selection is None or selection.get("selected") is not True
                    or not max(utc(selection["first_observed_at_utc"]),
                               utc(selection["signal_timestamp_utc"])) <= opened
                    < utc(selection["valid_until"])):
                raise EvidenceError("attribution_treatment_not_selected_ex_ante")
        elif event in scored:
            selection = scored[event]
            if not max(utc(selection["first_observed_at_utc"]),
                       utc(selection["signal_timestamp_utc"])) <= opened < utc(selection["valid_until"]):
                grouped[event] = None
                continue
        if row.get("is_open") == 1 and closed is None:
            grouped[event] = None
            continue
        pnl = _finite(row.get("close_profit_abs"))
        if (row.get("is_open") != 0 or closed is None or not opened < closed <= as_of
                or pnl is None):
            raise EvidenceError("attribution_trade_close_or_pnl_invalid")
        if event not in grouped:
            grouped[event] = []
        values = grouped[event]
        if values is not None:
            values.append(pnl)
    return grouped


def _profit_factor(values: Sequence[float]) -> float | None:
    profit = sum(value for value in values if value > 0)
    loss = -sum(value for value in values if value < 0)
    return profit / loss if loss > 0 else None


def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classified = [row for row in rows if row["classification"] is not None]
    counts = {name: sum(row["classification"] == name for row in classified) for name in (
        "GOOD_ABSTENTION", "FALSE_ABSTENTION", "GOOD_SELECTION", "BAD_SELECTION"
    )}
    avoided = sum(-float(row["control_pnl"]) for row in classified
                  if row["classification"] == "GOOD_ABSTENTION")
    missed = sum(float(row["control_pnl"]) for row in classified
                 if row["classification"] == "FALSE_ABSTENTION")
    selected_profit = sum(float(row["treatment_pnl"]) for row in classified
                          if row["classification"] == "GOOD_SELECTION")
    selected_loss = sum(float(row["treatment_pnl"]) for row in classified
                        if row["classification"] == "BAD_SELECTION")
    paired = [row for row in classified if row["control_pnl"] is not None]
    control_values = [float(row["control_pnl"]) for row in paired]
    treatment_values = [float(row["treatment_pnl"] or 0.0) for row in paired]
    control_net = sum(control_values) if paired else None
    treatment_net = sum(treatment_values) if paired else None
    delta_net = sum(treatment_values) - sum(control_values) if paired else None
    return {
        "eligible_count": len(rows),
        "resolved_count": len(classified),
        "allow_count": sum(row["decision"] == "ALLOW" for row in rows),
        "abstain_count": sum(row["decision"] == "ABSTAIN" for row in rows),
        "selected_closed_count": sum(row["decision"] == "ALLOW" and row["treatment_pnl"] is not None
                                     and row["valid_until_expired"] for row in rows),
        **{name.lower() + "_count": value for name, value in counts.items()},
        "avoided_loss_usdt": avoided,
        "missed_profit_usdt": missed,
        "selected_profit_usdt": selected_profit,
        "selected_loss_usdt": selected_loss,
        "net_selector_value_usdt": avoided - missed + selected_profit + selected_loss,
        "paired_resolved_count": len(paired),
        "control_net_pnl": control_net,
        "treatment_net_pnl": treatment_net,
        "delta_net_pnl": delta_net,
        "control_expectancy": sum(control_values) / len(paired) if paired else None,
        "treatment_expectancy": sum(treatment_values) / len(paired) if paired else None,
        "delta_expectancy": delta_net / len(paired) if delta_net is not None else None,
        "control_profit_factor": _profit_factor(control_values),
        "treatment_profit_factor": _profit_factor(treatment_values),
    }


def build_attribution_report(
    *,
    foundation: Mapping[str, Any],
    coverage: Mapping[str, Any],
    operational: Mapping[str, Mapping[str, Any]],
    scored: Mapping[str, Mapping[str, Any]],
    control_trades: Sequence[Mapping[str, Any]],
    treatment_trades: Sequence[Mapping[str, Any]],
    generated_at_utc: str,
) -> dict[str, Any]:
    """Attribute only closed, exactly linked outcomes in the selected cohort."""
    if foundation.get("status") != "ok" or coverage.get("status") != "ok":
        raise EvidenceError("upstream_foundation_or_coverage_blocked")
    pre = foundation["historical_debt"]
    post = foundation["post_fix_counters"]
    # The immutable baseline supplies pre-fix IDs; the foundation report
    # intentionally carries only historical debt counts and missing IDs.
    pre_ids = set(foundation["pre_fix_eligible_event_ids"])
    pre_scored_at_fix = set(pre["scored_event_ids_at_fix"])
    post_ids = set(post["eligible_event_ids"])
    eligible = pre_ids | post_ids
    epoch = foundation.get("epoch")
    if epoch:
        if (epoch.get("epoch_id") != epoch2.EPOCH2_ID or pre_ids
                or coverage.get("epoch") != epoch
                or foundation.get("baseline_sha256") != epoch.get("epoch_baseline_sha256")
                or any(utc(row["decision_timestamp"]) < utc(epoch["formal_activation_utc"])
                       for row in operational.values())):
            raise EvidenceError("attribution_epoch2_identity_or_boundary_invalid")
    elif coverage.get("epoch") is not None:
        raise EvidenceError("attribution_coverage_epoch_mismatch")
    if pre_ids & post_ids or eligible != set(operational):
        raise EvidenceError("attribution_population_partition_invalid")
    if (len(pre_ids) != pre["eligible"] or len(pre_scored_at_fix) != pre["scored_at_fix"]
            or pre_scored_at_fix | set(pre["missing_event_ids"]) != pre_ids
            or pre_scored_at_fix & set(pre["missing_event_ids"])):
        raise EvidenceError("attribution_historical_baseline_mismatch")
    if set(scored) - eligible:
        raise EvidenceError("attribution_scored_outside_cohort")
    if set(coverage["postfix_population"]["eligible_event_ids"]) != post_ids:
        raise EvidenceError("attribution_coverage_cohort_mismatch")
    if set(coverage["postfix_population"]["scored_event_ids"]) != (set(scored) & post_ids):
        raise EvidenceError("attribution_coverage_scored_mismatch")
    now = utc(generated_at_utc)
    control = _trades_by_event(control_trades, operational, scored, treatment=False, as_of=now)
    treatment = _trades_by_event(treatment_trades, operational, scored, treatment=True, as_of=now)
    rows: list[dict[str, Any]] = []
    for event in sorted(eligible, key=lambda item: (utc(operational[item]["decision_timestamp"]), item)):
        op = operational[event]
        selection = scored.get(event)
        row_epoch = "EPOCH_2" if epoch else "PRE_FIX" if event in pre_ids else "POST_FIX"
        if row_epoch == "PRE_FIX" and event not in pre_scored_at_fix:
            selection = None
        decision = None if selection is None else "ALLOW" if selection["selected"] is True else "ABSTAIN"
        control_values = control.get(event)
        treatment_values = treatment.get(event)
        control_pnl = sum(control_values) if isinstance(control_values, list) and control_values else None
        treatment_pnl = (sum(treatment_values) if isinstance(treatment_values, list)
                         and treatment_values else None)
        classification = None
        if selection is not None and utc(selection["valid_until"]) <= now:
            if decision == "ABSTAIN" and control_pnl is not None:
                classification = "GOOD_ABSTENTION" if control_pnl < 0 else (
                    "FALSE_ABSTENTION" if control_pnl > 0 else None
                )
            elif decision == "ALLOW" and treatment_pnl is not None:
                classification = "GOOD_SELECTION" if treatment_pnl > 0 else (
                    "BAD_SELECTION" if treatment_pnl < 0 else None
                )
        score = selection.get("qlib_score") if selection else None
        margin = selection.get("score_margin") if selection else None
        rows.append({
            "decision_event_id": event,
            "epoch": row_epoch,
            "symbol": op["symbol"],
            "side": op["side"],
            "direction": op["side"].upper(),
            "decision": decision,
            "classification": classification,
            "valid_until_expired": selection is not None and utc(selection["valid_until"]) <= now,
            "score": _finite(score),
            "score_bucket": _bucket(score),
            "score_margin": _finite(margin),
            "score_margin_status": "AVAILABLE" if _finite(margin) is not None else "UNAVAILABLE",
            "score_margin_bucket": _bucket(margin),
            "control_pnl": control_pnl,
            "treatment_pnl": treatment_pnl,
        })
    groups: dict[str, dict[str, Any]] = {}
    for dimension in ("epoch", "symbol", "side", "direction", "decision", "score_bucket",
                      "score_margin_bucket"):
        values = sorted({str(row[dimension] or "UNAVAILABLE") for row in rows})
        groups[dimension] = {
            value: _summarize([row for row in rows if str(row[dimension] or "UNAVAILABLE") == value])
            for value in values
        }
    summary = _summarize(rows)
    closed = summary["selected_closed_count"]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "status": "ok",
        "reason": "closed_exact_causal_outcomes_only",
        "baseline_sha256": foundation["baseline_sha256"],
        "coverage_report_sha256": coverage["report_sha256"],
        "coverage_funnel": coverage["causal_funnel"],
        "summary": summary,
        "by": groups,
        "score_margin_status": "AVAILABLE" if any(row["score_margin_status"] == "AVAILABLE" for row in rows)
        else "UNAVAILABLE",
        "short_attribution_status": "AVAILABLE" if any(row["side"] == "short" and
                                                      row["classification"] is not None for row in rows)
        else "PENDING_CLOSED_OUTCOME",
        "checkpoints": {f"checkpoint_{n}_selected_closed": "PASS" if closed >= n else "PENDING_SAMPLE"
                        for n in (10, 25, 50)},
        "rows": rows,
        "safety": dict(SAFETY),
        "write_performed": False,
    }
    if epoch:
        report["epoch"] = dict(epoch)
    report["report_sha256"] = digest(report)
    return report


def run_attribution(
    *, project_root: Path, runtime_root: Path, write_report: bool = False,
    epoch_registration: Path | None = None,
) -> dict[str, Any]:
    project, runtime = project_root.resolve(), runtime_root.resolve()
    now = governance.now_utc()
    try:
        registration = None
        if epoch_registration is None:
            baseline = read_object(runtime / DEFAULT_BASELINE_RELATIVE_PATH)
            validate_historical_baseline(baseline)
            validate_baseline(baseline)
        current = governance.audit_snapshots(governance.collect_runtime(runtime), git=governance._git(project))
        governance.validate_audit(current, governance.now_utc())
        if epoch_registration is None:
            manifest = read_object(project / governance.MANIFEST_PATH)
            governance.validate_manifest(manifest, current, governance.now_utc())
            boundary = utc(manifest["formal_activation_utc"])
        else:
            registration = load_epoch2_registration(epoch_registration, current)
            manifest = registration["epoch_manifest"]
            baseline = registration["epoch_baseline"]
            boundary = utc(manifest["formal_activation_utc"])
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
            + CANONICAL.epoch_id + "/" + CANONICAL.activation_sha256 + "/evidence.json"
        )
        ledger = governance.container_json(
            publisher, "/app/data/research/canonical_treatment/decision_ledger_v1.json"
        )
        scored, missing = _causal_rows(population, evidence, ledger, boundary, governance.now_utc())
        snapshot = Snapshot(
            generated_at_utc=now.isoformat(), formal_activation_utc=boundary.isoformat(),
            causal_manifest_sha256=(manifest["manifest_sha256"] if registration is None
                                    else manifest["epoch_manifest_sha256"]),
            parity_audit_sha256=current["audit_sha256"],
            eligible_event_times={event: record.decision_timestamp for event, record in population.items()},
            scored_rows=scored, current_missing_event_ids=tuple(sorted(missing)),
            decision_ledger_row_count=len(ledger["rows"]), v3_signal_count=len(evidence["signals"]),
            v3_outcome_count=len(evidence["outcomes"]),
        )
        foundation = (build_foundation_report(baseline, snapshot) if registration is None
                      else build_epoch2_foundation(registration, snapshot))
        db_paths = {}
        trades = {}
        for role in ("control", "treatment"):
            url = current["fingerprints"][role]["db_identity"]["url"]
            if not isinstance(url, str) or not url.startswith("sqlite:////"):
                raise EvidenceError("attribution_db_not_sqlite:" + role)
            db_paths[role] = url[len("sqlite:///"):]
            trades[role] = parse_json(governance.container_read(
                governance.CONTAINERS[role], db_paths[role], "sqlite"
            ))
            if not isinstance(trades[role], list):
                raise EvidenceError("attribution_trade_rows_invalid:" + role)
        operational = {
            event: {"decision_timestamp": record.decision_timestamp.isoformat(), "pair": record.pair,
                    "symbol": record.symbol, "side": record.side.value}
            for event, record in population.items()
        }
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
        coverage = build_monitor_report(
            foundation=foundation, scored_rows=scored, treatment_rows=ledger["rows"],
            treatment_trades=trades["treatment"], operational_rows=operational,
            baseline={**baseline, "causal_runtime_registered_started_at_utc": {
                role: manifest["fingerprints"][role]["started_at"]
                for role in ("control", "treatment", "publisher", "monitor")}},
            runtime_fingerprints=current["fingerprints"], source_hashes=source_hashes,
            generated_at_utc=now.isoformat(),
            pre_activation_event_ids=pre_activation_event_ids,
        )
        report = build_attribution_report(
            foundation={**foundation,
                        "pre_fix_eligible_event_ids": (
                            baseline["baseline_population"]["eligible_event_ids"]
                            if registration is None else []),
                        "historical_debt": {**foundation["historical_debt"],
                                            "scored_event_ids_at_fix": (
                                                baseline["baseline_population"]["scored_event_ids"]
                                                if registration is None else [])}},
            coverage=coverage, operational=operational, scored=scored,
            control_trades=trades["control"], treatment_trades=trades["treatment"],
            generated_at_utc=now.isoformat(),
        )
        after = governance.audit_snapshots(governance.collect_runtime(runtime), git=governance._git(project))
        if registration is None:
            governance.validate_manifest(manifest, after, governance.now_utc())
        else:
            epoch2.validate_current_epoch2_runtime(registration, after)
        if current["fingerprints"] != after["fingerprints"]:
            raise EvidenceError("runtime_changed_during_attribution_read")
    except (EvidenceError, epoch2.CausalEpoch2FoundationError, OSError, ValueError,
            KeyError, TypeError, json.JSONDecodeError) as exc:
        reason = (str(exc) if isinstance(exc, (EvidenceError, epoch2.CausalEpoch2FoundationError))
                  else "attribution_source_invalid:" + type(exc).__name__)
        report = {"schema_version": SCHEMA_VERSION, "generated_at_utc": now.isoformat(),
                  "status": "blocked", "reason": reason, "summary": None,
                  "safety": dict(SAFETY), "write_performed": False}
    report["write_requested"] = write_report
    report.pop("report_sha256", None)
    report["report_sha256"] = digest(report)
    if write_report:
        output = report_output_path(project, DEFAULT_REPORT, epoch_registration)
        output.parent.mkdir(parents=True, exist_ok=True)
        report["write_performed"] = True
        report["report_sha256"] = digest({k: v for k, v in report.items() if k != "report_sha256"})
        atomic_write_json(output, report, allow_nan=False,
                          policy=AtomicWritePolicy.restricted([output.parent], working_directory=project))
    return report
