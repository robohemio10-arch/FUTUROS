"""Prerequisite, testnet, chaos and incident gates for B06."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from .contracts import (
    REQUIRED_CHAOS_SCENARIOS,
    REQUIRED_TESTNET_STAGES,
    gate,
    mapping,
    mapping_list,
)
from .io import finite, finite_or, positive_int


def evaluate_prerequisites(payload: Any) -> dict[str, Any]:
    """Require the already-certified G00 closure before B06 readiness."""

    g00_status = str(mapping(payload).get("g00_status") or "").upper()
    blockers = [] if g00_status == "PASS" else ["g00_status_must_be_pass"]
    passed = not blockers
    return gate(
        passed,
        "g00_pass_confirmed" if passed else "g00_not_confirmed",
        blockers,
        g00_status=g00_status or None,
    )


def evaluate_testnet(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate externally produced exchange-testnet evidence.

    The local isolated harness is a software smoke test and is intentionally not
    sufficient for the final B06 readiness decision.
    """

    runs = mapping_list(mapping(payload).get("runs"))
    testnet_config = mapping(config.get("testnet_e2e"))
    minimum_runs = positive_int(testnet_config.get("minimum_runs"), 3)
    required_stages = tuple(
        str(item)
        for item in testnet_config.get("required_stages") or REQUIRED_TESTNET_STAGES
    )
    accepted_evidence_classes = {
        str(item)
        for item in testnet_config.get("accepted_evidence_classes")
        or ("exchange_testnet",)
    }
    blockers: list[str] = []
    run_ids: list[str] = []
    isolated_harness_run_count = 0

    if len(runs) < minimum_runs:
        blockers.append("insufficient_testnet_run_count")

    for index, run in enumerate(runs):
        run_id = str(run.get("run_id") or "").strip()
        display_id = run_id or f"run-{index}"
        run_ids.append(run_id)

        if not run_id:
            blockers.append(f"{display_id}:run_id_missing")
        environment = str(run.get("environment") or "").lower()
        endpoint_class = str(run.get("endpoint_class") or "").lower()
        evidence_class = str(run.get("evidence_class") or "").lower()
        if evidence_class == "isolated_harness":
            isolated_harness_run_count += 1
        if evidence_class not in accepted_evidence_classes:
            blockers.append(f"{display_id}:evidence_class_not_accepted")
        if environment != "testnet" or endpoint_class != "testnet":
            blockers.append(f"{display_id}:production_endpoint_forbidden")
        if run.get("real_order") is not False:
            blockers.append(f"{display_id}:real_order_must_be_false")
        if run.get("testnet_order_submitted") is not True:
            blockers.append(f"{display_id}:exchange_testnet_order_evidence_required")
        if run.get("active_runtime_touched") is not False:
            blockers.append(f"{display_id}:active_runtime_touched_must_be_false")

        stages = mapping(run.get("stages"))
        blockers.extend(
            f"{display_id}:missing_stage:{stage}"
            for stage in required_stages
            if stages.get(stage) is not True
        )

    nonempty_run_ids = [item for item in run_ids if item]
    if len(nonempty_run_ids) != len(set(nonempty_run_ids)):
        blockers.append("duplicate_testnet_run_ids")

    passed = not blockers
    return gate(
        passed,
        "testnet_e2e_evidence_complete"
        if passed
        else "testnet_e2e_evidence_incomplete_or_invalid",
        blockers,
        run_count=len(runs),
        minimum_runs=minimum_runs,
        required_stages=list(required_stages),
        accepted_evidence_classes=sorted(accepted_evidence_classes),
        isolated_harness_run_count=isolated_harness_run_count,
        executes_testnet_orders=False,
        executes_real_orders=False,
        active_runtime_touched=False,
    )


def evaluate_chaos(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate isolated chaos/recovery results for every mandatory scenario."""

    rows = mapping_list(mapping(payload).get("scenarios"))
    chaos_config = mapping(config.get("chaos"))
    required_scenarios = tuple(
        str(item)
        for item in chaos_config.get("required_scenarios") or REQUIRED_CHAOS_SCENARIOS
    )
    maximum_recovery_seconds = finite_or(
        chaos_config.get("maximum_recovery_seconds"), 300.0
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blockers: list[str] = []

    for row in rows:
        scenario_id = str(row.get("scenario_id") or "").strip()
        grouped[scenario_id].append(row)

    for scenario_id in required_scenarios:
        entries = grouped.get(scenario_id, [])
        if not entries:
            blockers.append(f"{scenario_id}:scenario_missing")
            continue
        if len(entries) != 1:
            blockers.append(f"{scenario_id}:duplicate_scenario_evidence")
            continue

        row = entries[0]
        if str(row.get("status") or "").lower() != "pass":
            blockers.append(f"{scenario_id}:status_must_be_pass")
        if row.get("data_loss") is not False:
            blockers.append(f"{scenario_id}:data_loss_must_be_false")
        if row.get("duplicate_orders") is not False:
            blockers.append(f"{scenario_id}:duplicate_orders_must_be_false")
        if row.get("active_runtime_touched") is not False:
            blockers.append(f"{scenario_id}:active_runtime_touched_must_be_false")

        recovery_seconds = finite(row.get("recovery_seconds"))
        if recovery_seconds is None or recovery_seconds < 0:
            blockers.append(f"{scenario_id}:recovery_seconds_invalid")
        elif recovery_seconds > maximum_recovery_seconds:
            blockers.append(f"{scenario_id}:recovery_seconds_exceeds_limit")

    passed = not blockers
    return gate(
        passed,
        "chaos_recovery_evidence_complete"
        if passed
        else "chaos_recovery_evidence_incomplete_or_invalid",
        blockers,
        scenario_count=len(rows),
        required_scenarios=list(required_scenarios),
        maximum_recovery_seconds=maximum_recovery_seconds,
        active_runtime_touched=False,
        containers_restarted_by_evaluator=False,
    )


def evaluate_incidents(payload: Any) -> dict[str, Any]:
    """Block readiness while any P0/P1 incident is unresolved."""

    incidents = mapping_list(payload)
    unresolved = [
        incident
        for incident in incidents
        if str(incident.get("severity") or "").upper() in {"P0", "P1"}
        and str(incident.get("status") or "").lower() not in {"resolved", "closed"}
    ]
    blockers = [
        "unresolved_"
        f"{str(incident.get('severity') or '').upper()}:"
        f"{str(incident.get('incident_id') or 'unknown')}"
        for incident in unresolved
    ]
    passed = not blockers
    return gate(
        passed,
        "no_unresolved_p0_p1" if passed else "unresolved_p0_p1_present",
        blockers,
        incident_count=len(incidents),
        unresolved_p0_p1_count=len(unresolved),
    )
