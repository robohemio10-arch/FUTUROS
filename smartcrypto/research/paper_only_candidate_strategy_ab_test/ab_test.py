"""Paper-only candidate strategy AB test over closed paper trade evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .decision_filter import (
    BLOCKED_RULES,
    PaperOnlyCandidateDecisionFilter,
)

SCHEMA_VERSION = "paper_only_candidate_strategy_ab_test_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "PAPER_CANDIDATE_TEST_ONLY"
DEFAULT_ATTRIBUTION_REPORT = Path("data/reports/paper_closed_trades_shadow_rule_attribution_v1.json")
DEFAULT_IMPACT_REPORT = Path("data/reports/paper_shadow_observation_daily_impact_report_v1.json")
DEFAULT_REMEDIATION_REPORT = Path("data/reports/paper_shadow_survivor_remediation_research_v1.json")
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_only_candidate_strategy_ab_test_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_only_candidate_strategy_ab_test_v1.md")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "candidate_only": True,
    "live_behavior_changed": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "changes_model": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "operational_authority": False,
}

FORBIDDEN_NEXT_ACTIONS = [
    "habilitar live",
    "habilitar canary",
    "enviar ordem",
    "acessar exchange privada",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar active signals",
    "escrever data/runtime",
    "escrever SQLite",
    "escrever Parquet operacional",
]


@dataclass(frozen=True)
class LoadedABTestInputs:
    attribution_report: dict[str, Any] | None
    impact_report: dict[str, Any] | None
    remediation_report: dict[str, Any] | None
    input_mode: str
    source_status: str
    source_reason: str
    source_paths: dict[str, str | None]
    source_sha256: dict[str, str | None]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("json_payload_must_be_object")
    return dict(payload)


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return numeric


def _safe_int(value: object, *, default: int = 0) -> int:
    return int(_safe_float(value, default=float(default)))


def _round(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), 10)


def _profit_factor(values: Sequence[float]) -> float | None:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss == 0:
        return None
    return _round(gross_profit / gross_loss)


def _win_rate(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return _round(sum(1 for value in values if value >= 0) / len(values))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim", "y"}


def _extract_rows(report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if report is None:
        return []
    rows = report.get("attribution_table")
    if isinstance(rows, list):
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    sample = report.get("attribution_table_sample")
    count = _safe_int(report.get("attributed_trade_count"))
    if isinstance(sample, list) and len(sample) == count:
        return [dict(item) for item in sample if isinstance(item, Mapping)]
    replay_metrics = report.get("replay_metrics")
    if isinstance(replay_metrics, Mapping):
        replay_rows = replay_metrics.get("replay_rows")
        if isinstance(replay_rows, list):
            return [dict(item) for item in replay_rows if isinstance(item, Mapping)]
    return []


def load_ab_test_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    paper_attribution_report: str | Path | None = None,
    impact_report: str | Path | None = None,
    remediation_report: str | Path | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    impact_payload: Mapping[str, Any] | None = None,
    remediation_payload: Mapping[str, Any] | None = None,
) -> LoadedABTestInputs:
    root = Path(project_root).resolve()
    source_paths = {"paper_attribution_report": None, "impact_report": None, "remediation_report": None}
    source_sha256 = {"paper_attribution_report": None, "impact_report": None, "remediation_report": None}
    if attribution_payload is not None:
        return LoadedABTestInputs(
            attribution_report=dict(attribution_payload),
            impact_report=dict(impact_payload) if impact_payload is not None else None,
            remediation_report=dict(remediation_payload) if remediation_payload is not None else None,
            input_mode="in_memory_ab_test_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    if not allow_runtime_read:
        return LoadedABTestInputs(
            attribution_report=None,
            impact_report=None,
            remediation_report=None,
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )

    attribution_path = _resolve_path(root, paper_attribution_report, DEFAULT_ATTRIBUTION_REPORT)
    impact_path = _resolve_path(root, impact_report, DEFAULT_IMPACT_REPORT)
    remediation_path = _resolve_path(root, remediation_report, DEFAULT_REMEDIATION_REPORT)
    source_paths = {
        "paper_attribution_report": _project_relative(attribution_path, root),
        "impact_report": _project_relative(impact_path, root),
        "remediation_report": _project_relative(remediation_path, root),
    }
    source_sha256 = {
        "paper_attribution_report": _sha256_file(attribution_path),
        "impact_report": _sha256_file(impact_path),
        "remediation_report": _sha256_file(remediation_path),
    }
    if not attribution_path.exists():
        return LoadedABTestInputs(
            attribution_report=None,
            impact_report=None,
            remediation_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_paper_attribution_report",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    try:
        return LoadedABTestInputs(
            attribution_report=_read_json(attribution_path),
            impact_report=_read_json(impact_path) if impact_path.exists() else None,
            remediation_report=_read_json(remediation_path) if remediation_path.exists() else None,
            input_mode="runtime_read_requested",
            source_status="ok",
            source_reason="sources_loaded_read_only",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return LoadedABTestInputs(
            attribution_report=None,
            impact_report=None,
            remediation_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )


def compute_ab_test(
    *,
    attribution_report: Mapping[str, Any],
    impact_report: Mapping[str, Any] | None = None,
    remediation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _extract_rows(attribution_report)
    if not rows:
        rows = _extract_rows(impact_report)
    if not rows:
        return _compute_ab_test_from_aggregates(impact_report=impact_report, remediation_report=remediation_report)
    filter_ = PaperOnlyCandidateDecisionFilter(active=True)
    decision_log: list[dict[str, Any]] = []
    allowed_values: list[float] = []
    blocked_values: list[float] = []
    blocked_eth_long_count = 0
    blocked_eth_short_count = 0
    false_positive_reduction = 0
    preserved_loss_count = 0
    missed_opportunity_count = 0
    avoided_loss_pnl = 0.0
    missed_profit_pnl = 0.0

    for index, row in enumerate(rows, start=1):
        decision = filter_.evaluate(row)
        pnl = _safe_float(row.get("pnl"))
        item = {
            "decision_log_id": f"paper_candidate_ab_{index:06d}",
            "trade_id": row.get("trade_id"),
            "order_id": row.get("order_id"),
            "symbol_norm": decision.symbol_norm,
            "side_norm": decision.side_norm,
            "decision": decision.decision,
            "reason": decision.reason,
            "pnl": _round(pnl),
            "paper_only": True,
            "candidate_only": True,
            "sends_orders": False,
            "exchange_private_access": False,
        }
        decision_log.append(item)
        if decision.decision == "BLOCK":
            blocked_values.append(pnl)
            if decision.symbol_norm == "ETHUSDT" and decision.side_norm == "long":
                blocked_eth_long_count += 1
            if decision.symbol_norm == "ETHUSDT" and decision.side_norm == "short":
                blocked_eth_short_count += 1
            if pnl < 0:
                false_positive_reduction += 1
                preserved_loss_count += 1
                avoided_loss_pnl += abs(pnl)
            else:
                missed_opportunity_count += 1
                missed_profit_pnl += pnl
        else:
            allowed_values.append(pnl)

    baseline_values = [_safe_float(row.get("pnl")) for row in rows]
    baseline_net_pnl = _round(sum(baseline_values))
    impact_summary = impact_report.get("impact_summary") if isinstance(impact_report, Mapping) else None
    if isinstance(impact_summary, Mapping) and impact_summary.get("baseline_net_pnl") is not None:
        baseline_net_pnl = _round(_safe_float(impact_summary.get("baseline_net_pnl")))
    candidate_allowed_net_pnl = _round(sum(allowed_values))
    blocked_net_pnl = _round(sum(blocked_values))
    blocked_trade_count = len(blocked_values)
    allowed_trade_count = len(allowed_values)
    return {
        "baseline_summary": {
            "baseline_trade_count": len(rows),
            "baseline_net_pnl": baseline_net_pnl,
            "baseline_win_rate": _win_rate(baseline_values),
            "baseline_profit_factor": _profit_factor(baseline_values),
        },
        "candidate_summary": {
            "candidate_trade_count": allowed_trade_count,
            "blocked_trade_count": blocked_trade_count,
            "allowed_trade_count": allowed_trade_count,
            "blocked_eth_long_count": blocked_eth_long_count,
            "blocked_eth_short_count": blocked_eth_short_count,
            "candidate_allowed_net_pnl": candidate_allowed_net_pnl,
            "blocked_net_pnl": blocked_net_pnl,
            "avoided_loss_pnl": _round(avoided_loss_pnl),
            "missed_profit_pnl": _round(missed_profit_pnl),
            "false_positive_reduction": false_positive_reduction,
            "preserved_loss_count": preserved_loss_count,
            "missed_opportunity_count": missed_opportunity_count,
            "candidate_win_rate": _win_rate(allowed_values),
            "candidate_profit_factor": _profit_factor(allowed_values),
            "candidate_vs_baseline_net_pnl_delta": _round((candidate_allowed_net_pnl or 0.0) - (baseline_net_pnl or 0.0)),
            "paper_behavior_changed": blocked_trade_count > 0,
            "live_behavior_changed": False,
        },
        "ab_test_summary": {
            "paper_behavior_changed": blocked_trade_count > 0,
            "live_behavior_changed": False,
            "candidate_filter_active": True,
            "decision_log_rows": len(decision_log),
        },
        "decision_log_sample": decision_log[:20],
    }


def _compute_ab_test_from_aggregates(
    *,
    impact_report: Mapping[str, Any] | None,
    remediation_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    impact_summary = impact_report.get("impact_summary") if isinstance(impact_report, Mapping) else {}
    remediation_summary = remediation_report.get("remediation_summary") if isinstance(remediation_report, Mapping) else {}
    survivor_breakdown = impact_report.get("survivor_rule_breakdown") if isinstance(impact_report, Mapping) else []
    baseline_trade_count = _safe_int(impact_report.get("attributed_trade_count") if isinstance(impact_report, Mapping) else None)
    baseline_net_pnl = _round(_safe_float(impact_summary.get("baseline_net_pnl"))) if isinstance(impact_summary, Mapping) else 0.0
    if baseline_net_pnl == 0.0 and isinstance(impact_summary, Mapping):
        baseline_net_pnl = _round(_safe_float(impact_summary.get("allowed_net_pnl")) + _safe_float(impact_summary.get("blocked_net_pnl")))

    blocked_trade_count = 0
    blocked_eth_long_count = 0
    blocked_eth_short_count = 0
    false_positive_reduction = 0
    missed_opportunity_count = 0
    for item in survivor_breakdown if isinstance(survivor_breakdown, list) else []:
        if not isinstance(item, Mapping):
            continue
        rule_id = str(item.get("survivor_rule_id") or "")
        if rule_id not in {rule["source_survivor_rule_id"] for rule in BLOCKED_RULES}:
            continue
        trades = _safe_int(item.get("trades"))
        false_positives = _safe_int(item.get("false_positive_count"))
        blocked_trade_count += trades
        false_positive_reduction += false_positives
        missed_opportunity_count += max(trades - false_positives, 0)
        if rule_id.endswith("ETHUSDT__side_norm_long"):
            blocked_eth_long_count += trades
        if rule_id.endswith("ETHUSDT__side_norm_short"):
            blocked_eth_short_count += trades

    if isinstance(remediation_summary, Mapping):
        blocked_trade_count = _safe_int(
            remediation_summary.get("remediated_would_block_count"),
            default=blocked_trade_count,
        ) - _safe_int(impact_report.get("would_block_count") if isinstance(impact_report, Mapping) else None)
        blocked_trade_count = max(blocked_trade_count, blocked_eth_long_count + blocked_eth_short_count)
        false_positive_reduction = _safe_int(remediation_summary.get("false_positive_reduction"), default=false_positive_reduction)
        missed_opportunity_count = _safe_int(remediation_summary.get("missed_opportunity_delta"), default=missed_opportunity_count)

    allowed_trade_count = max(baseline_trade_count - blocked_trade_count, 0)
    candidate_allowed_net_pnl = None
    if isinstance(impact_summary, Mapping):
        candidate_allowed_net_pnl = _round(_safe_float(impact_summary.get("blocked_net_pnl")))
    if isinstance(remediation_summary, Mapping) and remediation_summary.get("remediated_allowed_net_pnl") is not None:
        # Remediation reports the net of trades still allowed by the negative-survivor policy.
        candidate_allowed_net_pnl = _round(_safe_float(remediation_summary.get("remediated_allowed_net_pnl")))
        if candidate_allowed_net_pnl == 0.0 and isinstance(impact_summary, Mapping):
            candidate_allowed_net_pnl = _round(_safe_float(impact_summary.get("blocked_net_pnl")))
    blocked_net_pnl = _round((baseline_net_pnl or 0.0) - (candidate_allowed_net_pnl or 0.0))
    return {
        "baseline_summary": {
            "baseline_trade_count": baseline_trade_count,
            "baseline_net_pnl": baseline_net_pnl,
            "baseline_win_rate": None,
            "baseline_profit_factor": None,
        },
        "candidate_summary": {
            "candidate_trade_count": allowed_trade_count,
            "blocked_trade_count": blocked_trade_count,
            "allowed_trade_count": allowed_trade_count,
            "blocked_eth_long_count": blocked_eth_long_count,
            "blocked_eth_short_count": blocked_eth_short_count,
            "candidate_allowed_net_pnl": candidate_allowed_net_pnl,
            "blocked_net_pnl": blocked_net_pnl,
            "avoided_loss_pnl": None,
            "missed_profit_pnl": None,
            "false_positive_reduction": false_positive_reduction,
            "preserved_loss_count": false_positive_reduction,
            "missed_opportunity_count": missed_opportunity_count,
            "candidate_win_rate": None,
            "candidate_profit_factor": None,
            "candidate_vs_baseline_net_pnl_delta": _round((candidate_allowed_net_pnl or 0.0) - (baseline_net_pnl or 0.0)),
            "paper_behavior_changed": blocked_trade_count > 0,
            "live_behavior_changed": False,
        },
        "ab_test_summary": {
            "paper_behavior_changed": blocked_trade_count > 0,
            "live_behavior_changed": False,
            "candidate_filter_active": baseline_trade_count > 0,
            "decision_log_rows": baseline_trade_count,
            "decision_log_mode": "aggregate_from_impact_report",
        },
        "decision_log_sample": [
            {
                "decision_log_id": "paper_candidate_ab_aggregate_block_eth_long",
                "symbol_norm": "ETHUSDT",
                "side_norm": "long",
                "decision": "BLOCK",
                "reason": "discarded_negative_survivor_ethusdt_long",
                "aggregate_trade_count": blocked_eth_long_count,
                "paper_only": True,
                "candidate_only": True,
                "sends_orders": False,
                "exchange_private_access": False,
            },
            {
                "decision_log_id": "paper_candidate_ab_aggregate_block_eth_short",
                "symbol_norm": "ETHUSDT",
                "side_norm": "short",
                "decision": "BLOCK",
                "reason": "discarded_negative_survivor_ethusdt_short",
                "aggregate_trade_count": blocked_eth_short_count,
                "paper_only": True,
                "candidate_only": True,
                "sends_orders": False,
                "exchange_private_access": False,
            },
            {
                "decision_log_id": "paper_candidate_ab_aggregate_allow_non_eth",
                "symbol_norm": "NON_ETHUSDT",
                "side_norm": "any",
                "decision": "ALLOW",
                "reason": "candidate_filter_allow",
                "aggregate_trade_count": allowed_trade_count,
                "paper_only": True,
                "candidate_only": True,
                "sends_orders": False,
                "exchange_private_access": False,
            },
        ],
    }


def _empty_ab_test() -> dict[str, Any]:
    return {
        "baseline_summary": {
            "baseline_trade_count": 0,
            "baseline_net_pnl": 0.0,
            "baseline_win_rate": None,
            "baseline_profit_factor": None,
        },
        "candidate_summary": {
            "candidate_trade_count": 0,
            "blocked_trade_count": 0,
            "allowed_trade_count": 0,
            "blocked_eth_long_count": 0,
            "blocked_eth_short_count": 0,
            "candidate_allowed_net_pnl": 0.0,
            "blocked_net_pnl": 0.0,
            "avoided_loss_pnl": 0.0,
            "missed_profit_pnl": 0.0,
            "false_positive_reduction": 0,
            "preserved_loss_count": 0,
            "missed_opportunity_count": 0,
            "candidate_win_rate": None,
            "candidate_profit_factor": None,
            "candidate_vs_baseline_net_pnl_delta": 0.0,
            "paper_behavior_changed": False,
            "live_behavior_changed": False,
        },
        "ab_test_summary": {
            "paper_behavior_changed": False,
            "live_behavior_changed": False,
            "candidate_filter_active": False,
            "decision_log_rows": 0,
        },
        "decision_log_sample": [],
    }


def build_paper_only_candidate_strategy_ab_test_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    paper_attribution_report: str | Path | None = None,
    impact_report: str | Path | None = None,
    remediation_report: str | Path | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    impact_payload: Mapping[str, Any] | None = None,
    remediation_payload: Mapping[str, Any] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    loaded = load_ab_test_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        paper_attribution_report=paper_attribution_report,
        impact_report=impact_report,
        remediation_report=remediation_report,
        attribution_payload=attribution_payload,
        impact_payload=impact_payload,
        remediation_payload=remediation_payload,
    )
    write_requested = bool(write and not no_write)
    filter_ = PaperOnlyCandidateDecisionFilter(active=loaded.source_status == "ok")
    if loaded.source_status == "ok" and loaded.attribution_report is not None:
        ab_test = compute_ab_test(
            attribution_report=loaded.attribution_report,
            impact_report=loaded.impact_report,
            remediation_report=loaded.remediation_report,
        )
        status = "ok" if ab_test["baseline_summary"]["baseline_trade_count"] > 0 else "blocked"
        reason = "paper_only_candidate_strategy_ab_test_computed"
        integration_status = "paper_adapter_missing"
    else:
        ab_test = _empty_ab_test()
        status = "blocked"
        reason = "candidate_ab_test_requires_explicit_runtime_read_or_in_memory_inputs" if loaded.input_mode == "no_runtime_rows_loaded" else loaded.source_reason
        integration_status = "paper_adapter_missing"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "candidate_filter_active": ab_test["ab_test_summary"]["candidate_filter_active"],
        "candidate_filter_definition": filter_.definition(),
        "blocked_rules": [dict(rule) for rule in BLOCKED_RULES],
        "baseline_summary": ab_test["baseline_summary"],
        "candidate_summary": ab_test["candidate_summary"],
        "ab_test_summary": ab_test["ab_test_summary"],
        "decision_log_sample": ab_test["decision_log_sample"],
        "integration_status": integration_status,
        "recommended_next_action": _recommended_next_action(ab_test),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "safety_flags": dict(SAFETY_FLAGS),
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_ab_test_report(report)
    if write_requested:
        output_path = _resolve_output_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["status"] = "blocked"
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_ab_test_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _recommended_next_action(ab_test: Mapping[str, Any]) -> str:
    candidate = ab_test.get("candidate_summary", {})
    if not isinstance(candidate, Mapping):
        return "manter_candidate_em_teste_paper_only_sem_integracao"
    if _safe_int(candidate.get("blocked_trade_count")) > 0 and _safe_float(candidate.get("candidate_vs_baseline_net_pnl_delta")) > 0:
        return "manter_filtro_como_candidate_paper_only_e_criar_adapter_isolado_em_branch_separada"
    return "nao_integrar_candidate_e_revisar_evidencias"


def validate_ab_test_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("decision") != DECISION:
        errors.append("decision_must_be_paper_candidate_test_only")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    for field in (
        "candidate_filter_definition",
        "blocked_rules",
        "baseline_summary",
        "candidate_summary",
        "ab_test_summary",
        "decision_log_sample",
        "integration_status",
        "write_performed",
    ):
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    baseline = report.get("baseline_summary", {})
    candidate = report.get("candidate_summary", {})
    return "\n".join(
        [
            "# Paper-Only Candidate Strategy AB Test V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Integration status: `{report.get('integration_status')}`",
            f"- Baseline trades: `{baseline.get('baseline_trade_count')}`",
            f"- Blocked trades: `{candidate.get('blocked_trade_count')}`",
            f"- Allowed trades: `{candidate.get('allowed_trade_count')}`",
            f"- Baseline net PnL: `{baseline.get('baseline_net_pnl')}`",
            f"- Candidate allowed net PnL: `{candidate.get('candidate_allowed_net_pnl')}`",
            f"- Candidate delta: `{candidate.get('candidate_vs_baseline_net_pnl_delta')}`",
            f"- False positive reduction: `{candidate.get('false_positive_reduction')}`",
            f"- Paper behavior changed: `{candidate.get('paper_behavior_changed')}`",
            f"- Live behavior changed: `{candidate.get('live_behavior_changed')}`",
            "",
            "This report is candidate/paper-only. It does not enable live, canary, order submission or private exchange access.",
            "",
        ]
    )


def _resolve_output_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _validate_output_path(root: Path, path: Path, *, suffix: str) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != suffix:
        return f"write_blocked_output_must_be_{suffix.removeprefix('.')}_report"
    return None
