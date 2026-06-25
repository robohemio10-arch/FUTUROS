"""Research-only daily mistake and winner catalog."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_paper_master_kpi_pack import calculate_trade_kpis


DAILY_MISTAKE_WINNER_CATALOG_SCHEMA_VERSION = "daily_mistake_winner_catalog_v1"
MAX_CATALOG_SAMPLE = 20

CATALOG_SCOPE: dict[str, bool] = {
    "classifies_winners": True,
    "classifies_mistakes": True,
    "uses_net_pnl_as_label": True,
    "uses_net_pnl_as_feature": False,
    "uses_future_data": False,
    "mines_patterns": False,
    "registers_candidate_rules": False,
    "runs_oos_validation": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
    "writes_reports": False,
}

READINESS_POLICY: dict[str, bool] = {
    "catalog_is_not_readiness_evidence": True,
    "catalog_outputs_do_not_release_live": True,
    "catalog_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar pattern mining research em branch futura",
    "criar candidate shadow rule registry em branch futura",
    "criar OOS validation em branch futura",
    "criar AI Shadow feedback bridge em branch futura",
    "criar Qlib research dataset em branch futura",
]

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
    "usar catalogo para liberar operacao",
    "promover regra candidata",
    "promover modelo",
    "criar regras candidatas nesta branch",
    "minerar padroes nesta branch",
    "rodar OOS validation nesta branch",
]


def build_daily_mistake_winner_catalog_report(
    project_root: str | Path | None = None,
    trades: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    alignment_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a blocked research-only mistake/winner catalog report."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    trade_rows = [] if trades is None else list(trades)
    input_mode = "no_runtime_rows_loaded" if trades is None else "in_memory_catalog_inputs"
    catalog = build_mistake_winner_catalog(trade_rows, feature_rows, alignment_summary)
    payload: dict[str, Any] = {
        "schema_version": DAILY_MISTAKE_WINNER_CATALOG_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "mistake_winner_catalog_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "input_mode": input_mode,
        "catalog": catalog,
        "catalog_summary": summarize_catalog(catalog["catalog_entries"]),
        "trade_kpis": calculate_trade_kpis(trade_rows),
        "catalog_scope": dict(CATALOG_SCOPE),
        "readiness_policy": dict(READINESS_POLICY),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "write_requested": False,
        "write_performed": False,
    }
    payload["validation_errors"] = validate_daily_mistake_winner_catalog_report(payload)
    return payload


def classify_trade_outcome(
    trade: Mapping[str, Any],
    feature_row: Mapping[str, Any] | None = None,
    alignment_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one trade descriptively for research cataloging."""
    trade_id = str(trade.get("trade_id") or "unknown_trade")
    pnl = _to_float(trade.get("net_pnl"))
    evidence: list[str] = []
    severity = "none"
    confidence = 0.0
    if pnl is None:
        classification = "insufficient_evidence"
        subclassification = "missing_pnl"
        evidence.append("missing_numeric_net_pnl")
        confidence = 0.2
    elif pnl > 0:
        classification = "winner"
        subclassification = "profitable_trade"
        evidence.append("positive_net_pnl_label")
        if _positive_pre_entry_momentum(feature_row):
            evidence.append("positive_pre_entry_momentum")
        confidence = 0.8
    elif pnl == 0:
        classification = "neutral"
        subclassification = "flat_trade"
        evidence.append("zero_net_pnl_label")
        confidence = 0.7
    else:
        classification = "mistake"
        subclassification = _loss_subclassification(trade)
        evidence.append("negative_net_pnl_label")
        if "stop" in str(trade.get("exit_reason") or "").lower():
            evidence.append("stop_exit_reason")
        if _duration_under_30m(trade):
            evidence.append("fast_loss_under_30m")
        if _overextended_entry(feature_row):
            evidence.append("overextended_entry_rsi")
        if _weak_pre_entry_momentum(feature_row):
            evidence.append("weak_pre_entry_momentum")
        severity = _loss_severity(evidence)
        confidence = 0.75 if len(evidence) > 1 else 0.55
    evidence.extend(_alignment_evidence(trade_id, alignment_context))
    return {
        "trade_id": trade_id,
        "classification": classification,
        "subclassification": subclassification,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence[:10],
        "symbol": _normalize_symbol(trade.get("symbol")),
        "side": _normalize_side(trade.get("side")),
        "uses_future_data": False,
        "uses_net_pnl_as_label": True,
        "uses_net_pnl_as_feature": False,
        "creates_candidate_rule": False,
        "operational_action_allowed": False,
    }


def build_mistake_winner_catalog(
    trades: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    alignment_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic catalog from in-memory rows."""
    features_by_trade_id = _features_by_trade_id(feature_rows)
    entries = [
        classify_trade_outcome(
            trade,
            features_by_trade_id.get(str(trade.get("trade_id") or f"trade_{index}")),
            alignment_summary,
        )
        for index, trade in enumerate(trades)
    ]
    summary = summarize_catalog(entries)
    return {
        "entry_count": summary["entry_count"],
        "winner_count": summary["classification_counts"].get("winner", 0),
        "mistake_count": summary["classification_counts"].get("mistake", 0),
        "neutral_count": summary["classification_counts"].get("neutral", 0),
        "insufficient_evidence_count": summary["classification_counts"].get(
            "insufficient_evidence",
            0,
        ),
        "catalog_entries": entries,
        "catalog_entries_sample": entries[:MAX_CATALOG_SAMPLE],
        "classification_counts": summary["classification_counts"],
        "subclassification_counts": summary["subclassification_counts"],
        "severity_counts": summary["severity_counts"],
        "symbol_summary": summary["symbol_summary"],
        "side_summary": summary["side_summary"],
        "catalog_scope": dict(CATALOG_SCOPE),
    }


def summarize_catalog(
    catalog_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize catalog entries by class, subclass, severity, symbol and side."""
    classification_counts = _counter(catalog_entries, "classification")
    subclassification_counts = _counter(catalog_entries, "subclassification")
    severity_counts = _counter(catalog_entries, "severity")
    symbol_summary = _summary_by_field(catalog_entries, "symbol")
    side_summary = _summary_by_field(catalog_entries, "side")
    return {
        "entry_count": len(catalog_entries),
        "classification_counts": classification_counts,
        "subclassification_counts": subclassification_counts,
        "severity_counts": severity_counts,
        "symbol_summary": symbol_summary,
        "side_summary": side_summary,
    }


def validate_daily_mistake_winner_catalog_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the mistake/winner catalog report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_MISTAKE_WINNER_CATALOG_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "mistake_winner_catalog_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("catalog_scope"))
    for key, expected in CATALOG_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"catalog_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    if not isinstance(payload.get("catalog"), Mapping):
        errors.append("catalog_must_be_object")
    if not isinstance(payload.get("catalog_summary"), Mapping):
        errors.append("catalog_summary_must_be_object")
    return errors


def _features_by_trade_id(
    feature_rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in feature_rows or ():
        trade_id = row.get("trade_id")
        if trade_id is not None:
            result[str(trade_id)] = row
    return result


def _loss_subclassification(trade: Mapping[str, Any]) -> str:
    if "stop" in str(trade.get("exit_reason") or "").lower():
        return "stop_loss_loss"
    if _duration_under_30m(trade):
        return "fast_loss"
    return "unclassified_loss"


def _duration_under_30m(trade: Mapping[str, Any]) -> bool:
    duration = _to_float(trade.get("duration_minutes"))
    return duration is not None and duration < 30


def _overextended_entry(feature_row: Mapping[str, Any] | None) -> bool:
    rsi = _to_float(_mapping(feature_row).get("rsi_14"))
    return rsi is not None and rsi >= 70


def _weak_pre_entry_momentum(feature_row: Mapping[str, Any] | None) -> bool:
    row = _mapping(feature_row)
    lb_10 = _to_float(row.get("lb_10m_ret_close"))
    lb_30 = _to_float(row.get("lb_30m_ret_close"))
    return lb_10 is not None and lb_30 is not None and lb_10 < 0 and lb_30 < 0


def _positive_pre_entry_momentum(feature_row: Mapping[str, Any] | None) -> bool:
    row = _mapping(feature_row)
    values = [
        _to_float(row.get("lb_5m_ret_close")),
        _to_float(row.get("lb_10m_ret_close")),
    ]
    present = [value for value in values if value is not None]
    return bool(present) and all(value > 0 for value in present)


def _loss_severity(evidence: Sequence[str]) -> str:
    weighted = {
        "stop_exit_reason",
        "fast_loss_under_30m",
        "overextended_entry_rsi",
        "weak_pre_entry_momentum",
    }
    count = sum(1 for item in evidence if item in weighted)
    if count >= 3:
        return "high"
    if count == 2:
        return "medium"
    return "low"


def _alignment_evidence(
    trade_id: str,
    alignment_context: Mapping[str, Any] | None,
) -> list[str]:
    context = _mapping(alignment_context)
    evidence_by_trade_id = _mapping(context.get("evidence_by_trade_id"))
    raw = evidence_by_trade_id.get(trade_id)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        return [str(item) for item in raw][:5]
    return []


def _counter(entries: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for entry in entries:
        value = entry.get(key)
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def _summary_by_field(
    entries: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, dict[str, int]]:
    result: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in entries:
        value = entry.get(field)
        classification = entry.get("classification")
        if value is None or value == "" or classification is None:
            continue
        result[str(value)][str(classification)] += 1
    return {
        key: dict(sorted(counter.items()))
        for key, counter in sorted(result.items())
    }


def _normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return None


def _normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("/", "")
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
