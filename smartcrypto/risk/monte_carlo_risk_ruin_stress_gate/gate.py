"""Research-only Monte Carlo risk-of-ruin stress gate.

This module consumes existing evidence in read-only mode and estimates risk of
ruin under deterministic Monte Carlo stress scenarios. It has no operational
authority and never writes runtime, SQLite, parquet, model, registry, Freqtrade,
or RiskManager artifacts.

Boundary contract
-----------------
This module is a pure risk-domain calculator. It may read existing evidence and
build in-memory reports, but persistence must be delegated to a CLI or an ops
authority outside ``smartcrypto/risk``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "monte_carlo_risk_ruin_stress_gate_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_TARGET_STORE = Path("data/reports/financial_label_target_store_v1.json")
DEFAULT_DRIFT_MONITOR = Path("data/reports/ai_qlib_drift_regime_monitor_v1.json")
DEFAULT_PAPER_AUTOTRAIN = Path("data/reports/paper_autotrain_feedback_loop_v1.json")
DEFAULT_COST_GATE = Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json")

SCENARIOS = (
    "baseline",
    "fee_slippage_stress",
    "loss_cluster_stress",
    "fat_tail_stress",
    "low_liquidity_stress",
    "combined_adverse_stress",
)

GateDecision = Literal["PASS", "WARNING", "BLOCKED"]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 1337
    simulation_count: int = 1000
    sample_size: int | None = None
    initial_capital: float = 100.0
    capital_floor: float = 70.0
    ruin_floor: float = 50.0
    cost_per_trade: float = 0.02
    risk_of_ruin_block_threshold: float = 0.05
    risk_of_ruin_warning_threshold: float = 0.02
    max_drawdown_p99_block_threshold: float = 0.35
    max_drawdown_p99_warning_threshold: float = 0.25
    capital_breach_block_threshold: float = 0.05
    capital_breach_warning_threshold: float = 0.02

    def as_report_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "simulation_count": self.simulation_count,
            "sample_size": self.sample_size,
            "initial_capital": self.initial_capital,
            "capital_floor": self.capital_floor,
            "ruin_floor": self.ruin_floor,
            "cost_per_trade": self.cost_per_trade,
            "risk_of_ruin_block_threshold": self.risk_of_ruin_block_threshold,
            "risk_of_ruin_warning_threshold": self.risk_of_ruin_warning_threshold,
            "max_drawdown_p99_block_threshold": self.max_drawdown_p99_block_threshold,
            "max_drawdown_p99_warning_threshold": self.max_drawdown_p99_warning_threshold,
            "capital_breach_block_threshold": self.capital_breach_block_threshold,
            "capital_breach_warning_threshold": self.capital_breach_warning_threshold,
        }


def build_monte_carlo_risk_ruin_stress_gate_v1(
    *,
    project_root: str | Path,
    write: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    seed: int = 1337,
    simulation_count: int = 1000,
    sample_size: int | None = None,
    initial_capital: float = 100.0,
    capital_floor: float = 70.0,
    ruin_floor: float = 50.0,
    cost_per_trade: float = 0.02,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the Monte Carlo risk-of-ruin stress gate report in memory only.

    ``write`` is accepted only as metadata compatibility with the CLI. This
    risk-domain module never persists JSON/Markdown. Persistence is handled by
    ``scripts/build_monte_carlo_risk_ruin_stress_gate_v1.py``.
    """

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    config = SimulationConfig(
        seed=int(seed),
        simulation_count=int(simulation_count),
        sample_size=sample_size,
        initial_capital=float(initial_capital),
        capital_floor=float(capital_floor),
        ruin_floor=float(ruin_floor),
        cost_per_trade=float(cost_per_trade),
    )

    report_json = resolve(root, report_json_path, Path(report_json_path)) if report_json_path else None
    report_md = resolve(root, report_markdown_path, Path(report_markdown_path)) if report_markdown_path else None

    sources = load_sources(root)
    payloads = {source.source_id: source.payload for source in sources if source.payload}
    returns = extract_returns(payloads.get("target_store", {}))
    safety = safety_flags()

    if not returns:
        return blocked_no_returns_report(
            root=root,
            generated_at=generated_at,
            sources=sources,
            config=config,
            report_json=report_json,
            report_md=report_md,
            safety=safety,
            write_requested=write,
        )

    scenario_results = [
        run_scenario(scenario=scenario, returns=returns, config=config)
        for scenario in SCENARIOS
    ]
    aggregate_decision = worst_decision(result["gate_decision"] for result in scenario_results)

    blockers = sorted(
        {
            f"{result['scenario']}:{reason}"
            for result in scenario_results
            if result["gate_decision"] == "BLOCKED"
            for reason in result["gate_reasons"]
        }
    )
    warnings = sorted(
        {
            f"{result['scenario']}:{reason}"
            for result in scenario_results
            if result["gate_decision"] == "WARNING"
            for reason in result["gate_reasons"]
        }
    )

    status = (
        "blocked"
        if aggregate_decision == "BLOCKED"
        else "warning"
        if aggregate_decision == "WARNING"
        else "ok"
    )
    reason = (
        "monte_carlo_risk_ruin_stress_gate_blocked"
        if status == "blocked"
        else "monte_carlo_risk_ruin_stress_gate_warning"
        if status == "warning"
        else "monte_carlo_risk_ruin_stress_gate_passed_research_only"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "gate_decision": aggregate_decision,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "returns_source": "financial_label_target_store_v1.target_records",
        "valid_return_count": len(returns),
        "simulation_config": config.as_report_dict(),
        "stress_scenarios": scenario_results,
        "worst_scenario": select_worst_scenario(scenario_results),
        "blockers": blockers,
        "warnings": warnings,
        "lineage_hashes": build_lineage_hashes(payloads),
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": {
            "json": str(report_json) if report_json is not None else None,
            "markdown": str(report_md) if report_md is not None else None,
        },
        **safety,
        "safety_flags": safety,
    }


def blocked_no_returns_report(
    *,
    root: Path,
    generated_at: str,
    sources: Sequence[SourceRecord],
    config: SimulationConfig,
    report_json: Path | None,
    report_md: Path | None,
    safety: Mapping[str, bool],
    write_requested: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": "no_valid_returns_source",
        "gate_decision": "BLOCKED",
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "returns_source": None,
        "valid_return_count": 0,
        "simulation_config": config.as_report_dict(),
        "stress_scenarios": [],
        "worst_scenario": None,
        "blockers": ["no_valid_returns_source"],
        "warnings": [],
        "lineage_hashes": {},
        "write_requested": bool(write_requested),
        "write_performed": False,
        "output_paths": {
            "json": str(report_json) if report_json is not None else None,
            "markdown": str(report_md) if report_md is not None else None,
        },
        **safety,
        "safety_flags": dict(safety),
    }


def load_sources(project_root: Path) -> list[SourceRecord]:
    specs = (
        ("target_store", DEFAULT_TARGET_STORE, True),
        ("drift_monitor", DEFAULT_DRIFT_MONITOR, False),
        ("paper_autotrain_feedback_loop", DEFAULT_PAPER_AUTOTRAIN, False),
        ("event_driven_execution_cost_gate", DEFAULT_COST_GATE, False),
    )
    records: list[SourceRecord] = []

    for source_id, relative_path, required in specs:
        path = project_root / relative_path
        exists = path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None

        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                load_error = f"invalid_json:{exc.__class__.__name__}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"

        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )

    return records


def extract_returns(target_store: Mapping[str, Any]) -> list[float]:
    rows = list_of_mappings(target_store.get("target_records"))
    values: list[float] = []

    for row in rows:
        value = first_float(
            row.get("target_expected_value_component"),
            row.get("target_net_pnl"),
            row.get("net_pnl"),
            row.get("pnl"),
        )
        if value is not None:
            values.append(value)

    return values


def run_scenario(*, scenario: str, returns: Sequence[float], config: SimulationConfig) -> dict[str, Any]:
    scenario_returns = apply_stress_scenario(returns, scenario, config)
    path_metrics = run_monte_carlo_paths(scenario_returns, config)
    decision, reasons = decide_scenario(path_metrics, config)

    return {
        "scenario": scenario,
        "gate_decision": decision,
        "gate_reasons": reasons,
        **path_metrics,
    }


def apply_stress_scenario(returns: Sequence[float], scenario: str, config: SimulationConfig) -> list[float]:
    if scenario == "baseline":
        return [float(value) for value in returns]

    if scenario == "fee_slippage_stress":
        return [float(value) - config.cost_per_trade for value in returns]

    if scenario == "loss_cluster_stress":
        return sorted((float(value) for value in returns), key=lambda item: (item >= 0, item))

    if scenario == "fat_tail_stress":
        return [float(value) * 2.0 if value < 0 else float(value) * 0.8 for value in returns]

    if scenario == "low_liquidity_stress":
        return [
            (float(value) * 1.25 if value < 0 else float(value) * 0.75) - (config.cost_per_trade * 1.5)
            for value in returns
        ]

    if scenario == "combined_adverse_stress":
        stressed = [float(value) * 2.25 if value < 0 else float(value) * 0.65 for value in returns]
        return sorted(
            (value - (config.cost_per_trade * 2.0) for value in stressed),
            key=lambda item: (item >= 0, item),
        )

    return [float(value) for value in returns]


def run_monte_carlo_paths(returns: Sequence[float], config: SimulationConfig) -> dict[str, Any]:
    sample_size = config.sample_size or len(returns)
    terminal_equities: list[float] = []
    max_drawdowns: list[float] = []
    loss_streaks: list[int] = []
    floor_breaches = 0
    ruin_breaches = 0

    rng = random.Random(config.seed)

    for _ in range(config.simulation_count):
        sampled = [returns[rng.randrange(len(returns))] for _ in range(sample_size)]
        equity = config.initial_capital
        peak = config.initial_capital
        max_drawdown = 0.0
        current_loss_streak = 0
        max_loss_streak = 0
        breached_floor = False
        breached_ruin = False

        for result in sampled:
            equity += result
            peak = max(peak, equity)

            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

            if result < 0:
                current_loss_streak += 1
                max_loss_streak = max(max_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0

            if equity <= config.capital_floor:
                breached_floor = True

            if equity <= config.ruin_floor:
                breached_ruin = True

        terminal_equities.append(equity)
        max_drawdowns.append(max_drawdown)
        loss_streaks.append(max_loss_streak)
        floor_breaches += int(breached_floor)
        ruin_breaches += int(breached_ruin)

    terminal_losses = [config.initial_capital - equity for equity in terminal_equities]

    return {
        "risk_of_ruin": round(ruin_breaches / config.simulation_count, 10),
        "max_drawdown_p95": round(percentile(max_drawdowns, 0.95), 10),
        "max_drawdown_p99": round(percentile(max_drawdowns, 0.99), 10),
        "cvar_95": round(cvar(terminal_losses, 0.95), 10),
        "cvar_99": round(cvar(terminal_losses, 0.99), 10),
        "loss_streak_p95": int(round(percentile(loss_streaks, 0.95))),
        "loss_streak_p99": int(round(percentile(loss_streaks, 0.99))),
        "capital_floor_breach_probability": round(floor_breaches / config.simulation_count, 10),
        "expected_terminal_equity": round(sum(terminal_equities) / len(terminal_equities), 10),
        "terminal_equity_p05": round(percentile(terminal_equities, 0.05), 10),
        "terminal_equity_p50": round(percentile(terminal_equities, 0.50), 10),
        "terminal_equity_p95": round(percentile(terminal_equities, 0.95), 10),
    }


def decide_scenario(metrics: Mapping[str, Any], config: SimulationConfig) -> tuple[GateDecision, list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    if to_float(metrics.get("risk_of_ruin")) > config.risk_of_ruin_block_threshold:
        blockers.append("risk_of_ruin_exceeds_threshold")
    elif to_float(metrics.get("risk_of_ruin")) > config.risk_of_ruin_warning_threshold:
        warnings.append("risk_of_ruin_warning_threshold_exceeded")

    if to_float(metrics.get("max_drawdown_p99")) > config.max_drawdown_p99_block_threshold:
        blockers.append("max_drawdown_p99_exceeds_threshold")
    elif to_float(metrics.get("max_drawdown_p99")) > config.max_drawdown_p99_warning_threshold:
        warnings.append("max_drawdown_p99_warning_threshold_exceeded")

    if to_float(metrics.get("capital_floor_breach_probability")) > config.capital_breach_block_threshold:
        blockers.append("capital_floor_breach_probability_exceeds_threshold")
    elif to_float(metrics.get("capital_floor_breach_probability")) > config.capital_breach_warning_threshold:
        warnings.append("capital_floor_breach_probability_warning_threshold_exceeded")

    if blockers:
        return "BLOCKED", blockers

    if warnings:
        return "WARNING", warnings

    return "PASS", []


def select_worst_scenario(results: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None

    decision_rank = {"PASS": 0, "WARNING": 1, "BLOCKED": 2}

    return dict(
        max(
            results,
            key=lambda item: (
                decision_rank.get(str(item.get("gate_decision")), -1),
                to_float(item.get("risk_of_ruin")),
                to_float(item.get("max_drawdown_p99")),
                to_float(item.get("capital_floor_breach_probability")),
            ),
        )
    )


def worst_decision(decisions: Sequence[str] | Any) -> GateDecision:
    values = list(decisions)

    if "BLOCKED" in values:
        return "BLOCKED"

    if "WARNING" in values:
        return "WARNING"

    return "PASS"


def percentile(values: Sequence[float] | Sequence[int], quantile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(float(value) for value in values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower

    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def cvar(losses: Sequence[float], confidence: float) -> float:
    if not losses:
        return 0.0

    threshold = percentile(losses, confidence)
    tail = [loss for loss in losses if loss >= threshold]

    return sum(tail) / len(tail) if tail else threshold


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}

    for payload in payloads.values():
        for key in (
            "dataset_hash",
            "feature_contract_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
        ):
            if payload.get(key):
                output[key] = payload[key]

        nested = payload.get("lineage_hashes")
        if isinstance(nested, dict):
            output.update({str(key): value for key, value in nested.items() if value})

    return output


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Monte Carlo Risk Ruin Stress Gate V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Gate decision: `{report.get('gate_decision')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Valid returns: `{report.get('valid_return_count')}`",
            f"- Release allowed: `{report.get('release_allowed')}`",
            "",
            "## Stress Scenarios",
            "",
            *markdown_scenarios(report.get("stress_scenarios", [])),
            "",
            "## Safety Invariants",
            "",
            "- `operational_authority=false`",
            "- `can_change_risk_limits=false`",
            "- `can_stop_bot=false`",
            "- `can_send_orders=false`",
            "- `can_promote_model=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "- `updates_risk_manager=false`",
            "- `writes_runtime=false`",
            "- `writes_sqlite=false`",
            "- `writes_parquet=false`",
            "",
            "This report is research evidence only. It does not alter risk limits, runtime, models, registry, or orders.",
            "",
        ]
    )


def markdown_scenarios(rows: Any) -> list[str]:
    scenarios = list_of_mappings(rows)

    if not scenarios:
        return ["- No valid scenario results."]

    return [
        (
            f"- `{row.get('scenario')}`: decision=`{row.get('gate_decision')}`, "
            f"risk_of_ruin=`{row.get('risk_of_ruin')}`, "
            f"max_drawdown_p99=`{row.get('max_drawdown_p99')}`, "
            f"terminal_p05=`{row.get('terminal_equity_p05')}`"
        )
        for row in scenarios
    ]


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "release_allowed": False,
        "can_change_risk_limits": False,
        "can_stop_bot": False,
        "can_send_orders": False,
        "can_promote_model": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "changes_risk": False,
        "updates_risk_manager": False,
        "changes_model": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def first_float(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue

    return None


def to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, Mapping)]


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass

    return value