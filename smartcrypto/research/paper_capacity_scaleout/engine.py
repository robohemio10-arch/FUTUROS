"""Research-only Branch 5 capacity scaleout simulator.

This engine evaluates marginal capacity scenarios without operational authority.

Critical identity rule:
realized marginal outcomes are accepted only from an explicit outcome source
that carries ``candidate_id`` plus an explicit outcome timestamp and realized
PnL. Branch-4 assignment ledgers are NOT outcome sources and are rejected by
contract.

There is no fuzzy matching, timestamp-nearest linkage, historical backfill,
trade-id-as-candidate-id mapping, runtime capacity change, RiskManager change,
strategy change, model change, or order submission.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import compute_financial_metrics
from smartcrypto.research.paper_edge_foundation.foundation import (
    file_sha256,
    prepare_closed_trades,
    read_authoritative_paper_source,
)

from .contracts import SAFETY_FLAGS, SCHEMA_VERSION, CapacityScaleoutConfig
from .persistence import (
    resolve_report_markdown_path,
    resolve_report_path,
    resolve_research_path,
    write_research_rows,
    write_report,
)


DEFAULT_BRANCH4_REPORT = Path("data/reports/paper_ab_edge_selector_v1.json")
DEFAULT_OPPORTUNITY_REPORT = Path("data/reports/shadow_opportunity_engine_v1.json")
OUTCOME_PNL_FIELDS = (
    "realized_net_pnl_usdt",
    "effective_arm_pnl_usdt",
)


class PaperCapacityScaleoutError(RuntimeError):
    """Controlled Branch-5 domain failure."""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _utc(value: Any) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed) or parsed.tzinfo is None:
        return None
    return parsed.tz_convert("UTC")


def _load_json_object(
    root: Path,
    value: str | Path | None,
    default: Path,
) -> dict[str, Any]:
    selected = Path(value) if value is not None else default
    path = selected if selected.is_absolute() else root / selected
    path = path.resolve()

    if not path.is_file():
        return {
            "__source_status": "SOURCE_MISSING",
            "__source_path": str(path),
            "__source_sha256": None,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "__source_status": "SOURCE_INVALID",
            "__source_path": str(path),
            "__source_sha256": None,
        }

    if not isinstance(payload, dict):
        return {
            "__source_status": "SOURCE_INVALID",
            "__source_path": str(path),
            "__source_sha256": file_sha256(path),
        }

    normalized = dict(payload)
    normalized["__source_status"] = "OK"
    normalized["__source_path"] = str(path)
    normalized["__source_sha256"] = file_sha256(path)
    return normalized


def _read_outcome_rows(
    root: Path,
    value: str | Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read an explicit candidate outcome source.

    Contract:
    - candidate_id
    - outcome_available_at_utc
    - realized_net_pnl_usdt OR effective_arm_pnl_usdt

    No default source is assumed. Assignment ledgers do not satisfy this
    contract and are rejected rather than interpreted as outcomes.
    """

    if value is None:
        return [], {
            "status": "NOT_PROVIDED",
            "reason": "explicit_candidate_outcome_source_required",
            "path": None,
            "sha256": None,
            "row_count": 0,
            "valid_outcome_row_count": 0,
            "invalid_contract_row_count": 0,
        }

    selected = Path(value)
    path = selected if selected.is_absolute() else root / selected
    path = path.resolve()

    if not path.is_file():
        return [], {
            "status": "SOURCE_MISSING",
            "reason": "candidate_outcome_source_missing",
            "path": str(path),
            "sha256": None,
            "row_count": 0,
            "valid_outcome_row_count": 0,
            "invalid_contract_row_count": 0,
        }

    if path.suffix.lower() != ".jsonl":
        return [], {
            "status": "SOURCE_UNSUPPORTED",
            "reason": "candidate_outcome_source_requires_jsonl_v1",
            "path": str(path),
            "sha256": file_sha256(path),
            "row_count": 0,
            "valid_outcome_row_count": 0,
            "invalid_contract_row_count": 0,
        }

    raw_rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("jsonl_row_not_object")
            raw_rows.append(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [], {
            "status": "SOURCE_INVALID",
            "reason": f"{type(exc).__name__}:{exc}",
            "path": str(path),
            "sha256": file_sha256(path),
            "row_count": 0,
            "valid_outcome_row_count": 0,
            "invalid_contract_row_count": 0,
        }

    valid_rows: list[dict[str, Any]] = []
    invalid_contract_count = 0

    for raw in raw_rows:
        candidate_id = str(raw.get("candidate_id") or "").strip()
        available = _utc(raw.get("outcome_available_at_utc"))
        pnl_values = [
            parsed
            for field in OUTCOME_PNL_FIELDS
            if raw.get(field) is not None
            and (parsed := _finite(raw.get(field))) is not None
        ]

        if not candidate_id or available is None or not pnl_values:
            invalid_contract_count += 1
            continue

        if len({round(value, 12) for value in pnl_values}) != 1:
            invalid_contract_count += 1
            continue

        valid_rows.append(dict(raw))

    if raw_rows and not valid_rows:
        status = "SOURCE_CONTRACT_MISMATCH"
        reason = "rows_do_not_satisfy_explicit_candidate_outcome_contract"
    else:
        status = "OK"
        reason = "explicit_candidate_outcome_contract"

    return valid_rows, {
        "status": status,
        "reason": reason,
        "path": str(path),
        "sha256": file_sha256(path),
        "row_count": len(raw_rows),
        "valid_outcome_row_count": len(valid_rows),
        "invalid_contract_row_count": invalid_contract_count,
    }


def _branch4_context(report: Mapping[str, Any]) -> dict[str, Any]:
    source_status = str(report.get("__source_status") or "UNKNOWN")
    software = report.get("software_dod")
    financial = report.get("financial_evidence")
    software_status = (
        str(software.get("status") or "UNKNOWN")
        if isinstance(software, Mapping)
        else "UNKNOWN"
    )
    financial_status = (
        str(financial.get("status") or "UNKNOWN")
        if isinstance(financial, Mapping)
        else "UNKNOWN"
    )

    return {
        "source_status": source_status,
        "source_path": report.get("__source_path"),
        "source_sha256": report.get("__source_sha256"),
        "software_dod_status": software_status,
        "software_dod_pass": (
            source_status == "OK" and software_status == "PASS"
        ),
        "financial_evidence_status": financial_status,
        "decision": str(report.get("decision") or "UNKNOWN"),
        "research_simulation_only": True,
    }


def _opportunity_ledger(
    report: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_status = str(report.get("__source_status") or "UNKNOWN")
    opportunity_cost = report.get("opportunity_cost")

    if source_status != "OK" or not isinstance(opportunity_cost, Mapping):
        return [], {
            "source_status": source_status,
            "source_path": report.get("__source_path"),
            "source_sha256": report.get("__source_sha256"),
            "candidate_ev_coverage_rate": 0.0,
            "ledger_row_count": 0,
        }

    raw_ledger = opportunity_cost.get("ledger")
    rows = (
        [dict(row) for row in raw_ledger if isinstance(row, Mapping)]
        if isinstance(raw_ledger, list)
        else []
    )
    coverage = _finite(opportunity_cost.get("candidate_ev_coverage_rate"))

    return rows, {
        "source_status": source_status,
        "source_path": report.get("__source_path"),
        "source_sha256": report.get("__source_sha256"),
        "candidate_ev_coverage_rate": coverage if coverage is not None else 0.0,
        "ledger_row_count": len(rows),
    }


def _normalize_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for raw in rows:
        candidate_id = str(raw.get("candidate_id") or "").strip()
        observed = _utc(raw.get("observed_at_utc"))
        symbol = str(raw.get("candidate_symbol") or "").strip().upper()
        side = str(raw.get("candidate_side") or "").strip().upper()

        if (
            not candidate_id
            or observed is None
            or not symbol
            or side not in {"LONG", "SHORT"}
        ):
            continue

        normalized.append(
            {
                "candidate_id": candidate_id,
                "observed_at_utc": observed,
                "symbol": symbol,
                "side": side,
                "regime": str(
                    raw.get("candidate_regime")
                    or raw.get("regime")
                    or ""
                ).strip()
                or None,
                "candidate_ev": _finite(raw.get("candidate_ev")),
                "candidate_actionable_shadow": (
                    raw.get("candidate_actionable_shadow") is True
                ),
                "capacity_blocked": raw.get("capacity_blocked") is True,
                "missed_due_to_global_capacity": (
                    raw.get("missed_due_to_global_capacity") is True
                ),
                "missed_due_to_pair_occupancy": (
                    raw.get("missed_due_to_pair_occupancy") is True
                ),
                "capital_hours": _finite(raw.get("capital_hours")) or 0.0,
            }
        )

    return sorted(
        normalized,
        key=lambda row: (
            row["observed_at_utc"],
            -(row["candidate_ev"] or 0.0),
            row["candidate_id"],
        ),
    )


def _admit_c1(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Simulate exactly one extra global slot per exact event timestamp."""

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in rows:
        row = dict(raw)

        if row.get("candidate_actionable_shadow") is not True:
            continue
        if row.get("capacity_blocked") is not True:
            continue
        if row.get("missed_due_to_global_capacity") is not True:
            continue
        if row.get("missed_due_to_pair_occupancy") is True:
            continue

        observed = _utc(row.get("observed_at_utc"))
        if observed is None:
            continue

        buckets[observed.isoformat()].append(row)

    admitted: list[dict[str, Any]] = []

    for key in sorted(buckets):
        ranked = sorted(
            buckets[key],
            key=lambda row: (
                -(row["candidate_ev"] or 0.0),
                row["candidate_id"],
            ),
        )
        admitted.append(ranked[0])

    return admitted


def _admit_c2(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_per_symbol_regime_day: int,
) -> list[dict[str, Any]]:
    """Conservative C2 allocation using positive EV as a filter only."""

    counts: Counter[tuple[str, str, str]] = Counter()
    admitted: list[dict[str, Any]] = []

    for raw in rows:
        row = dict(raw)
        candidate_ev = _finite(row.get("candidate_ev"))
        regime = str(row.get("regime") or "").strip()
        observed = _utc(row.get("observed_at_utc"))

        if (
            candidate_ev is None
            or candidate_ev <= 0
            or not regime
            or observed is None
        ):
            continue

        key = (
            observed.strftime("%Y-%m-%d"),
            str(row["symbol"]),
            regime,
        )

        if counts[key] >= max_per_symbol_regime_day:
            continue

        counts[key] += 1
        admitted.append(row)

    return admitted


def _outcome_index(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    index: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    for raw in rows:
        candidate_id = str(raw.get("candidate_id") or "").strip()
        available = _utc(raw.get("outcome_available_at_utc"))

        pnl = _finite(
            raw.get("realized_net_pnl_usdt")
            if raw.get("realized_net_pnl_usdt") is not None
            else raw.get("effective_arm_pnl_usdt")
        )

        if not candidate_id or available is None or pnl is None:
            continue

        normalized = {
            "candidate_id": candidate_id,
            "realized_net_pnl_usdt": pnl,
            "outcome_available_at_utc": available,
            "trade_id": raw.get("trade_id"),
        }

        prior = index.get(candidate_id)
        if prior is not None and prior != normalized:
            blockers.append(
                f"OUTCOME_DUPLICATE_CONFLICT:{candidate_id}"
            )
            continue

        index[candidate_id] = normalized

    return index, sorted(set(blockers))


def _join_outcomes(
    admitted: Sequence[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
    config: CapacityScaleoutConfig,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for raw in admitted:
        candidate_id = str(raw["candidate_id"])
        outcome = outcomes.get(candidate_id)

        if outcome is None:
            continue

        observed = _utc(raw.get("observed_at_utc"))
        available = _utc(outcome.get("outcome_available_at_utc"))

        if (
            observed is None
            or available is None
            or available < observed
        ):
            continue

        realized = float(outcome["realized_net_pnl_usdt"])
        marginal = (
            realized
            - config.incremental_cost_per_trade_usdt
            - config.latency_penalty_per_trade_usdt
        )

        result.append(
            {
                **dict(raw),
                "outcome_available_at_utc": available,
                "trade_id": outcome.get("trade_id"),
                "realized_net_pnl_usdt": realized,
                "marginal_net_pnl_usdt": marginal,
                "linkage_method": "EXACT_CANDIDATE_ID",
            }
        )

    return result


def _metrics(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    values = np.array(
        [float(row["marginal_net_pnl_usdt"]) for row in rows],
        dtype=float,
    )

    if not len(values):
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "win_rate": None,
            "max_drawdown": None,
            "cvar_95": None,
        }

    raw = compute_financial_metrics(
        pd.DataFrame({"__pnl": values})
    )

    equity = np.concatenate(([0.0], np.cumsum(values)))
    peaks = np.maximum.accumulate(equity)
    losses = -values[values < 0]

    return {
        "trade_count": int(len(values)),
        "net_pnl": float(np.sum(values)),
        "expectancy": float(np.mean(values)),
        "profit_factor": _finite(raw.get("profit_factor")),
        "win_rate": _finite(raw.get("win_rate")),
        "max_drawdown": float(np.max(peaks - equity)),
        "cvar_95": (
            float(np.mean(losses))
            if len(losses)
            else 0.0
        ),
    }


def _bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    config: CapacityScaleoutConfig,
) -> dict[str, Any]:
    values = np.array(
        [float(row["marginal_net_pnl_usdt"]) for row in rows],
        dtype=float,
    )

    if not len(values):
        return {
            "status": "INSUFFICIENT",
            "ci_lower": None,
            "ci_upper": None,
        }

    rng = np.random.default_rng(config.bootstrap_seed)
    means = [
        float(
            np.mean(
                rng.choice(
                    values,
                    size=len(values),
                    replace=True,
                )
            )
        )
        for _ in range(config.bootstrap_iterations)
    ]

    alpha = (1.0 - config.confidence_level) / 2.0

    return {
        "status": "AVAILABLE",
        "ci_lower": float(np.quantile(means, alpha)),
        "ci_upper": float(np.quantile(means, 1.0 - alpha)),
        "iterations": config.bootstrap_iterations,
        "seed": config.bootstrap_seed,
        "confidence_level": config.confidence_level,
    }


def _monte_carlo(
    rows: Sequence[Mapping[str, Any]],
    config: CapacityScaleoutConfig,
) -> dict[str, Any]:
    values = np.array(
        [float(row["marginal_net_pnl_usdt"]) for row in rows],
        dtype=float,
    )

    if not len(values):
        return {
            "status": "INSUFFICIENT",
            "risk_of_ruin": None,
            "max_drawdown_p99": None,
        }

    rng = np.random.default_rng(config.bootstrap_seed + 101)
    ruin_count = 0
    drawdowns: list[float] = []

    for _ in range(config.monte_carlo_iterations):
        path = rng.choice(
            values,
            size=len(values),
            replace=True,
        )
        equity = config.initial_capital_usdt
        peak = equity
        max_drawdown = 0.0
        ruined = False

        for pnl in path:
            equity += float(pnl)
            peak = max(peak, equity)
            max_drawdown = max(
                max_drawdown,
                peak - equity,
            )
            if equity <= config.ruin_floor_usdt:
                ruined = True

        ruin_count += int(ruined)
        drawdowns.append(max_drawdown)

    return {
        "status": "AVAILABLE",
        "iterations": config.monte_carlo_iterations,
        "risk_of_ruin": float(
            ruin_count / config.monte_carlo_iterations
        ),
        "max_drawdown_p99": float(
            np.quantile(drawdowns, 0.99)
        ),
    }


def _stress_c3(
    rows: Sequence[Mapping[str, Any]],
    config: CapacityScaleoutConfig,
) -> list[dict[str, Any]]:
    stressed: list[dict[str, Any]] = []

    for raw in rows:
        pnl = float(raw["marginal_net_pnl_usdt"])

        if pnl < 0:
            pnl *= config.c3_loss_multiplier
        else:
            pnl *= config.c3_win_retention

        pnl -= config.c3_extra_cost_per_trade_usdt

        stressed.append(
            {
                **dict(raw),
                "marginal_net_pnl_usdt": pnl,
                "c3_stressed": True,
            }
        )

    return stressed


def _baseline(
    closed: pd.DataFrame,
) -> dict[str, Any]:
    values = pd.to_numeric(
        closed["close_profit_abs"],
        errors="coerce",
    )

    raw = compute_financial_metrics(
        pd.DataFrame({"__pnl": values})
    )

    array = values.to_numpy(dtype=float)
    equity = (
        np.concatenate(([0.0], np.cumsum(array)))
        if len(array)
        else np.array([0.0])
    )
    peaks = np.maximum.accumulate(equity)

    return {
        "trade_count": int(
            raw.get("trades", len(closed))
        ),
        "net_pnl": float(raw.get("total_pnl") or 0.0),
        "expectancy": _finite(raw.get("expectancy")),
        "profit_factor": _finite(raw.get("profit_factor")),
        "max_drawdown": float(
            np.max(peaks - equity)
        ),
        "pnl_authority": "FREQTRADE_CLOSE_PROFIT_ABS",
        "open_trades_excluded": True,
    }


def evaluate_capacity_scenarios(
    *,
    closed_trades: pd.DataFrame,
    branch4_report: Mapping[str, Any],
    opportunity_report: Mapping[str, Any],
    outcome_rows: Sequence[Mapping[str, Any]],
    config: CapacityScaleoutConfig,
    input_blockers: Sequence[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    branch4 = _branch4_context(branch4_report)
    ledger, opportunity_source = _opportunity_ledger(
        opportunity_report
    )
    candidates = _normalize_candidates(ledger)
    outcomes, outcome_blockers = _outcome_index(
        outcome_rows
    )

    c1_structural = _admit_c1(candidates)
    c2_structural = _admit_c2(
        c1_structural,
        max_per_symbol_regime_day=(
            config.c2_max_recovered_per_symbol_regime_day
        ),
    )

    c1_rows = _join_outcomes(
        c1_structural,
        outcomes,
        config,
    )
    c2_rows = _join_outcomes(
        c2_structural,
        outcomes,
        config,
    )
    c3_rows = _stress_c3(
        c1_rows,
        config,
    )

    baseline = _baseline(closed_trades)
    c1_metrics = _metrics(c1_rows)
    c2_metrics = _metrics(c2_rows)
    c3_metrics = _metrics(c3_rows)

    bootstrap = _bootstrap_ci(
        c1_rows,
        config,
    )
    monte_carlo = _monte_carlo(
        c3_rows,
        config,
    )

    blockers = [
        *input_blockers,
        *outcome_blockers,
    ]

    if not branch4["software_dod_pass"]:
        blockers.append(
            "BRANCH4_RUNTIME_REPORT_SOFTWARE_DOD_NOT_VERIFIED"
        )

    coverage = float(
        opportunity_source["candidate_ev_coverage_rate"]
    )

    if coverage < config.minimum_opportunity_coverage:
        blockers.append(
            "OPPORTUNITY_COVERAGE_BELOW_POLICY"
        )

    if len(c1_rows) < config.minimum_marginal_outcomes:
        blockers.append(
            "MARGINAL_OUTCOME_SAMPLE_INSUFFICIENT"
        )

    if blockers:
        evidence_status = "INSUFFICIENT"
        decision = "AGUARDAR_EVIDENCIA"
    else:
        expectancy = _finite(c1_metrics.get("expectancy"))
        profit_factor = _finite(
            c1_metrics.get("profit_factor")
        )
        ci_lower = _finite(
            bootstrap.get("ci_lower")
        )
        stress_expectancy = _finite(
            c3_metrics.get("expectancy")
        )
        risk_of_ruin = _finite(
            monte_carlo.get("risk_of_ruin")
        )

        baseline_drawdown = float(
            baseline.get("max_drawdown") or 0.0
        )
        marginal_drawdown = float(
            c1_metrics.get("max_drawdown") or 0.0
        )
        allowed_drawdown = (
            baseline_drawdown
            * config.max_marginal_drawdown_ratio_to_baseline
        )

        gates = {
            "marginal_expectancy_positive": (
                expectancy is not None and expectancy > 0
            ),
            "bootstrap_ci_lower_positive": (
                ci_lower is not None and ci_lower > 0
            ),
            "marginal_profit_factor": (
                profit_factor is not None
                and profit_factor
                >= config.minimum_marginal_profit_factor
            ),
            "c3_stress_expectancy_positive": (
                stress_expectancy is not None
                and stress_expectancy > 0
            ),
            "c3_risk_of_ruin": (
                risk_of_ruin is not None
                and risk_of_ruin <= config.risk_of_ruin_cap
            ),
            "marginal_drawdown": (
                marginal_drawdown <= allowed_drawdown
                if baseline_drawdown > 0
                else marginal_drawdown <= 0
            ),
            "c2_has_financial_outcomes": len(c2_rows) > 0,
        }

        positive = all(gates.values())

        evidence_status = (
            "POSITIVE_RESEARCH_ONLY"
            if positive
            else "NEGATIVE"
        )
        decision = (
            "CANDIDATO_PARA_REAVALIACAO_FUTURA"
            if positive
            else "REJEITAR_SCALEOUT"
        )

    status = (
        "ok"
        if branch4["software_dod_pass"]
        else "blocked"
    )

    return (
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "reason": (
                "paper_capacity_scaleout_research_evaluated"
                if status == "ok"
                else "branch5_input_evidence_not_ready"
            ),
            "decision": decision,
            "simulation_mode": "RESEARCH_SIMULATION_ONLY",
            "branch4_context": branch4,
            "opportunity_source": opportunity_source,
            "baseline": baseline,
            "scenarios": {
                "C0": {
                    "status": "BASELINE",
                    "baseline_capacity": config.baseline_capacity,
                    "metrics": baseline,
                },
                "C1": {
                    "status": (
                        "EVALUABLE"
                        if c1_rows
                        else "STRUCTURAL_ONLY"
                    ),
                    "recovered_count": len(c1_structural),
                    "financially_linked_count": len(c1_rows),
                    "metrics": c1_metrics,
                    "bootstrap_expectancy": bootstrap,
                },
                "C2": {
                    "status": (
                        "EVALUABLE"
                        if c2_rows
                        else "STRUCTURAL_ONLY"
                    ),
                    "recovered_count": len(c2_structural),
                    "financially_linked_count": len(c2_rows),
                    "metrics": c2_metrics,
                },
                "C3": {
                    "status": (
                        "EVALUABLE"
                        if c3_rows
                        else "STRUCTURAL_ONLY"
                    ),
                    "recovered_count": len(c1_structural),
                    "financially_linked_count": len(c3_rows),
                    "metrics": c3_metrics,
                    "monte_carlo_marginal_risk": monte_carlo,
                },
                "C4": {
                    "status": "PASS_FAIL_CLOSED",
                    "kill_switch_active": True,
                    "stale_data": True,
                    "recovered_count": 0,
                    "financially_linked_count": 0,
                    "fail_closed": True,
                },
            },
            "capacity_evidence": {
                "status": evidence_status,
                "blockers": sorted(set(blockers)),
                "activation_allowed": False,
            },
            "software_dod": {
                "status": "PASS",
                "exact_candidate_linkage_only": True,
                "assignment_rows_are_not_outcomes": True,
                "candidate_ev_is_not_realized_pnl": True,
                "no_runtime_capacity_change": True,
            },
            "capacity_activation_allowed": False,
            "write_requested": False,
            "write_performed": False,
            "safety": dict(SAFETY_FLAGS),
            **SAFETY_FLAGS,
        },
        c1_rows,
    )


def build_paper_capacity_scaleout_v1(
    *,
    project_root: str | Path,
    paper_db: str | Path,
    baseline_commit: str,
    baseline_capacity: int,
    branch4_report: str | Path | None = None,
    opportunity_report: str | Path | None = None,
    opportunity_outcomes: str | Path | None = None,
    write_report_requested: bool = False,
    write_research_requested: bool = False,
    output_report: str | Path | None = None,
    output_markdown: str | Path | None = None,
    output_research: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()

    config = CapacityScaleoutConfig(
        baseline_commit=baseline_commit,
        baseline_capacity=int(baseline_capacity),
    )

    source = read_authoritative_paper_source(
        paper_db
    )
    closed, counts = prepare_closed_trades(
        source["trades"]
    )

    branch4 = _load_json_object(
        root,
        branch4_report,
        DEFAULT_BRANCH4_REPORT,
    )
    opportunity = _load_json_object(
        root,
        opportunity_report,
        DEFAULT_OPPORTUNITY_REPORT,
    )
    outcome_rows, outcome_source = _read_outcome_rows(
        root,
        opportunity_outcomes,
    )

    input_blockers: list[str] = []

    if str(branch4.get("__source_status")) != "OK":
        input_blockers.append(
            "BRANCH4_REPORT_"
            + str(branch4.get("__source_status"))
        )

    if str(opportunity.get("__source_status")) != "OK":
        input_blockers.append(
            "OPPORTUNITY_REPORT_"
            + str(opportunity.get("__source_status"))
        )

    if outcome_source["status"] != "OK":
        input_blockers.append(
            "OUTCOME_SOURCE_"
            + str(outcome_source["status"])
        )

    report, research_rows = evaluate_capacity_scenarios(
        closed_trades=closed,
        branch4_report=branch4,
        opportunity_report=opportunity,
        outcome_rows=outcome_rows,
        config=config,
        input_blockers=input_blockers,
    )

    report["sources"] = {
        "paper_db": {
            "path": str(source["path"]),
            "sha256": source["sha256_before"],
            "sqlite_integrity_check": (
                source["sqlite_integrity_check"]
            ),
            **counts,
        },
        "branch4_report": {
            "path": branch4.get("__source_path"),
            "sha256": branch4.get("__source_sha256"),
            "status": branch4.get("__source_status"),
        },
        "opportunity_report": {
            "path": opportunity.get("__source_path"),
            "sha256": opportunity.get("__source_sha256"),
            "status": opportunity.get("__source_status"),
        },
        "opportunity_outcomes": outcome_source,
    }

    report_path = resolve_report_path(
        root,
        output_report,
    )
    markdown_path = resolve_report_markdown_path(
        root,
        output_markdown,
    )
    research_path = resolve_research_path(
        root,
        output_research,
    )

    report["outputs"] = {
        "report_json": str(report_path),
        "report_markdown": str(markdown_path),
        "research_jsonl": str(research_path),
    }

    write_performed = False

    if write_research_requested:
        report["research_rows_written"] = (
            write_research_rows(
                root,
                research_path,
                research_rows,
            )
        )
        write_performed = True
    else:
        report["research_rows_written"] = 0

    if write_report_requested:
        report["write_performed"] = True
        write_report(
            root,
            report_path,
            markdown_path,
            report,
        )
        write_performed = True

    report["write_requested"] = bool(
        write_report_requested
        or write_research_requested
    )
    report["write_performed"] = write_performed

    return report
