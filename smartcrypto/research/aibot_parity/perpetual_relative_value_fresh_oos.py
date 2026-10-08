"""Fully funded long-spot/short-perp research; immutable pre-OOS calibration seal.

Signals use candles closed +60s and only settlements available +300s. Orders are
modeled at the next minute open, with equal base quantity fixed at decision time.
PnL uses actual subsequent prices and funding marks only AFTER selection. Fees
and slippage are positive, frozen research assumptions, not account fee claims.
No borrow, leverage tuning, reverse sleeve, reinvestment, or operational authority.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import numpy as np
import numpy.typing as npt
import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    PITSourceError,
    encode,
    external_root,
    sha256,
    write_once,
)
from smartcrypto.research.aibot_parity.perpetual_relative_value_source import (
    SYMBOLS,
    load_public_history,
    timestamp,
)

SCHEMA = "perpetual_relative_value_fresh_oos_v1"


class Costs(TypedDict):
    spot_fee_bps: float
    perp_fee_bps: float
    slippage_bps_per_leg_per_fill: float


class Rule(TypedDict):
    entry_basis_bps: float
    minimum_expected_net_carry_bps: float
    maximum_holding_hours: float
    convergence_exit_bps: float
    require_expected_carry: NotRequired[bool]


class ResearchPlan(TypedDict):
    schema_version: str
    train_start: str
    train_end: str
    calibration_start: str
    calibration_end: str
    fresh_oos_start: str
    fresh_oos_end: str
    grid: dict[str, list[float]]
    costs: Costs
    spot_signal_notional_usdt: float
    hedge: str
    margin: str
    maximum_positions_per_symbol: int
    availability: str
    execution: str
    expected_carry_model: str
    calibration_objective: str
    benchmark: Rule
    cost_stress: list[float]


PLAN: ResearchPlan = {
    "schema_version": SCHEMA,
    "train_start": "2026-08-01T00:00:00Z",
    "train_end": "2026-08-19T00:00:00Z",
    "calibration_start": "2026-08-19T00:00:00Z",
    "calibration_end": "2026-08-28T16:52:50Z",
    "fresh_oos_start": "2026-08-29T00:00:00Z",
    "fresh_oos_end": "2026-10-01T00:00:00Z",
    "grid": {
        "entry_basis_bps": [5.0, 10.0, 20.0],
        "minimum_expected_net_carry_bps": [0.5, 2.0],
        "maximum_holding_hours": [24.0, 72.0],
        "convergence_exit_bps": [0.0, 2.0],
    },
    "costs": {"spot_fee_bps": 10.0, "perp_fee_bps": 5.0, "slippage_bps_per_leg_per_fill": 1.0},
    "spot_signal_notional_usdt": 1000.0,
    "hedge": "equal_base_quantity_1_to_1_fixed_at_decision_time",
    "margin": "fully_collateralized_perp_1x_no_borrow",
    "maximum_positions_per_symbol": 1,
    "availability": "closed_candle_plus_60s_settlement_plus_300s",
    "execution": "next_1m_open_both_legs_nonzero_costs",
    "expected_carry_model": "last_known_settled_rate_per_hour_times_horizon_plus_basis_convergence_less_roundtrip_costs",
    "calibration_objective": "maximum_calibration_net_pnl_stable_grid_order_tiebreak",
    "benchmark": {
        "entry_basis_bps": 10.0,
        "minimum_expected_net_carry_bps": 0.0,
        "maximum_holding_hours": 72.0,
        "convergence_exit_bps": 2.0,
        "require_expected_carry": False,
    },
    "cost_stress": [1.0, 1.5, 2.0],
}
SAFETY = {
    "research_only": True,
    "operational_authority": False,
    "paper_enabled": False,
    "live_enabled": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_strategy": False,
    "promotion_allowed": False,
}


def validate_plan(plan: Mapping[str, Any]) -> None:
    if encode(plan) != encode(PLAN):
        raise PITSourceError("predefined_research_plan_changed")
    if timestamp(plan["calibration_end"]) >= timestamp(plan["fresh_oos_start"]):
        raise PITSourceError("calibration_fresh_overlap")
    if (timestamp(plan["fresh_oos_end"]) - timestamp(plan["fresh_oos_start"])).days < 30:
        raise PITSourceError("BLOCKED_INSUFFICIENT_FRESH_OOS")
    if any(float(v) <= 0 for v in plan["costs"].values()):
        raise PITSourceError("zero_cost_forbidden")


def replay(
    prices: Mapping[str, pd.DataFrame],
    funding: Mapping[str, pd.DataFrame],
    rule: Mapping[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame = prices[symbol]
        times = frame.time_ms.to_numpy(dtype="int64")
        spot_open, perp_open = frame.spot_open.to_numpy(), frame.perp_open.to_numpy()
        spot_close, perp_close = frame.spot_close.to_numpy(), frame.perp_close.to_numpy()
        settlements = funding[symbol]
        ft = settlements.event_ms.to_numpy(dtype="int64")
        fa = settlements.available_ms.to_numpy(dtype="int64")
        rates, marks, intervals = (
            settlements[c].to_numpy(dtype=float) for c in ("rate", "mark", "interval_hours")
        )
        position: dict[str, Any] | None = None
        start_ms, end_ms = start.value // 1_000_000, end.floor("min").value // 1_000_000
        for i in range(2, len(times) - 1):
            decision, execution = int(times[i]), int(times[i + 1])
            if decision < start_ms or execution >= end_ms:
                continue
            basis = (perp_close[i - 2] / spot_close[i - 2] - 1) * 10_000
            k = int(np.searchsorted(fa, decision, side="right")) - 1
            known = k >= 1 and decision - int(fa[k]) <= 29_100_000 and np.isfinite(intervals[k])
            if position is not None:
                held_hours = (execution - position["entry_ms"]) / 3_600_000
                final = execution >= end_ms - 60_000
                if (
                    basis <= rule["convergence_exit_bps"]
                    or held_hours >= rule["maximum_holding_hours"]
                    or final
                ):
                    q = position["quantity"]
                    funding_mask = (ft > position["entry_ms"]) & (ft < execution)
                    position.update(
                        {
                            "exit_ms": execution,
                            "exit_spot": float(spot_open[i + 1]),
                            "exit_perp": float(perp_open[i + 1]),
                            "basis_at_exit_bps": float(
                                (perp_open[i + 1] / spot_open[i + 1] - 1) * 10_000
                            ),
                            "exit_reason": "window_end"
                            if final
                            else "convergence"
                            if basis <= rule["convergence_exit_bps"]
                            else "horizon",
                            "spot_leg_pnl": float(q * (spot_open[i + 1] - position["entry_spot"])),
                            "perp_leg_pnl": float(q * (position["entry_perp"] - perp_open[i + 1])),
                            "funding_pnl": float(
                                q * np.sum(rates[funding_mask] * marks[funding_mask])
                            ),
                            "funding_events": [
                                {"event_ms": int(t), "pnl": float(q * rate * mark)}
                                for t, rate, mark in zip(
                                    ft[funding_mask],
                                    rates[funding_mask],
                                    marks[funding_mask],
                                    strict=True,
                                )
                            ],
                        }
                    )
                    trades.append(position)
                    position = None
                continue
            if (
                execution >= end_ms - 60_000
                or not known
                or basis < rule["entry_basis_bps"]
                or rates[k] <= 0
            ):
                continue
            costs = PLAN["costs"]
            roundtrip = (
                2 * (costs["spot_fee_bps"] + costs["perp_fee_bps"])
                + 4 * costs["slippage_bps_per_leg_per_fill"]
            )
            expected_net = (
                basis
                - rule["convergence_exit_bps"]
                + rates[k] * 10_000 * rule["maximum_holding_hours"] / intervals[k]
                - roundtrip
            )
            if (
                rule.get("require_expected_carry", True)
                and expected_net < rule["minimum_expected_net_carry_bps"]
            ):
                continue
            # Size is decided using the already available close, not the future fill.
            q = PLAN["spot_signal_notional_usdt"] / spot_close[i - 2]
            position = {
                "symbol": symbol,
                "decision_ms": decision,
                "entry_ms": execution,
                "quantity": float(q),
                "entry_spot": float(spot_open[i + 1]),
                "entry_perp": float(perp_open[i + 1]),
                "basis_at_entry_bps": float((perp_open[i + 1] / spot_open[i + 1] - 1) * 10_000),
                "signal_basis_bps": float(basis),
                "expected_net_carry_bps": float(expected_net),
                "last_known_funding_event_ms": int(ft[k]),
                "last_known_funding_available_ms": int(fa[k]),
                "signal_candle_available_ms": int(times[i - 2] + 120_000),
                "outcomes_used_for_selection": False,
            }
        if position is not None:
            raise PITSourceError("open_position_at_evaluation_boundary")
    return sorted(trades, key=lambda r: (r["exit_ms"], r["symbol"], r["entry_ms"]))


def costs_and_metrics(
    trades: list[dict[str, Any]],
    prices: Mapping[str, pd.DataFrame],
    *,
    multiplier: float = 1.0,
) -> dict[str, Any]:
    if multiplier not in PLAN["cost_stress"]:
        raise PITSourceError("cost_scenario_not_predefined")
    records = []
    c = PLAN["costs"]
    equity_times = np.unique(
        np.concatenate([p.time_ms.to_numpy(dtype="int64") for p in prices.values()])
    )
    equity: npt.NDArray[np.float64] = np.zeros(len(equity_times), dtype=float)
    for original in trades:
        r = dict(original)
        q = r["quantity"]
        entry_gross = q * (r["entry_spot"] + r["entry_perp"])
        exit_gross = q * (r["exit_spot"] + r["exit_perp"])
        r["entry_fees"] = (
            multiplier
            * q
            * (r["entry_spot"] * c["spot_fee_bps"] + r["entry_perp"] * c["perp_fee_bps"])
            / 10_000
        )
        r["exit_fees"] = (
            multiplier
            * q
            * (r["exit_spot"] * c["spot_fee_bps"] + r["exit_perp"] * c["perp_fee_bps"])
            / 10_000
        )
        r["slippage_cost"] = (
            multiplier * (entry_gross + exit_gross) * c["slippage_bps_per_leg_per_fill"] / 10_000
        )
        entry_slippage = multiplier * entry_gross * c["slippage_bps_per_leg_per_fill"] / 10_000
        r["gross_notional"] = entry_gross
        r["capital_committed"] = entry_gross + r["entry_fees"] + entry_slippage
        r["holding_hours"] = (r["exit_ms"] - r["entry_ms"]) / 3_600_000
        r["capital_hours"] = r["capital_committed"] * r["holding_hours"]
        r["net_pnl"] = (
            r["spot_leg_pnl"]
            + r["perp_leg_pnl"]
            + r["funding_pnl"]
            - r["entry_fees"]
            - r["exit_fees"]
            - r["slippage_cost"]
        )
        if (
            r["signal_candle_available_ms"] > r["decision_ms"]
            or r["last_known_funding_available_ms"] > r["decision_ms"]
            or r["entry_ms"] <= r["decision_ms"]
        ):
            raise PITSourceError("future_join_detected")
        p = prices[r["symbol"]]
        mask = p.time_ms.between(r["entry_ms"], r["exit_ms"] - 1)
        at = p.time_ms.loc[mask].to_numpy(dtype="int64")
        mtm = (
            q
            * (
                p.spot_open.loc[mask].to_numpy()
                - r["entry_spot"]
                + r["entry_perp"]
                - p.perp_open.loc[mask].to_numpy()
            )
            - r["entry_fees"]
            - entry_slippage
        )
        for settlement in r["funding_events"]:
            mtm[at >= settlement["event_ms"]] += settlement["pnl"]
        equity[np.searchsorted(equity_times, at)] += mtm
        equity[equity_times >= r["exit_ms"]] += r["net_pnl"]
        records.append(r)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        components = {
            name: float(sum(r[name] for r in rows))
            for name in (
                "spot_leg_pnl",
                "perp_leg_pnl",
                "funding_pnl",
                "entry_fees",
                "exit_fees",
                "slippage_cost",
                "gross_notional",
                "capital_committed",
                "capital_hours",
            )
        }
        pnls = [r["net_pnl"] for r in rows]
        net, gains, losses = sum(pnls), sum(max(v, 0) for v in pnls), -sum(min(v, 0) for v in pnls)
        return {
            "trade_count": len(rows),
            "net_pnl_usdt": float(net),
            **components,
            "total_fees": components["entry_fees"] + components["exit_fees"],
            "profit_factor": gains / losses if losses > 0 else None,
            "expectancy": net / len(rows) if rows else None,
            "net_pnl_per_capital_hour": net / components["capital_hours"]
            if components["capital_hours"] > 0
            else None,
            "return_on_committed_capital": net / components["capital_committed"]
            if components["capital_committed"] > 0
            else None,
            "average_holding_time_hours": float(np.mean([r["holding_hours"] for r in rows]))
            if rows
            else None,
            "basis_at_entry_bps": float(np.mean([r["basis_at_entry_bps"] for r in rows]))
            if rows
            else None,
            "basis_at_exit_bps": float(np.mean([r["basis_at_exit_bps"] for r in rows]))
            if rows
            else None,
        }

    report = summarize(records)
    running_peak = np.maximum.accumulate(np.maximum(equity, 0))
    report["max_drawdown"] = float(np.max(running_peak - equity)) if len(equity) else 0.0
    report["drawdown_semantics"] = "1m_joint_leg_mark_to_market_including_actual_funding_and_costs"
    report["capital_semantics"] = (
        "sum_trade_initial_spot_and_1x_perp_collateral_plus_entry_costs_not_portfolio_NAV_return"
    )
    report["by_symbol"] = {s: summarize([r for r in records if r["symbol"] == s]) for s in SYMBOLS}
    start, end = (
        timestamp(PLAN["fresh_oos_start"]).value // 1_000_000,
        timestamp(PLAN["fresh_oos_end"]).value // 1_000_000,
    )
    cuts = [start + (end - start) * i // 3 for i in range(4)]
    report["blocks"] = [
        {
            "start": pd.Timestamp(cuts[i], unit="ms", tz="UTC").isoformat(),
            "end": pd.Timestamp(cuts[i + 1], unit="ms", tz="UTC").isoformat(),
            "attribution": "whole_trade_by_exit_time",
            **summarize([r for r in records if cuts[i] <= r["exit_ms"] < cuts[i + 1]]),
        }
        for i in range(3)
    ]
    report["positive_blocks"] = sum(b["net_pnl_usdt"] > 0 for b in report["blocks"])
    report["trades"] = records
    report["future_join_count"] = 0
    report["anti_leakage_status"] = "PASS"
    return report


def calibrate(
    prices: Mapping[str, pd.DataFrame], funding: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    start, end = timestamp(PLAN["calibration_start"]), timestamp(PLAN["calibration_end"])
    if any(int(p.time_ms.max()) > end.value // 1_000_000 for p in prices.values()) or any(
        int(f.event_ms.max()) >= end.value // 1_000_000 for f in funding.values()
    ):
        raise PITSourceError("fresh_outcome_in_calibration_input")
    names = list(PLAN["grid"])
    trials = []
    for values in itertools.product(*(PLAN["grid"][n] for n in names)):
        rule = dict(zip(names, values, strict=True))
        trades = replay(prices, funding, rule, start, end)
        # Ex-post calibration labels are permitted only on the old period.
        m = costs_and_metrics(trades, prices)
        trials.append(
            {"rule": rule, "net_pnl_usdt": m["net_pnl_usdt"], "trade_count": m["trade_count"]}
        )
    selected = max(range(len(trials)), key=lambda i: (trials[i]["net_pnl_usdt"], -i))
    return {
        "selected_rule": trials[selected]["rule"],
        "trials": trials,
        "selected_grid_index": selected,
        "fresh_oos_used_for_calibration": False,
    }


def implementation_hashes() -> dict[str, str]:
    from smartcrypto.research.aibot_parity import (
        market_intelligence_pit_source_foundation,
        perpetual_relative_value_source,
    )

    paths = [
        Path(__file__),
        Path(perpetual_relative_value_source.__file__),
        Path(market_intelligence_pit_source_foundation.__file__),
    ]
    return {path.name: sha256(path.read_bytes()) for path in paths}


def run_relative_value(
    *,
    project_root: Path,
    artifact_root: Path,
    phase: str,
    allow_download: bool = False,
    write_evidence: bool = False,
) -> dict[str, Any]:
    common = {
        "schema_version": SCHEMA,
        **SAFETY,
        "write_performed": False,
        "economic_candidate": False,
    }
    try:
        root = external_root(artifact_root, project_root)
        validate_plan(PLAN)
        cache = root / "cache"
        freeze_path, result_path = root / "frozen_config_v1.json", root / "fresh_oos_result_v1.json"
        if phase == "calibrate":
            if freeze_path.exists():
                raise PITSourceError("configuration_already_frozen_no_recalibration")
            if write_evidence:
                write_once(root / "predefined_plan_v1.json", encode(PLAN))
            start, end = (
                timestamp(PLAN["train_start"]) - pd.Timedelta(days=1),
                timestamp(PLAN["calibration_end"]),
            )
            # Kline loading ends at a minute boundary and never includes fresh data.
            prices, funding, coverage = load_public_history(
                cache, start, end.floor("min"), allow_download=allow_download
            )
            calibration = calibrate(prices, funding)
            content = {
                "plan": PLAN,
                "calibration": calibration,
                "calibration_coverage": coverage,
                "implementation_hashes": implementation_hashes(),
            }
            sealed = {
                **content,
                "config_hash": sha256(encode(content)),
                "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            if write_evidence:
                write_once(freeze_path, encode(sealed))
            return {
                **common,
                "status": "ok",
                "decision": "CONFIG_FROZEN" if write_evidence else "READY_TO_FREEZE",
                "write_performed": write_evidence,
                "config_hash": sealed["config_hash"],
                "freeze_path": str(freeze_path) if write_evidence else None,
                "calibration": calibration,
                "coverage": coverage,
            }
        if phase != "evaluate" or not freeze_path.is_file() or freeze_path.is_symlink():
            raise PITSourceError("valid_frozen_config_required_before_fresh_download")
        seal = json.loads(freeze_path.read_bytes())
        validate_plan(seal["plan"])
        content = {
            k: seal[k]
            for k in ("plan", "calibration", "calibration_coverage", "implementation_hashes")
        }
        if seal["config_hash"] != sha256(encode(content)):
            raise PITSourceError("frozen_config_hash_mismatch")
        if seal["implementation_hashes"] != implementation_hashes():
            raise PITSourceError("implementation_changed_after_freeze")
        if result_path.is_file():
            recorded = json.loads(result_path.read_bytes())
            if recorded["config_hash"] != seal["config_hash"] or sha256(
                result_path.read_bytes()
            ) != (root / "fresh_oos_result_v1.sha256").read_text(encoding="ascii"):
                raise PITSourceError("recorded_result_hash_mismatch")
            return {**recorded, "write_performed": False, "already_evaluated": True}
        if not write_evidence:
            raise PITSourceError("one_shot_evaluation_requires_external_evidence_seal")
        started = root / "evaluation_started_v1.json"
        if started.exists():
            raise PITSourceError(
                "evaluation_previously_started_without_result_manual_review_required"
            )
        write_once(
            started,
            encode(
                {
                    "config_hash": seal["config_hash"],
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
        start, end = timestamp(PLAN["fresh_oos_start"]), timestamp(PLAN["fresh_oos_end"])
        if end > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2):
            raise PITSourceError("BLOCKED_INSUFFICIENT_FRESH_OOS")
        prices, funding, coverage = load_public_history(
            cache, start - pd.Timedelta(days=1), end, allow_download=allow_download
        )
        fresh_rows = int((end - start).total_seconds() / 60)
        coverage["fresh_complete_days"] = (end - start).days
        coverage["fresh_rows_per_symbol"] = fresh_rows
        strategies = {}
        for name, rule in (
            ("RELATIVE_VALUE", seal["calibration"]["selected_rule"]),
            ("SIMPLE_CASH_AND_CARRY", PLAN["benchmark"]),
        ):
            trades = replay(prices, funding, rule, start, end)
            strategies[name] = {
                label: costs_and_metrics(trades, prices, multiplier=value)
                for label, value in zip(
                    ("BASE_COST", "1.5X_COST", "2.0X_COST"), PLAN["cost_stress"], strict=True
                )
            }
        base = strategies["RELATIVE_VALUE"]["BASE_COST"]
        candidate = (
            base["net_pnl_usdt"] > 0
            and base["expectancy"] is not None
            and base["expectancy"] > 0
            and base["positive_blocks"] >= 2
        )
        report = {
            **common,
            "status": "ok",
            "decision": "ECONOMIC_CANDIDATE" if candidate else "RELATIVE_VALUE_NO_FRESH_OOS_EDGE",
            "economic_candidate": candidate,
            "write_performed": True,
            "config_hash": seal["config_hash"],
            "frozen_at_utc": seal["frozen_at_utc"],
            "evaluation_started_at_utc": json.loads(started.read_bytes())["started_at_utc"],
            "plan": PLAN,
            "frozen_rule": seal["calibration"]["selected_rule"],
            "coverage": coverage,
            "strategies": strategies,
            "NO_TRADE": {"net_pnl_usdt": 0.0},
            "survives_1_5x_cost": strategies["RELATIVE_VALUE"]["1.5X_COST"]["net_pnl_usdt"] > 0,
            "future_join_count": 0,
            "anti_leakage_status": "PASS",
            "fresh_oos_recalibration_performed": False,
        }
        raw = encode(report)
        write_once(result_path, raw)
        write_once(root / "fresh_oos_result_v1.sha256", sha256(raw).encode("ascii"))
        return report
    except (PITSourceError, OSError, ValueError, KeyError, TypeError) as exc:
        return {
            **common,
            "status": "blocked",
            "reason": str(exc),
            "decision": "RELATIVE_VALUE_RESEARCH_BLOCKED",
            "anti_leakage_status": "BLOCKED",
        }
