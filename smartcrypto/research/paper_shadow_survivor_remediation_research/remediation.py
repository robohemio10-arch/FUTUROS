"""Research-only remediation analysis for discarded paper shadow survivors."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "paper_shadow_survivor_remediation_research_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_IMPACT_REPORT = Path("data/reports/paper_shadow_observation_daily_impact_report_v1.json")
DEFAULT_ATTRIBUTION_REPORT = Path("data/reports/paper_closed_trades_shadow_rule_attribution_v1.json")
DEFAULT_REPLAY_REPORT = Path("data/reports/ocr_master_candle_shadow_observation_replay_v1.json")
DEFAULT_CONTRACT_REPORT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.json")
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_shadow_survivor_remediation_research_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_shadow_survivor_remediation_research_v1.md")

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "paper_observation_allowed": False,
    "ready_for_shadow_observation": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "registers_shadow_rules": False,
    "applies_shadow_rules": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
}

FORBIDDEN_NEXT_ACTIONS = [
    "ativar observer",
    "aplicar veto",
    "promover survivor",
    "registrar regra operacional",
    "alterar runtime",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar configs",
    "alterar registry",
    "alterar sinais ativos",
    "enviar ordens",
    "acessar exchange privada",
    "escrever data/runtime",
    "escrever SQLite",
    "escrever Parquet operacional",
]

OUTCOME_FIELDS = {
    "pnl",
    "expected_value_delta",
    "false_positive_observation",
    "preserved_loss",
    "missed_opportunity",
    "would_allow",
    "would_block",
}


@dataclass(frozen=True)
class LoadedRemediationInputs:
    impact_report: dict[str, Any] | None
    attribution_report: dict[str, Any] | None
    replay_report: dict[str, Any] | None
    source_contract_report: dict[str, Any] | None
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


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim", "y"}


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


def _extract_replay_rows(replay_report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if replay_report is None:
        return []
    metrics = replay_report.get("replay_metrics")
    if not isinstance(metrics, Mapping):
        return []
    rows = metrics.get("replay_rows")
    if isinstance(rows, list):
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    sample = metrics.get("replay_rows_sample")
    count = _safe_int(metrics.get("replay_trade_count"))
    if isinstance(sample, list) and len(sample) == count:
        return [dict(item) for item in sample if isinstance(item, Mapping)]
    return []


def _extract_attribution_rows(attribution_report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if attribution_report is None:
        return []
    rows = attribution_report.get("attribution_table")
    if isinstance(rows, list):
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    sample = attribution_report.get("attribution_table_sample")
    count = _safe_int(attribution_report.get("attributed_trade_count"))
    if isinstance(sample, list) and len(sample) == count:
        return [dict(item) for item in sample if isinstance(item, Mapping)]
    return []


def _rows_from_reports(
    attribution_report: Mapping[str, Any] | None,
    replay_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    attribution_rows = _extract_attribution_rows(attribution_report)
    if attribution_rows:
        return attribution_rows
    replay_rows = _extract_replay_rows(replay_report)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(replay_rows, start=1):
        would_allow = _truthy(row.get("would_allow"))
        rows.append(
            {
                "remediation_row_id": f"remediation_from_replay_{index:06d}",
                "trade_id": row.get("trade_id"),
                "order_id": row.get("order_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "pnl": _safe_float(row.get("pnl")),
                "would_allow": would_allow,
                "would_block": _truthy(row.get("would_block")) or not would_allow,
                "matched_survivor_rule_id": row.get("matched_survivor_rule_id"),
                "matched_survivor_expression": row.get("matched_survivor_expression"),
                "expected_value_delta": row.get("expected_value_delta"),
                "attributed": True,
            }
        )
    return rows


def _classify(row: Mapping[str, Any], *, allow_field: str = "would_allow", block_field: str = "would_block") -> str:
    pnl = _safe_float(row.get("pnl"))
    would_allow = _truthy(row.get(allow_field))
    would_block = _truthy(row.get(block_field)) or not would_allow
    if would_allow and pnl < 0:
        return "false_positive"
    if would_allow and pnl >= 0:
        return "true_positive_allow"
    if would_block and pnl < 0:
        return "preserved_loss"
    if would_block and pnl >= 0:
        return "missed_opportunity"
    return "unclassified"


def _metrics_for_rows(rows: Sequence[Mapping[str, Any]], *, allow_field: str = "would_allow", block_field: str = "would_block") -> dict[str, Any]:
    allowed = [row for row in rows if _truthy(row.get(allow_field))]
    blocked = [row for row in rows if _truthy(row.get(block_field)) or not _truthy(row.get(allow_field))]
    allowed_values = [_safe_float(row.get("pnl")) for row in allowed]
    blocked_values = [_safe_float(row.get("pnl")) for row in blocked]
    return {
        "would_allow_count": len(allowed),
        "would_block_count": len(blocked),
        "allowed_net_pnl": _round(sum(allowed_values)),
        "blocked_net_pnl": _round(sum(blocked_values)),
        "allowed_profit_factor": _profit_factor(allowed_values),
        "blocked_profit_factor": _profit_factor(blocked_values),
        "allowed_win_rate": _win_rate(allowed_values),
        "blocked_win_rate": _win_rate(blocked_values),
        "false_positive_count": sum(1 for row in rows if _classify(row, allow_field=allow_field, block_field=block_field) == "false_positive"),
        "preserved_loss_count": sum(1 for row in rows if _classify(row, allow_field=allow_field, block_field=block_field) == "preserved_loss"),
        "missed_opportunity_count": sum(1 for row in rows if _classify(row, allow_field=allow_field, block_field=block_field) == "missed_opportunity"),
    }


def _negative_survivor_ids(impact_report: Mapping[str, Any] | None, rows: Sequence[Mapping[str, Any]]) -> set[str]:
    ids: set[str] = set()
    if impact_report is not None:
        for item in impact_report.get("survivor_rule_breakdown", []):
            if not isinstance(item, Mapping):
                continue
            rule_id = str(item.get("survivor_rule_id") or "")
            if not rule_id:
                continue
            recommendation = str(item.get("recommendation") or "")
            if recommendation == "DISCARD_RESEARCH_ONLY" or (
                _safe_float(item.get("net_pnl")) < 0 and _safe_int(item.get("false_positive_count")) > 0
            ):
                ids.add(rule_id)
    if ids:
        return ids

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        rule_id = str(row.get("matched_survivor_rule_id") or "")
        if rule_id:
            grouped.setdefault(rule_id, []).append(row)
    for rule_id, group_rows in grouped.items():
        values = [_safe_float(row.get("pnl")) for row in group_rows]
        false_positives = sum(1 for row in group_rows if _classify(row) == "false_positive")
        if sum(values) < 0 and false_positives > 0:
            ids.add(rule_id)
    return ids


def _remediate_rows(rows: Sequence[Mapping[str, Any]], discarded_ids: set[str]) -> list[dict[str, Any]]:
    remediated: list[dict[str, Any]] = []
    for row in rows:
        rule_id = str(row.get("matched_survivor_rule_id") or "")
        discarded = rule_id in discarded_ids and _truthy(row.get("would_allow"))
        item = dict(row)
        item["discarded_survivor_research_only"] = discarded
        item["remediated_would_allow"] = _truthy(row.get("would_allow")) and not discarded
        item["remediated_would_block"] = _truthy(row.get("would_block")) or not item["remediated_would_allow"]
        item["operational_action_allowed"] = False
        item["can_be_used_as_signal"] = False
        item["can_be_used_as_veto"] = False
        remediated.append(item)
    return remediated


def _survivor_plan(rows: Sequence[Mapping[str, Any]], discarded_ids: set[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        rule_id = str(row.get("matched_survivor_rule_id") or "")
        if rule_id:
            grouped.setdefault(rule_id, []).append(row)
    plan: list[dict[str, Any]] = []
    for rule_id in sorted(grouped):
        group_rows = grouped[rule_id]
        values = [_safe_float(row.get("pnl")) for row in group_rows]
        action = "DISCARD_SURVIVOR_RESEARCH_ONLY" if rule_id in discarded_ids else "RETAIN_FOR_RESEARCH_REVIEW_ONLY"
        false_positive_count = sum(1 for row in group_rows if _classify(row) == "false_positive")
        positive_discarded = sum(1 for row in group_rows if _truthy(row.get("would_allow")) and _safe_float(row.get("pnl")) >= 0)
        plan.append(
            {
                "survivor_rule_id": rule_id,
                "action": action,
                "trades": len(group_rows),
                "net_pnl": _round(sum(values)),
                "profit_factor": _profit_factor(values),
                "win_rate": _win_rate(values),
                "false_positive_count": false_positive_count,
                "positive_opportunity_count": positive_discarded,
                "research_only": True,
                "can_activate_observer": False,
                "can_promote_rules": False,
            }
        )
    return plan


def _candidate_subfilters(rows: Sequence[Mapping[str, Any]], discarded_ids: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for rule_id in sorted(discarded_ids):
        group_rows = [row for row in rows if str(row.get("matched_survivor_rule_id") or "") == rule_id]
        available_numeric_fields = sorted(
            {
                key
                for row in group_rows
                for key, value in row.items()
                if key not in OUTCOME_FIELDS and isinstance(value, int | float) and not isinstance(value, bool)
            }
        )
        candidates.append(
            {
                "survivor_rule_id": rule_id,
                "scenario": "keep_only_positive_pnl_subsets",
                "status": "blocked",
                "reason": "insufficient_non_outcome_feature_fields" if not available_numeric_fields else "threshold_review_required_research_only",
                "available_numeric_fields": available_numeric_fields,
                "sample_size": len(group_rows),
                "candidate_decision": "NO_ROBUST_REMEDIATION_FOUND",
                "research_only": True,
                "operational_authority": False,
                "can_activate_observer": False,
                "can_promote_rules": False,
                "can_apply_to_freqtrade": False,
                "can_apply_to_risk_manager": False,
            }
        )
    return candidates


def load_remediation_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    impact_report: str | Path | None = None,
    paper_attribution_report: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    impact_payload: Mapping[str, Any] | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    replay_payload: Mapping[str, Any] | None = None,
    contract_payload: Mapping[str, Any] | None = None,
) -> LoadedRemediationInputs:
    """Load remediation inputs from in-memory payloads or explicit local JSON reports."""

    root = Path(project_root).resolve()
    source_paths = {"impact_report": None, "paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None}
    source_sha256 = {"impact_report": None, "paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None}
    if impact_payload is not None:
        return LoadedRemediationInputs(
            impact_report=dict(impact_payload),
            attribution_report=dict(attribution_payload) if attribution_payload is not None else None,
            replay_report=dict(replay_payload) if replay_payload is not None else None,
            source_contract_report=dict(contract_payload) if contract_payload is not None else None,
            input_mode="in_memory_remediation_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    if not allow_runtime_read:
        return LoadedRemediationInputs(
            impact_report=None,
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )

    impact_path = _resolve_path(root, impact_report, DEFAULT_IMPACT_REPORT)
    attribution_path = _resolve_path(root, paper_attribution_report, DEFAULT_ATTRIBUTION_REPORT)
    replay_path = _resolve_path(root, shadow_replay_report, DEFAULT_REPLAY_REPORT)
    contract_path = _resolve_path(root, closed_trades_source_contract, DEFAULT_CONTRACT_REPORT)
    source_paths = {
        "impact_report": _project_relative(impact_path, root),
        "paper_attribution_report": _project_relative(attribution_path, root),
        "shadow_replay_report": _project_relative(replay_path, root),
        "closed_trades_source_contract": _project_relative(contract_path, root),
    }
    source_sha256 = {
        "impact_report": _sha256_file(impact_path),
        "paper_attribution_report": _sha256_file(attribution_path),
        "shadow_replay_report": _sha256_file(replay_path),
        "closed_trades_source_contract": _sha256_file(contract_path),
    }
    if not impact_path.exists():
        return LoadedRemediationInputs(
            impact_report=None,
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_impact_report",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    try:
        return LoadedRemediationInputs(
            impact_report=_read_json(impact_path),
            attribution_report=_read_json(attribution_path) if attribution_path.exists() else None,
            replay_report=_read_json(replay_path) if replay_path.exists() else None,
            source_contract_report=_read_json(contract_path) if contract_path.exists() else None,
            input_mode="runtime_read_requested",
            source_status="ok",
            source_reason="sources_loaded_read_only",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return LoadedRemediationInputs(
            impact_report=None,
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )


def _baseline_summary(impact_report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    impact_summary = impact_report.get("impact_summary")
    summary = impact_summary if isinstance(impact_summary, Mapping) else {}
    computed = _metrics_for_rows(rows)
    return {
        "baseline_total_closed_trades": _safe_int(impact_report.get("total_closed_trades"), default=len(rows)),
        "baseline_attributed_trade_count": _safe_int(impact_report.get("attributed_trade_count"), default=len(rows)),
        "baseline_would_allow_count": _safe_int(impact_report.get("would_allow_count"), default=computed["would_allow_count"]),
        "baseline_would_block_count": _safe_int(impact_report.get("would_block_count"), default=computed["would_block_count"]),
        "baseline_allowed_net_pnl": _round(_safe_float(summary.get("allowed_net_pnl"), default=computed["allowed_net_pnl"] or 0.0)),
        "baseline_blocked_net_pnl": _round(_safe_float(summary.get("blocked_net_pnl"), default=computed["blocked_net_pnl"] or 0.0)),
        "baseline_false_positive_count": _safe_int(summary.get("false_positive_count"), default=computed["false_positive_count"]),
        "baseline_preserved_loss_count": _safe_int(summary.get("preserved_loss_count"), default=computed["preserved_loss_count"]),
        "baseline_missed_opportunity_count": _safe_int(summary.get("missed_opportunity_count"), default=computed["missed_opportunity_count"]),
    }


def compute_survivor_remediation(
    *,
    impact_report: Mapping[str, Any],
    attribution_report: Mapping[str, Any] | None = None,
    replay_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute research-only remediation by discarding negative survivor cohorts."""

    rows = _rows_from_reports(attribution_report, replay_report)
    baseline = _baseline_summary(impact_report, rows)
    discarded_ids = _negative_survivor_ids(impact_report, rows)
    remediated_rows = _remediate_rows(rows, discarded_ids)
    remediated = _metrics_for_rows(remediated_rows, allow_field="remediated_would_allow", block_field="remediated_would_block")
    plan = _survivor_plan(rows, discarded_ids)
    candidate_subfilters = _candidate_subfilters(rows, discarded_ids)
    discarded_plan = [item for item in plan if item["action"] == "DISCARD_SURVIVOR_RESEARCH_ONLY"]
    retained_plan = [item for item in plan if item["action"] != "DISCARD_SURVIVOR_RESEARCH_ONLY"]
    allowed_delta = _round((remediated["allowed_net_pnl"] or 0.0) - (baseline["baseline_allowed_net_pnl"] or 0.0))
    false_positive_reduction = baseline["baseline_false_positive_count"] - remediated["false_positive_count"]
    missed_opportunity_delta = remediated["missed_opportunity_count"] - baseline["baseline_missed_opportunity_count"]
    if discarded_ids and allowed_delta is not None and allowed_delta > 0 and false_positive_reduction > 0:
        remediation_decision = "DISCARD_SURVIVOR_RESEARCH_ONLY"
    elif any(item["reason"] == "threshold_review_required_research_only" for item in candidate_subfilters):
        remediation_decision = "REVIEW_RESEARCH_ONLY"
    else:
        remediation_decision = "NO_ROBUST_REMEDIATION_FOUND"

    return {
        "rows_loaded": len(rows),
        "baseline_summary": baseline,
        "remediation_summary": {
            "scenario": "discard_all_negative_survivors",
            "remediated_would_allow_count": remediated["would_allow_count"],
            "remediated_would_block_count": remediated["would_block_count"],
            "remediated_allowed_net_pnl": remediated["allowed_net_pnl"],
            "remediated_blocked_net_pnl": remediated["blocked_net_pnl"],
            "remediated_allowed_profit_factor": remediated["allowed_profit_factor"],
            "remediated_allowed_win_rate": remediated["allowed_win_rate"],
            "remediated_false_positive_count": remediated["false_positive_count"],
            "remediated_preserved_loss_count": remediated["preserved_loss_count"],
            "remediated_missed_opportunity_count": remediated["missed_opportunity_count"],
            "false_positive_reduction": false_positive_reduction,
            "allowed_net_pnl_delta": allowed_delta,
            "missed_opportunity_delta": missed_opportunity_delta,
            "discarded_survivor_count": len(discarded_plan),
            "retained_survivor_count": len(retained_plan),
            "remediation_decision_research_only": remediation_decision,
        },
        "survivor_remediation_plan": plan,
        "candidate_subfilters": candidate_subfilters,
        "remediation_recommendations": [
            {
                "recommendation": remediation_decision,
                "research_only": True,
                "operational_authority": False,
                "paper_observation_allowed": False,
                "can_activate_observer": False,
                "can_promote_rules": False,
                "summary": "Descartar survivors negativos apenas como hipótese de pesquisa; observer continua bloqueado.",
            }
        ],
    }


def _empty_remediation() -> dict[str, Any]:
    return {
        "rows_loaded": 0,
        "baseline_summary": {
            "baseline_total_closed_trades": 0,
            "baseline_attributed_trade_count": 0,
            "baseline_would_allow_count": 0,
            "baseline_would_block_count": 0,
            "baseline_allowed_net_pnl": 0.0,
            "baseline_blocked_net_pnl": 0.0,
            "baseline_false_positive_count": 0,
            "baseline_preserved_loss_count": 0,
            "baseline_missed_opportunity_count": 0,
        },
        "remediation_summary": {
            "scenario": "discard_all_negative_survivors",
            "remediated_would_allow_count": 0,
            "remediated_would_block_count": 0,
            "remediated_allowed_net_pnl": 0.0,
            "remediated_blocked_net_pnl": 0.0,
            "remediated_false_positive_count": 0,
            "remediated_preserved_loss_count": 0,
            "remediated_missed_opportunity_count": 0,
            "false_positive_reduction": 0,
            "allowed_net_pnl_delta": 0.0,
            "missed_opportunity_delta": 0,
            "discarded_survivor_count": 0,
            "retained_survivor_count": 0,
            "remediation_decision_research_only": "NO_ROBUST_REMEDIATION_FOUND",
        },
        "survivor_remediation_plan": [],
        "candidate_subfilters": [],
        "remediation_recommendations": [],
    }


def _recommended_next_action(remediation: Mapping[str, Any]) -> str:
    summary = remediation.get("remediation_summary", {})
    if not isinstance(summary, Mapping):
        return "manter_em_research_e_reexecutar_com_fontes_validas"
    if _safe_int(summary.get("discarded_survivor_count")) > 0 and _safe_float(summary.get("allowed_net_pnl_delta")) > 0:
        return "descartar_survivors_ruins_apenas_em_research_e_exigir_nova_observacao_passiva_bloqueada"
    return "manter_em_research_sem_observer_e_investigar_subfiltros_com_features_validas"


def build_paper_shadow_survivor_remediation_research_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    impact_report: str | Path | None = None,
    paper_attribution_report: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    impact_payload: Mapping[str, Any] | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    replay_payload: Mapping[str, Any] | None = None,
    contract_payload: Mapping[str, Any] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    loaded = load_remediation_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        impact_report=impact_report,
        paper_attribution_report=paper_attribution_report,
        shadow_replay_report=shadow_replay_report,
        closed_trades_source_contract=closed_trades_source_contract,
        impact_payload=impact_payload,
        attribution_payload=attribution_payload,
        replay_payload=replay_payload,
        contract_payload=contract_payload,
    )
    write_requested = bool(write and not no_write)
    if loaded.source_status == "ok" and loaded.impact_report is not None:
        remediation = compute_survivor_remediation(
            impact_report=loaded.impact_report,
            attribution_report=loaded.attribution_report,
            replay_report=loaded.replay_report,
        )
        remediation_status = "ok" if remediation["rows_loaded"] > 0 else "blocked"
        reason = "paper_shadow_survivor_remediation_computed_research_only"
    else:
        remediation = _empty_remediation()
        remediation_status = "blocked"
        reason = "remediation_requires_explicit_runtime_read_or_in_memory_inputs" if loaded.input_mode == "no_runtime_rows_loaded" else loaded.source_reason

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "remediation_status": remediation_status,
        "remediation_decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "baseline_summary": remediation["baseline_summary"],
        "remediation_summary": remediation["remediation_summary"],
        "survivor_remediation_plan": remediation["survivor_remediation_plan"],
        "candidate_subfilters": remediation["candidate_subfilters"],
        "remediation_recommendations": remediation["remediation_recommendations"],
        "recommended_next_action": _recommended_next_action(remediation),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "gate_summary": _gate_summary(remediation_status),
        "safety_flags": dict(SAFETY_FLAGS),
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_remediation_report(report)
    if write_requested:
        output_path = _resolve_output_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_remediation_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _gate_summary(remediation_status: str) -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
        "remediation_status": remediation_status,
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        "operational_authority": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
        "result_can_be_used_for_operations": False,
    }


def validate_remediation_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH or report.get("remediation_decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    for field in (
        "baseline_summary",
        "remediation_summary",
        "survivor_remediation_plan",
        "candidate_subfilters",
        "remediation_recommendations",
        "gate_summary",
        "write_performed",
    ):
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    baseline = report.get("baseline_summary", {})
    remediation = report.get("remediation_summary", {})
    return "\n".join(
        [
            "# Paper Shadow Survivor Remediation Research V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Remediation status: `{report.get('remediation_status')}`",
            f"- Baseline allowed net PnL: `{baseline.get('baseline_allowed_net_pnl')}`",
            f"- Remediated allowed net PnL: `{remediation.get('remediated_allowed_net_pnl')}`",
            f"- Allowed net PnL delta: `{remediation.get('allowed_net_pnl_delta')}`",
            f"- False positive reduction: `{remediation.get('false_positive_reduction')}`",
            f"- Discarded survivors: `{remediation.get('discarded_survivor_count')}`",
            f"- Missed opportunity delta: `{remediation.get('missed_opportunity_delta')}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "This report is research-only. It cannot activate an observer, apply vetoes, promote rules, change risk or send orders.",
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
