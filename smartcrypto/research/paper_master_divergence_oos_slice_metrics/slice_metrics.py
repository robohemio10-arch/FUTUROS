"""OOS slice metrics for Paper/Master divergence research.

This module is deliberately research-only. It computes auditable slice metrics
for the hypotheses H1/H2/H6 when rows are supplied by tests or by an explicit
input JSON. In the default CLI path no runtime rows are loaded and the report
remains blocked while preserving the required schema, safety flags, and OOS
metric contract.

No code in this module can apply a rule, update Freqtrade, update RiskManager,
update Qlib runtime, update AI Shadow runtime, promote a model, or submit
orders.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_master_divergence_oos_slice_metrics_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "MANTER_EM_RESEARCH"

DEFAULT_PAPER_KPIS: dict[str, Any] = {
    "trade_count": 239,
    "net_pnl": -21.35477552,
    "profit_factor": 0.803331,
    "win_rate": 0.405858,
    "gross_profit": 87.22777285,
    "gross_loss": -108.58254837,
    "fees": 11.89221848,
    "avg_duration_hours": 3.4234,
    "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
}

DEFAULT_MASTER_KPIS: dict[str, Any] = {
    "trade_count": 243,
    "net_pnl": 143.166332,
    "profit_factor": 2.072573,
    "win_rate": 0.707819,
    "max_drawdown": -9.378203,
    "mean_trade_roi": 0.001181,
}

DEFAULT_DIVERGENCE_METRICS: dict[str, Any] = {
    "paper_minus_master_trade_count": -4,
    "paper_minus_master_net_pnl": -164.52110752,
    "paper_minus_master_profit_factor": -1.269242,
    "paper_minus_master_win_rate_points": -30.1961,
    "paper_replicates_master_edge": False,
}

CANONICAL_CLUSTER_EVIDENCE: dict[str, Any] = {
    "roi_net_pnl": 87.22777285,
    "stop_loss_net_pnl": -108.58254837,
    "remove_stop_loss_under_30m_delta": 34.9161,
    "fast_stop_under_30m": "critical",
    "eth_long_stop_loss_cluster": "critical",
    "candidate_shadow_rule": (
        "lb_10m_ret_close <= -0.0038501215827868 "
        "AND lb_30m_ret_close <= -0.0060685748963285"
    ),
    "candidate_shadow_rule_precision": 0.65625,
    "candidate_shadow_rule_recall": 0.41176,
    "candidate_shadow_rule_simulated_removed_pnl_delta": 8.9745,
}

HYPOTHESIS_SCOPE = ["H1", "H2", "H6"]
SLICE_DIMENSIONS = [
    "day",
    "symbol",
    "side",
    "exit_reason",
    "duration_bucket",
    "covered_vs_uncovered",
]

MINIMUM_METRICS = [
    "trade_count",
    "net_pnl",
    "profit_factor",
    "win_rate",
    "max_drawdown",
    "winner_retention_rate",
    "winner_pnl_removed",
    "loser_pnl_removed",
    "false_positive_count",
    "false_negative_count",
    "precision",
    "recall",
    "coverage_ratio",
    "simulated_removed_pnl_delta",
]

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "registers_candidate_rules": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "runs_training": False,
    "executes_scheduler": False,
    "executes_orchestrator": False,
    "executes_stage_builders": False,
}

FORBIDDEN_ACTIONS = [
    "aplicar regra no Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar ou promover candidate rule",
    "promover modelo",
    "executar treino operacional",
    "habilitar live ou canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade por padrão",
]

NEXT_RESEARCH_GATES = [
    "inserir fonte real version-safe de trades paper/master sem data runtime versionado",
    "computar OOS por day/symbol/side/exit_reason/duration/covered-vs-uncovered",
    "medir false positive/false negative e winner retention por hipótese",
    "bloquear regra se remover ROI winners ou concentrar efeito em único dia",
    "somente criar registry shadow bloqueado se OOS passar",
]


@dataclass(frozen=True)
class TradeObservation:
    """Normalized research-only trade observation."""

    trade_id: str
    day: str
    symbol: str
    side: str
    exit_reason: str
    duration_minutes: float
    net_pnl: float
    covered_feature_subset: bool
    candidate_rule_triggered: bool
    source_split: str = "oos"

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0.0

    @property
    def is_loser(self) -> bool:
        return self.net_pnl < 0.0

    @property
    def duration_bucket(self) -> str:
        return duration_bucket(self.duration_minutes)

    @property
    def covered_vs_uncovered(self) -> str:
        return "covered" if self.covered_feature_subset else "uncovered"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "covered"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def _as_text(value: Any, *, default: str = "unknown") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else default


def duration_bucket(duration_minutes: float) -> str:
    """Return the canonical duration bucket for a trade duration in minutes."""
    minutes = _as_float(duration_minutes, default=-1.0)
    if minutes < 0:
        return "unknown"
    if minutes < 15:
        return "<15m"
    if minutes < 30:
        return "15-30m"
    if minutes < 60:
        return "30-60m"
    if minutes < 180:
        return "1-3h"
    if minutes < 360:
        return "3-6h"
    return ">6h"


def normalize_observation(raw: Mapping[str, Any], index: int) -> TradeObservation:
    """Normalize a raw mapping into a typed trade observation."""
    duration = raw.get("duration_minutes", raw.get("duration_min", raw.get("duration", 0.0)))
    covered = raw.get(
        "covered_feature_subset",
        raw.get("covered", raw.get("has_entry_candle", raw.get("feature_covered", False))),
    )
    rule = raw.get(
        "candidate_rule_triggered",
        raw.get("rule_triggered", raw.get("shadow_rule_triggered", False)),
    )
    return TradeObservation(
        trade_id=_as_text(raw.get("trade_id", raw.get("id", f"row_{index}"))),
        day=_as_text(raw.get("day", raw.get("date", raw.get("open_date", "unknown"))))[:10],
        symbol=_as_text(raw.get("symbol", raw.get("pair", "unknown"))).upper(),
        side=_as_text(raw.get("side", raw.get("direction", "unknown"))).lower(),
        exit_reason=_as_text(raw.get("exit_reason", raw.get("reason", "unknown"))).lower(),
        duration_minutes=_as_float(duration),
        net_pnl=_as_float(raw.get("net_pnl", raw.get("pnl", raw.get("profit_abs", 0.0)))),
        covered_feature_subset=_as_bool(covered),
        candidate_rule_triggered=_as_bool(rule),
        source_split=_as_text(raw.get("source_split", raw.get("split", "oos"))).lower(),
    )


def normalize_observations(rows: Sequence[Mapping[str, Any]] | None) -> list[TradeObservation]:
    if not rows:
        return []
    return [normalize_observation(row, index) for index, row in enumerate(rows)]


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _round(value: float | None, places: int = 8) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def _max_drawdown(net_pnls: Sequence[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in net_pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    return round(max_dd, 8)


def _hypothesis_tags(row: TradeObservation) -> list[str]:
    tags: list[str] = []
    if row.exit_reason == "stop_loss" and row.duration_minutes < 30:
        tags.append("H1")
    if "ETH" in row.symbol and row.side == "long":
        tags.append("H2")
    if row.candidate_rule_triggered:
        tags.append("H6")
    return tags


def _compute_metrics(rows: Sequence[TradeObservation], total_rows: int) -> dict[str, Any]:
    trade_count = len(rows)
    net_pnls = [row.net_pnl for row in rows]
    winners = [row for row in rows if row.is_winner]
    losers = [row for row in rows if row.is_loser]
    triggered = [row for row in rows if row.candidate_rule_triggered]
    true_positives = [row for row in triggered if row.is_loser]
    false_positives = [row for row in triggered if row.is_winner]
    false_negatives = [row for row in rows if row.is_loser and not row.candidate_rule_triggered]
    gross_profit = sum(row.net_pnl for row in winners)
    gross_loss = sum(row.net_pnl for row in losers)
    winner_pnl_removed = sum(row.net_pnl for row in false_positives)
    loser_pnl_removed = sum(row.net_pnl for row in true_positives)
    winners_retained = [row for row in winners if not row.candidate_rule_triggered]

    precision = _safe_ratio(float(len(true_positives)), float(len(triggered)))
    recall = _safe_ratio(float(len(true_positives)), float(len(losers)))
    win_rate = _safe_ratio(float(len(winners)), float(trade_count))
    profit_factor = None if gross_loss == 0 else gross_profit / abs(gross_loss)
    winner_retention_rate = _safe_ratio(float(len(winners_retained)), float(len(winners)))
    coverage_ratio = _safe_ratio(float(trade_count), float(total_rows))
    simulated_removed_pnl_delta = -sum(row.net_pnl for row in triggered)

    return {
        "trade_count": trade_count,
        "net_pnl": _round(sum(net_pnls)),
        "gross_profit": _round(gross_profit),
        "gross_loss": _round(gross_loss),
        "profit_factor": _round(profit_factor),
        "win_rate": _round(win_rate),
        "max_drawdown": _max_drawdown(net_pnls),
        "winner_count": len(winners),
        "loser_count": len(losers),
        "triggered_count": len(triggered),
        "true_positive_count": len(true_positives),
        "false_positive_count": len(false_positives),
        "false_negative_count": len(false_negatives),
        "precision": _round(precision),
        "recall": _round(recall),
        "winner_retention_rate": _round(winner_retention_rate),
        "winner_pnl_removed": _round(winner_pnl_removed),
        "loser_pnl_removed": _round(loser_pnl_removed),
        "simulated_removed_pnl_delta": _round(simulated_removed_pnl_delta),
        "coverage_ratio": _round(coverage_ratio),
    }


def _slice_bucket(row: TradeObservation, dimension: str) -> str:
    if dimension == "day":
        return row.day
    if dimension == "symbol":
        return row.symbol
    if dimension == "side":
        return row.side
    if dimension == "exit_reason":
        return row.exit_reason
    if dimension == "duration_bucket":
        return row.duration_bucket
    if dimension == "covered_vs_uncovered":
        return row.covered_vs_uncovered
    raise ValueError(f"Unsupported slice dimension: {dimension}")


def compute_slice_metrics(
    rows: Sequence[Mapping[str, Any]] | Sequence[TradeObservation],
) -> dict[str, Any]:
    """Compute OOS slice metrics from explicit in-memory observations."""
    if not rows:
        observations: list[TradeObservation] = []
    elif isinstance(rows[0], TradeObservation):  # type: ignore[index]
        observations = list(rows)  # type: ignore[arg-type]
    else:
        observations = normalize_observations(rows)  # type: ignore[arg-type]

    total_rows = len(observations)
    if total_rows == 0:
        return {
            "oos_slice_metrics_computed": False,
            "slice_metrics_status": "blocked_no_rows_loaded",
            "observation_count": 0,
            "slice_count": 0,
            "slice_metrics": [],
            "hypothesis_slice_metrics": [],
            "global_metrics": _compute_metrics([], 0),
        }

    slice_metrics: list[dict[str, Any]] = []
    for dimension in SLICE_DIMENSIONS:
        grouped: dict[str, list[TradeObservation]] = defaultdict(list)
        for row in observations:
            grouped[_slice_bucket(row, dimension)].append(row)
        for bucket, bucket_rows in sorted(grouped.items(), key=lambda item: item[0]):
            tags = sorted({tag for row in bucket_rows for tag in _hypothesis_tags(row)})
            slice_metrics.append(
                {
                    "dimension": dimension,
                    "bucket": bucket,
                    "hypothesis_ids": tags,
                    "metrics": _compute_metrics(bucket_rows, total_rows),
                    "promotion_status": "blocked_research_only",
                }
            )

    hypothesis_slice_metrics: list[dict[str, Any]] = []
    for hypothesis_id in HYPOTHESIS_SCOPE:
        hypothesis_rows = [row for row in observations if hypothesis_id in _hypothesis_tags(row)]
        hypothesis_slice_metrics.append(
            {
                "hypothesis_id": hypothesis_id,
                "metrics": _compute_metrics(hypothesis_rows, total_rows),
                "oos_validated": False,
                "promotion_status": "blocked_pending_oos_acceptance",
                "can_apply_to_freqtrade": False,
                "can_apply_to_risk_manager": False,
                "can_promote_rules": False,
                "can_promote_model": False,
            }
        )

    return {
        "oos_slice_metrics_computed": True,
        "slice_metrics_status": "computed_research_only_not_validated",
        "observation_count": total_rows,
        "slice_count": len(slice_metrics),
        "slice_metrics": slice_metrics,
        "hypothesis_slice_metrics": hypothesis_slice_metrics,
        "global_metrics": _compute_metrics(observations, total_rows),
    }


def _load_input_rows(input_path: str | Path | None) -> list[dict[str, Any]]:
    if input_path is None:
        return []
    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("observations", "rows", "trades", "paper_trades"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
    raise ValueError("input JSON must be a list or contain observations/rows/trades/paper_trades")


def _gate_matrix(metrics_result: dict[str, Any]) -> list[dict[str, Any]]:
    computed = bool(metrics_result.get("oos_slice_metrics_computed"))
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": True,
            "evidence": "research_only=true; operational_authority=false",
        },
        {
            "gate_id": "paper_master_divergence_confirmed",
            "gate_name": "Paper/Master divergence remains explicit",
            "severity": "critical",
            "passed": True,
            "evidence": "paper_minus_master_net_pnl=-164.52110752",
        },
        {
            "gate_id": "slice_dimensions_declared",
            "gate_name": "Required OOS slice dimensions declared",
            "severity": "high",
            "passed": True,
            "evidence": f"dimensions={SLICE_DIMENSIONS}",
        },
        {
            "gate_id": "oos_metrics_computation_status_explicit",
            "gate_name": "OOS slice metrics computation status is explicit",
            "severity": "high",
            "passed": True,
            "evidence": f"computed={computed}; rows={metrics_result.get('observation_count')}",
        },
        {
            "gate_id": "oos_required_not_bypassed",
            "gate_name": "OOS validation remains mandatory",
            "severity": "critical",
            "passed": True,
            "evidence": "oos_validation_required=true; oos_validated=false",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "can_promote_rules=false; can_promote_model=false",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": True,
            "evidence": "no runtime updates; sends_orders=false",
        },
    ]


def _gate_summary(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [gate for gate in gates if not bool(gate["passed"])]
    critical_failed = [gate["gate_id"] for gate in failed if gate.get("severity") == "critical"]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [str(gate["gate_id"]) for gate in failed],
        "critical_failed_gate_ids": critical_failed,
    }


def build_oos_slice_metrics_report(
    project_root: str | Path,
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a blocked research-only report with optional in-memory slice metrics."""
    metrics_result = compute_slice_metrics(rows or [])
    gates = _gate_matrix(metrics_result)
    observation_count = int(metrics_result["observation_count"])

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root),
        "status": "blocked",
        "decision": DECISION,
        "reason": "paper_master_divergence_requires_oos_slice_metrics_before_any_operation",
        "input_mode": "in_memory_rows_loaded" if observation_count else "no_runtime_rows_loaded",
        "paper_kpis": DEFAULT_PAPER_KPIS,
        "master_kpis": DEFAULT_MASTER_KPIS,
        "divergence_metrics": DEFAULT_DIVERGENCE_METRICS,
        "divergence_confirmed": True,
        "paper_replicates_master_edge": False,
        "canonical_cluster_evidence": CANONICAL_CLUSTER_EVIDENCE,
        "hypothesis_scope": HYPOTHESIS_SCOPE,
        "oos_slice_dimensions": SLICE_DIMENSIONS,
        "minimum_metrics": MINIMUM_METRICS,
        "oos_validation_required": True,
        "oos_validated": False,
        "ready_for_candidate_registry": False,
        "remediation_application_allowed": False,
        "slice_metrics_created": True,
        "slice_metrics_are_operational": False,
        "registers_candidate_rules": False,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "allowed_next_steps": NEXT_RESEARCH_GATES,
        "gate_matrix": gates,
        "gate_summary": _gate_summary(gates),
        **metrics_result,
        **SAFETY_FLAGS,
    }
    return report


def run_oos_slice_metrics_research(
    project_root: str | Path,
    *,
    write: bool = False,
    output_path: str | Path | None = None,
    input_path: str | Path | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the OOS slice metrics research report."""
    loaded_rows = list(rows or [])
    if input_path is not None:
        loaded_rows = _load_input_rows(input_path)

    report = build_oos_slice_metrics_report(project_root, rows=loaded_rows)
    report["write_requested"] = bool(write)
    report["write_performed"] = False
    report["writes_reports"] = False
    report["writes_data"] = False
    report["writes_runtime"] = False
    report["writes_sqlite"] = False
    report["writes_parquet"] = False
    report["output_path"] = None

    if write:
        destination = Path(output_path) if output_path else Path(project_root) / "data" / "reports" / "paper_master_divergence_oos_slice_metrics_v1.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
        report["write_performed"] = True
        report["writes_reports"] = True
        report["output_path"] = str(destination)

    return report
