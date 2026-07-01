"""Research-only daily impact report for paper shadow observation attribution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "paper_shadow_observation_daily_impact_report_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_shadow_observation_daily_impact_report_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_shadow_observation_daily_impact_report_v1.md")
DEFAULT_ATTRIBUTION_REPORT = Path("data/reports/paper_closed_trades_shadow_rule_attribution_v1.json")
DEFAULT_REPLAY_REPORT = Path("data/reports/ocr_master_candle_shadow_observation_replay_v1.json")
DEFAULT_CONTRACT_REPORT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.json")

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


@dataclass(frozen=True)
class LoadedImpactInputs:
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


def _day_from_row(row: Mapping[str, Any]) -> str:
    for field in ("close_time", "close_time_utc", "open_time", "open_time_utc"):
        raw = row.get(field)
        if raw not in (None, ""):
            return str(raw)[:10]
    return "unknown"


def _extract_replay_rows(replay_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = replay_report.get("replay_metrics")
    if isinstance(metrics, Mapping):
        rows = metrics.get("replay_rows")
        if isinstance(rows, list):
            return [dict(item) for item in rows if isinstance(item, Mapping)]
        sample = metrics.get("replay_rows_sample")
        count = int(_safe_float(metrics.get("replay_trade_count")))
        if isinstance(sample, list) and len(sample) == count:
            return [dict(item) for item in sample if isinstance(item, Mapping)]
    return []


def _extract_attribution_rows(attribution_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = attribution_report.get("attribution_table")
    if isinstance(rows, list):
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    sample = attribution_report.get("attribution_table_sample")
    count = int(_safe_float(attribution_report.get("attributed_trade_count")))
    if isinstance(sample, list) and len(sample) == count:
        return [dict(item) for item in sample if isinstance(item, Mapping)]
    return []


def _rows_from_reports(
    attribution_report: Mapping[str, Any],
    replay_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    attribution_rows = _extract_attribution_rows(attribution_report)
    if attribution_rows:
        return attribution_rows
    if replay_report is None:
        return []
    replay_rows = _extract_replay_rows(replay_report)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(replay_rows, start=1):
        pnl = _safe_float(row.get("pnl"))
        would_allow = _truthy(row.get("would_allow"))
        would_block = _truthy(row.get("would_block")) or not would_allow
        rows.append(
            {
                "attribution_row_id": f"impact_from_replay_{index:06d}",
                "trade_id": row.get("trade_id"),
                "order_id": row.get("order_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "open_time": row.get("open_time"),
                "close_time": row.get("close_time"),
                "pnl": pnl,
                "would_allow": would_allow,
                "would_block": would_block,
                "matched_survivor_rule_id": row.get("matched_survivor_rule_id"),
                "matched_survivor_expression": row.get("matched_survivor_expression"),
                "expected_value_delta": row.get("expected_value_delta"),
                "attributed": True,
            }
        )
    return rows


def load_impact_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    paper_attribution_report: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    replay_payload: Mapping[str, Any] | None = None,
    contract_payload: Mapping[str, Any] | None = None,
) -> LoadedImpactInputs:
    """Load impact inputs from in-memory payloads or explicit local report files."""

    root = Path(project_root).resolve()
    if attribution_payload is not None:
        return LoadedImpactInputs(
            attribution_report=dict(attribution_payload),
            replay_report=dict(replay_payload) if replay_payload is not None else None,
            source_contract_report=dict(contract_payload) if contract_payload is not None else None,
            input_mode="in_memory_impact_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
            source_paths={"paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None},
            source_sha256={"paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None},
        )
    if not allow_runtime_read:
        return LoadedImpactInputs(
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths={"paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None},
            source_sha256={"paper_attribution_report": None, "shadow_replay_report": None, "closed_trades_source_contract": None},
        )

    attribution_path = _resolve_path(root, paper_attribution_report, DEFAULT_ATTRIBUTION_REPORT)
    replay_path = _resolve_path(root, shadow_replay_report, DEFAULT_REPLAY_REPORT)
    contract_path = _resolve_path(root, closed_trades_source_contract, DEFAULT_CONTRACT_REPORT)
    source_paths = {
        "paper_attribution_report": _project_relative(attribution_path, root),
        "shadow_replay_report": _project_relative(replay_path, root),
        "closed_trades_source_contract": _project_relative(contract_path, root),
    }
    source_sha256 = {
        "paper_attribution_report": _sha256_file(attribution_path),
        "shadow_replay_report": _sha256_file(replay_path),
        "closed_trades_source_contract": _sha256_file(contract_path),
    }
    if not attribution_path.exists():
        return LoadedImpactInputs(
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_paper_attribution_report",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    try:
        attribution = _read_json(attribution_path)
        replay = _read_json(replay_path) if replay_path.exists() else None
        contract = _read_json(contract_path) if contract_path.exists() else None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return LoadedImpactInputs(
            attribution_report=None,
            replay_report=None,
            source_contract_report=None,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            source_paths=source_paths,
            source_sha256=source_sha256,
        )
    return LoadedImpactInputs(
        attribution_report=attribution,
        replay_report=replay,
        source_contract_report=contract,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        source_paths=source_paths,
        source_sha256=source_sha256,
    )


def _classify(row: Mapping[str, Any]) -> str:
    pnl = _safe_float(row.get("pnl"))
    would_allow = _truthy(row.get("would_allow"))
    would_block = _truthy(row.get("would_block")) or not would_allow
    if would_allow and pnl < 0:
        return "false_positive"
    if would_allow and pnl >= 0:
        return "true_positive_allow"
    if would_block and pnl < 0:
        return "preserved_loss"
    if would_block and pnl >= 0:
        return "missed_opportunity"
    return "unclassified"


def _aggregate_rows(rows: Sequence[Mapping[str, Any]], group_fields: Sequence[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "unknown") for field in group_fields)
        groups.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    for key in sorted(groups):
        group_rows = groups[key]
        values = [_safe_float(row.get("pnl")) for row in group_rows]
        allowed = [row for row in group_rows if _truthy(row.get("would_allow"))]
        blocked = [row for row in group_rows if _truthy(row.get("would_block")) or not _truthy(row.get("would_allow"))]
        item = {field: key[index] for index, field in enumerate(group_fields)}
        item.update(
            {
                "trades": len(group_rows),
                "net_pnl": _round(sum(values)),
                "profit_factor": _profit_factor(values),
                "win_rate": _win_rate(values),
                "would_allow_count": len(allowed),
                "would_block_count": len(blocked),
                "false_positive_count": sum(1 for row in group_rows if _classify(row) == "false_positive"),
                "preserved_loss_count": sum(1 for row in group_rows if _classify(row) == "preserved_loss"),
                "missed_opportunity_count": sum(1 for row in group_rows if _classify(row) == "missed_opportunity"),
            }
        )
        output.append(item)
    return output


def _survivor_recommendation(item: Mapping[str, Any]) -> str:
    trades = int(_safe_float(item.get("trades")))
    net_pnl = _safe_float(item.get("net_pnl"))
    false_positive_count = int(_safe_float(item.get("false_positive_count")))
    false_positive_ratio = false_positive_count / trades if trades else 0.0
    profit_factor = item.get("profit_factor")
    pf = _safe_float(profit_factor, default=0.0) if profit_factor is not None else 0.0
    if net_pnl < 0 and false_positive_count >= max(2, math.ceil(trades * 0.25)):
        return "DISCARD_RESEARCH_ONLY"
    if trades < 10 or (net_pnl <= 0 and false_positive_count > 0):
        return "REVIEW_RESEARCH_ONLY"
    if net_pnl > 0 and pf > 1.2 and false_positive_ratio <= 0.35:
        return "KEEP_PASSIVE_OBSERVATION_ONLY"
    return "REVIEW_RESEARCH_ONLY"


def _survivor_breakdown(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    breakdown = _aggregate_rows(rows, ("matched_survivor_rule_id",))
    for item in breakdown:
        item["survivor_rule_id"] = item.pop("matched_survivor_rule_id")
        item["recommendation"] = _survivor_recommendation(item)
        item["research_only"] = True
        item["can_activate_observer"] = False
        item["can_promote_rules"] = False
    return breakdown


def compute_impact_report(
    *,
    attribution_report: Mapping[str, Any],
    replay_report: Mapping[str, Any] | None = None,
    source_contract_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute impact metrics from paper attribution/replay rows."""

    rows = _rows_from_reports(attribution_report, replay_report)
    allowed = [row for row in rows if _truthy(row.get("would_allow"))]
    blocked = [row for row in rows if _truthy(row.get("would_block")) or not _truthy(row.get("would_allow"))]
    false_positive = [row for row in rows if _classify(row) == "false_positive"]
    preserved_loss = [row for row in rows if _classify(row) == "preserved_loss"]
    missed_opportunity = [row for row in rows if _classify(row) == "missed_opportunity"]
    true_positive = [row for row in rows if _classify(row) == "true_positive_allow"]
    allowed_values = [_safe_float(row.get("pnl")) for row in allowed]
    blocked_values = [_safe_float(row.get("pnl")) for row in blocked]
    all_values = [_safe_float(row.get("pnl")) for row in rows]
    ev_values = [
        _safe_float(row.get("expected_value_delta"))
        for row in rows
        if row.get("expected_value_delta") not in (None, "")
    ]
    daily_rows = [dict(row, impact_day=_day_from_row(row)) for row in rows]
    survivor_breakdown = _survivor_breakdown([row for row in rows if row.get("matched_survivor_rule_id") not in (None, "")])
    worst_survivors = sorted(survivor_breakdown, key=lambda item: (_safe_float(item.get("net_pnl")), -_safe_float(item.get("false_positive_count"))))[:10]
    best_survivors = sorted(survivor_breakdown, key=lambda item: (_safe_float(item.get("net_pnl")), _safe_float(item.get("profit_factor"))), reverse=True)[:10]
    return {
        "total_closed_trades": int(_safe_float(attribution_report.get("closed_trade_count"), default=len(rows))) if rows else int(_safe_float(attribution_report.get("closed_trade_count"))),
        "attributed_trade_count": int(_safe_float(attribution_report.get("attributed_trade_count"), default=len(rows))) if rows else int(_safe_float(attribution_report.get("attributed_trade_count"))),
        "unattributed_trade_count": int(_safe_float(attribution_report.get("unattributed_trade_count"))),
        "would_allow_count": len(allowed) if rows else int(_safe_float(attribution_report.get("would_allow_count"))),
        "would_block_count": len(blocked) if rows else int(_safe_float(attribution_report.get("would_block_count"))),
        "impact_summary": {
            "allowed_net_pnl": _round(sum(allowed_values)),
            "blocked_net_pnl": _round(sum(blocked_values)),
            "baseline_net_pnl": _round(sum(all_values)) if rows else _safe_float(_nested(replay_report, ("replay_metrics", "baseline_net_pnl"))),
            "false_positive_count": len(false_positive),
            "false_positive_net_pnl": _round(sum(_safe_float(row.get("pnl")) for row in false_positive)),
            "preserved_loss_count": len(preserved_loss),
            "preserved_loss_net_pnl": _round(sum(_safe_float(row.get("pnl")) for row in preserved_loss)),
            "missed_opportunity_count": len(missed_opportunity),
            "missed_opportunity_net_pnl": _round(sum(_safe_float(row.get("pnl")) for row in missed_opportunity)),
            "true_positive_allow_count": len(true_positive),
            "expected_value_delta_total": _round(sum(ev_values)),
            "expected_value_delta_mean": _round(sum(ev_values) / len(ev_values)) if ev_values else None,
            "allowed_profit_factor": _profit_factor(allowed_values),
            "blocked_profit_factor": _profit_factor(blocked_values),
            "allowed_win_rate": _win_rate(allowed_values),
            "blocked_win_rate": _win_rate(blocked_values),
        },
        "daily_breakdown": _aggregate_rows(daily_rows, ("impact_day",)),
        "symbol_side_breakdown": _aggregate_rows(rows, ("symbol", "side")),
        "survivor_rule_breakdown": survivor_breakdown,
        "worst_survivors": worst_survivors,
        "best_survivors": best_survivors,
        "survivor_recommendations": [
            {
                "survivor_rule_id": item["survivor_rule_id"],
                "recommendation": item["recommendation"],
                "research_only": True,
                "can_activate_observer": False,
                "can_promote_rules": False,
            }
            for item in survivor_breakdown
        ],
        "source_contract_summary": _source_contract_summary(source_contract_report),
    }


def _nested(payload: Mapping[str, Any] | None, path: Sequence[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _source_contract_summary(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {"present": False}
    return {
        "present": True,
        "source_contract_status": report.get("source_contract_status"),
        "normalized_closed_trade_count": report.get("normalized_closed_trade_count"),
        "recommended_join_key": report.get("recommended_join_key"),
    }


def _recommended_next_action(impact: Mapping[str, Any]) -> str:
    recommendations = impact.get("survivor_recommendations", [])
    if not recommendations:
        return "manter_em_research_e_reexecutar_quando_houver_attribution_rows_completas"
    keep_count = sum(1 for item in recommendations if item.get("recommendation") == "KEEP_PASSIVE_OBSERVATION_ONLY")
    discard_count = sum(1 for item in recommendations if item.get("recommendation") == "DISCARD_RESEARCH_ONLY")
    if discard_count > keep_count:
        return "descartar_survivors_negativos_em_research_e_manter_observer_bloqueado"
    return "manter_survivors_elegiveis_apenas_em_observacao_passiva_research_sem_liberar_observer"


def build_paper_shadow_observation_daily_impact_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    paper_attribution_report: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    attribution_payload: Mapping[str, Any] | None = None,
    replay_payload: Mapping[str, Any] | None = None,
    contract_payload: Mapping[str, Any] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    loaded = load_impact_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        paper_attribution_report=paper_attribution_report,
        shadow_replay_report=shadow_replay_report,
        closed_trades_source_contract=closed_trades_source_contract,
        attribution_payload=attribution_payload,
        replay_payload=replay_payload,
        contract_payload=contract_payload,
    )
    write_requested = bool(write and not no_write)
    if loaded.source_status == "ok" and loaded.attribution_report is not None:
        impact = compute_impact_report(
            attribution_report=loaded.attribution_report,
            replay_report=loaded.replay_report,
            source_contract_report=loaded.source_contract_report,
        )
        reason = "paper_shadow_observation_daily_impact_computed_research_only"
        impact_status = "ok" if impact["attributed_trade_count"] > 0 else "blocked"
    else:
        impact = _empty_impact()
        reason = "daily_impact_report_requires_explicit_runtime_read_or_in_memory_inputs" if loaded.input_mode == "no_runtime_rows_loaded" else loaded.source_reason
        impact_status = "blocked"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "impact_report_status": impact_status,
        "impact_report_decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "total_closed_trades": impact["total_closed_trades"],
        "attributed_trade_count": impact["attributed_trade_count"],
        "unattributed_trade_count": impact["unattributed_trade_count"],
        "would_allow_count": impact["would_allow_count"],
        "would_block_count": impact["would_block_count"],
        "daily_breakdown_count": len(impact["daily_breakdown"]),
        "impact_summary": impact["impact_summary"],
        "daily_breakdown": impact["daily_breakdown"],
        "symbol_side_breakdown": impact["symbol_side_breakdown"],
        "survivor_rule_breakdown": impact["survivor_rule_breakdown"],
        "worst_survivors": impact["worst_survivors"],
        "best_survivors": impact["best_survivors"],
        "survivor_recommendations": impact["survivor_recommendations"],
        "source_contract_summary": impact["source_contract_summary"],
        "recommended_next_action": _recommended_next_action(impact),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "gate_summary": _gate_summary(impact_status),
        "safety_flags": dict(SAFETY_FLAGS),
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_daily_impact_report(report)
    if write_requested:
        output_path = _resolve_output_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_daily_impact_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _empty_impact() -> dict[str, Any]:
    return {
        "total_closed_trades": 0,
        "attributed_trade_count": 0,
        "unattributed_trade_count": 0,
        "would_allow_count": 0,
        "would_block_count": 0,
        "impact_summary": {
            "allowed_net_pnl": 0.0,
            "blocked_net_pnl": 0.0,
            "baseline_net_pnl": 0.0,
            "false_positive_count": 0,
            "false_positive_net_pnl": 0.0,
            "preserved_loss_count": 0,
            "preserved_loss_net_pnl": 0.0,
            "missed_opportunity_count": 0,
            "missed_opportunity_net_pnl": 0.0,
            "true_positive_allow_count": 0,
            "expected_value_delta_total": 0.0,
            "expected_value_delta_mean": None,
            "allowed_profit_factor": None,
            "blocked_profit_factor": None,
            "allowed_win_rate": None,
            "blocked_win_rate": None,
        },
        "daily_breakdown": [],
        "symbol_side_breakdown": [],
        "survivor_rule_breakdown": [],
        "worst_survivors": [],
        "best_survivors": [],
        "survivor_recommendations": [],
        "source_contract_summary": {"present": False},
    }


def _gate_summary(impact_status: str) -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
        "impact_report_status": impact_status,
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


def validate_daily_impact_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH or report.get("impact_report_decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    for field in (
        "status",
        "reason",
        "impact_summary",
        "daily_breakdown",
        "symbol_side_breakdown",
        "survivor_rule_breakdown",
        "survivor_recommendations",
        "gate_summary",
        "write_performed",
    ):
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    summary = report.get("impact_summary", {})
    return "\n".join(
        [
            "# Paper Shadow Observation Daily Impact Report V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Impact status: `{report.get('impact_report_status')}`",
            f"- Total closed trades: `{report.get('total_closed_trades')}`",
            f"- Attributed trades: `{report.get('attributed_trade_count')}`",
            f"- Would allow count: `{summary.get('true_positive_allow_count', 0) + summary.get('false_positive_count', 0)}`",
            f"- Allowed net PnL: `{summary.get('allowed_net_pnl')}`",
            f"- Blocked net PnL: `{summary.get('blocked_net_pnl')}`",
            f"- False positives: `{summary.get('false_positive_count')}`",
            f"- Preserved losses: `{summary.get('preserved_loss_count')}`",
            f"- Missed opportunities: `{summary.get('missed_opportunity_count')}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "This report is research-only and cannot activate observer, veto trades, promote survivors, change risk or send orders.",
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
