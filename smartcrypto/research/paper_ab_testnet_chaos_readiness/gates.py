"""Testnet, chaos, capacity and incident gates for B06."""
from __future__ import annotations
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any
from .contracts import REQUIRED_CHAOS_SCENARIOS, REQUIRED_TESTNET_STAGES, gate, mapping, mapping_list
from .io import finite, finite_or, positive_int, rounded

def evaluate_prerequisites(payload: Any) -> dict[str, Any]:
    status = str(mapping(payload).get("g00_status") or "").upper()
    blockers = [] if status == "PASS" else ["g00_status_must_be_pass"]
    return gate(not blockers, "g00_pass_confirmed" if not blockers else "g00_not_confirmed", blockers,
                g00_status=status or None)

def evaluate_testnet(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    runs, cfg = mapping_list(mapping(payload).get("runs")), mapping(config.get("testnet_e2e"))
    minimum = positive_int(cfg.get("minimum_runs"), 3)
    required = tuple(map(str, cfg.get("required_stages") or REQUIRED_TESTNET_STAGES))
    blockers: list[str] = []; ids: list[str] = []
    if len(runs) < minimum: blockers.append("insufficient_testnet_run_count")
    for index, run in enumerate(runs):
        run_id = str(run.get("run_id") or f"run-{index}"); ids.append(run_id)
        if str(run.get("environment") or "").lower() != "testnet" or str(run.get("endpoint_class") or "").lower() != "testnet":
            blockers.append(f"{run_id}:production_endpoint_forbidden")
        if run.get("real_order") is not False: blockers.append(f"{run_id}:real_order_must_be_false")
        if run.get("active_runtime_touched") is not False: blockers.append(f"{run_id}:active_runtime_touched_must_be_false")
        stages = mapping(run.get("stages"))
        blockers += [f"{run_id}:missing_stage:{stage}" for stage in required if stages.get(stage) is not True]
    if len(ids) != len(set(ids)): blockers.append("duplicate_testnet_run_ids")
    return gate(not blockers, "testnet_e2e_evidence_complete" if not blockers else "testnet_e2e_evidence_incomplete_or_invalid", blockers,
                run_count=len(runs), minimum_runs=minimum, required_stages=list(required),
                executes_testnet_orders=False, executes_real_orders=False,
                active_runtime_touched=False)

def evaluate_chaos(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    rows, cfg = mapping_list(mapping(payload).get("scenarios")), mapping(config.get("chaos"))
    required = tuple(map(str, cfg.get("required_scenarios") or REQUIRED_CHAOS_SCENARIOS))
    limit = finite_or(cfg.get("maximum_recovery_seconds"), 300.0)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list); blockers: list[str] = []
    for row in rows: grouped[str(row.get("scenario_id") or "")].append(row)
    for scenario in required:
        entries = grouped.get(scenario, [])
        if not entries: blockers.append(f"{scenario}:scenario_missing"); continue
        if len(entries) != 1: blockers.append(f"{scenario}:duplicate_scenario_evidence"); continue
        row = entries[0]
        if str(row.get("status") or "").lower() != "pass": blockers.append(f"{scenario}:status_must_be_pass")
        if row.get("data_loss") is not False: blockers.append(f"{scenario}:data_loss_must_be_false")
        if row.get("duplicate_orders") is not False: blockers.append(f"{scenario}:duplicate_orders_must_be_false")
        if row.get("active_runtime_touched") is not False: blockers.append(f"{scenario}:active_runtime_touched_must_be_false")
        recovery = finite(row.get("recovery_seconds"))
        if recovery is None or recovery < 0: blockers.append(f"{scenario}:recovery_seconds_invalid")
        elif recovery > limit: blockers.append(f"{scenario}:recovery_seconds_exceeds_limit")
    return gate(not blockers, "chaos_recovery_evidence_complete" if not blockers else "chaos_recovery_evidence_incomplete_or_invalid", blockers,
                scenario_count=len(rows), required_scenarios=list(required),
                maximum_recovery_seconds=limit, active_runtime_touched=False,
                containers_restarted_by_evaluator=False)

def evaluate_capacity(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    rows, cfg = mapping_list(mapping(payload).get("observations")), mapping(config.get("capacity"))
    symbols = tuple(str(item).upper() for item in cfg.get("required_symbols") or ("BTCUSDT", "ETHUSDT"))
    minimum = positive_int(cfg.get("minimum_observations_per_symbol"), 3)
    max_cost = finite_or(cfg.get("maximum_total_execution_cost_bps"), 50.0)
    max_participation = finite_or(cfg.get("maximum_participation_ratio"), .05)
    max_leverage = finite_or(cfg.get("maximum_leverage"), 3.0)
    min_buffer = finite_or(cfg.get("minimum_liquidation_buffer_pct"), 15.0)
    blockers: list[str] = []; ids: list[str] = []; counts: Counter[str] = Counter(); depths: dict[str, list[float]] = defaultdict(list)
    fields = ("notional", "depth_usdt", "leverage", "participation_ratio", "spread_bps", "slippage_bps", "market_impact_bps", "liquidation_buffer_pct")
    for index, row in enumerate(rows):
        observation_id = str(row.get("observation_id") or ""); symbol = str(row.get("symbol") or "").upper(); prefix = observation_id or f"observation-{index}"; ids.append(observation_id)
        values = [finite(row.get(field)) for field in fields]
        if not observation_id: blockers.append(f"{prefix}:observation_id_missing")
        if symbol not in symbols: blockers.append(f"{prefix}:symbol_not_required")
        if any(value is None for value in values): blockers.append(f"{prefix}:numeric_fields_invalid"); continue
        notional, depth, leverage, participation, spread, slippage, impact, buffer = values
        assert None not in values
        if symbol not in symbols: continue
        counts[symbol] += 1; depths[symbol].append(float(depth))
        if float(notional) <= 0 or float(depth) <= 0: blockers.append(f"{prefix}:notional_and_depth_must_be_positive")
        if float(participation) > max_participation: blockers.append(f"{prefix}:participation_ratio_exceeds_limit")
        if float(leverage) > max_leverage: blockers.append(f"{prefix}:leverage_exceeds_limit")
        if float(buffer) < min_buffer: blockers.append(f"{prefix}:liquidation_buffer_below_limit")
        if float(spread) + float(slippage) + float(impact) > max_cost: blockers.append(f"{prefix}:total_execution_cost_exceeds_limit")
    if len(ids) != len(set(ids)): blockers.append("duplicate_observation_ids")
    blockers += [f"{symbol}:insufficient_capacity_observations" for symbol in symbols if counts[symbol] < minimum]
    safe = {symbol: rounded(min(depths[symbol]) * max_participation) for symbol in symbols if depths[symbol]}
    return gate(not blockers, "capacity_evidence_complete" if not blockers else "capacity_evidence_incomplete_or_invalid", blockers,
                required_symbols=list(symbols), minimum_observations_per_symbol=minimum,
                observation_count_by_symbol=dict(counts), safe_notional_by_symbol=safe,
                recommendations_are_advisory=True, risk_configuration_changed=False)

def evaluate_incidents(payload: Any) -> dict[str, Any]:
    rows = mapping_list(payload)
    unresolved = [row for row in rows if str(row.get("severity") or "").upper() in {"P0", "P1"}
                  and str(row.get("status") or "").lower() not in {"resolved", "closed"}]
    blockers = [f"unresolved_{str(row.get('severity') or '').upper()}:{str(row.get('incident_id') or 'unknown')}" for row in unresolved]
    return gate(not blockers, "no_unresolved_p0_p1" if not blockers else "unresolved_p0_p1_present", blockers,
                incident_count=len(rows), unresolved_p0_p1_count=len(unresolved))
