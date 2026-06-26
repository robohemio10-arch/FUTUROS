"""Research-only Qlib dataset builder for the SMART FUTUROS Daily Learning loop.

This module materializes an in-memory dataset contract for research. It does not
load runtime sources, write project data, update Qlib runtime state, train models,
or grant operational authority.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

DAILY_LEARNING_QLIB_RESEARCH_DATASET_SCHEMA_VERSION = (
    "daily_learning_qlib_research_dataset_v1"
)
DEFAULT_SAMPLE_LIMIT = 20

_NUMERIC_FEATURE_NAMES = {
    "has_entry_candle",
    "max_lookback_covered",
    "entry_close",
    "entry_open",
    "entry_high",
    "entry_low",
    "entry_volume",
    "entry_return_1_candle",
    "sma_20",
    "dist_sma_20_pct",
    "rsi_14",
    "pre_entry_volatility_20",
    "lb_5m_candle_count",
    "lb_5m_expected_candle_count",
    "lb_5m_coverage_ratio",
    "lb_5m_ret_close",
    "lb_5m_high_low_range_pct",
    "lb_5m_volume_sum",
    "lb_10m_candle_count",
    "lb_10m_expected_candle_count",
    "lb_10m_coverage_ratio",
    "lb_10m_ret_close",
    "lb_10m_high_low_range_pct",
    "lb_10m_volume_sum",
    "lb_30m_candle_count",
    "lb_30m_expected_candle_count",
    "lb_30m_coverage_ratio",
    "lb_30m_ret_close",
    "lb_30m_high_low_range_pct",
    "lb_30m_volume_sum",
}

_FORBIDDEN_FEATURE_FRAGMENTS = (
    "pnl",
    "profit",
    "loss",
    "winner",
    "mistake",
    "classification",
    "subclassification",
    "label",
    "target",
    "oos_status",
    "feedback",
    "future",
)

_REQUIRED_FEEDBACK_EVENT_BLOCKERS = (
    "research_only_feedback",
    "not_reviewed_by_operator",
    "not_bound_to_ai_shadow_runtime_contract",
    "not_approved_for_ai_shadow_runtime",
    "not_approved_for_freqtrade",
    "not_approved_for_risk_manager",
    "not_gap_free_soak_validated",
    "live_canary_blocked",
)


def _safe_str(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "sim"}:
            return True
        if text in {"0", "false", "no", "n", "nao", "não"}:
            return False
    return None


def _stable_id(prefix: str, index: int, trade_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in trade_id)
    clean = clean.strip("_") or f"row_{index:04d}"
    return f"{prefix}_{index:04d}_{clean}"


def _as_list(items: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if items is None:
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _index_by_key(
    items: Sequence[Mapping[str, Any]], key: str
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if value is not None:
            indexed[_safe_str(value)] = item
    return indexed


def _collect_trade_ids(
    catalog_entries: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in list(feature_rows) + list(catalog_entries):
        value = item.get("trade_id")
        if value is None:
            continue
        trade_id = _safe_str(value)
        if trade_id not in seen:
            ordered.append(trade_id)
            seen.add(trade_id)
    if ordered:
        return ordered
    fallback_count = max(len(catalog_entries), len(feature_rows))
    return [f"synthetic_{index:04d}" for index in range(fallback_count)]


def _feature_allowed(name: str) -> bool:
    normalized = name.lower()
    if normalized not in _NUMERIC_FEATURE_NAMES:
        return False
    return not any(fragment in normalized for fragment in _FORBIDDEN_FEATURE_FRAGMENTS)


def _extract_pre_entry_features(feature_row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not feature_row:
        return {}
    features: dict[str, Any] = {}
    for source_name in sorted(_NUMERIC_FEATURE_NAMES):
        if not _feature_allowed(source_name):
            continue
        if source_name not in feature_row:
            continue
        value = feature_row.get(source_name)
        bool_value = _safe_bool(value)
        if source_name == "has_entry_candle" and bool_value is not None:
            features[f"feature_{source_name}"] = int(bool_value)
            continue
        number = _safe_float(value)
        if number is not None:
            features[f"feature_{source_name}"] = number
    side = _safe_str(feature_row.get("side"), "unknown").lower()
    symbol = _safe_str(feature_row.get("symbol"), "unknown").upper()
    features["feature_side_long"] = 1 if side in {"long", "buy"} else 0
    features["feature_side_short"] = 1 if side in {"short", "sell"} else 0
    features["feature_symbol_hash_bucket"] = sum(ord(ch) for ch in symbol) % 997
    return features


def _build_labels(catalog_entry: Mapping[str, Any] | None) -> dict[str, Any]:
    classification = _safe_str(
        catalog_entry.get("classification") if catalog_entry else None,
        "insufficient_evidence",
    ).lower()
    subclassification = _safe_str(
        catalog_entry.get("subclassification") if catalog_entry else None,
        "unknown",
    ).lower()
    severity = _safe_str(
        catalog_entry.get("severity") if catalog_entry else None,
        "unknown",
    ).lower()
    return {
        "label_classification": classification,
        "label_subclassification": subclassification,
        "label_severity": severity,
        "label_is_winner": 1 if classification == "winner" else 0,
        "label_is_mistake": 1 if classification == "mistake" else 0,
        "label_is_neutral": 1 if classification == "neutral" else 0,
        "label_has_insufficient_evidence": 1
        if classification == "insufficient_evidence"
        else 0,
    }


def _build_metadata(
    trade_id: str,
    catalog_entry: Mapping[str, Any] | None,
    feature_row: Mapping[str, Any] | None,
    index: int,
) -> dict[str, Any]:
    source = catalog_entry or feature_row or {}
    return {
        "row_id": _stable_id("qlib_research", index, trade_id),
        "trade_id": trade_id,
        "symbol": _safe_str(source.get("symbol"), "unknown").upper(),
        "side": _safe_str(source.get("side"), "unknown").lower(),
        "source": _safe_str(source.get("source"), "unknown").lower(),
        "entry_time": _safe_str(
            source.get("entry_time") or source.get("open_time"), "unknown"
        ),
        "dataset_row_status": "research_only_not_for_training",
        "qlib_runtime_update_allowed": False,
        "training_allowed": False,
        "model_promotion_allowed": False,
    }


def build_qlib_dataset_row(
    catalog_entry: Mapping[str, Any] | None = None,
    feature_row: Mapping[str, Any] | None = None,
    index: int = 0,
) -> dict[str, Any]:
    """Build one research-only row with explicit feature/label separation."""
    trade_id = _safe_str(
        (feature_row or {}).get("trade_id") or (catalog_entry or {}).get("trade_id"),
        f"synthetic_{index:04d}",
    )
    row: dict[str, Any] = {}
    row.update(_build_metadata(trade_id, catalog_entry, feature_row, index))
    row.update(_extract_pre_entry_features(feature_row))
    row.update(_build_labels(catalog_entry))
    row["uses_net_pnl_as_feature"] = False
    row["uses_outcome_as_feature"] = False
    row["uses_future_data"] = False
    row["writes_runtime"] = False
    row["writes_data"] = False
    return row


def separate_feature_label_columns(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    features: set[str] = set()
    labels: set[str] = set()
    metadata: set[str] = set()
    for row in rows:
        for key in row:
            if key.startswith("feature_"):
                features.add(key)
            elif key.startswith("label_"):
                labels.add(key)
            else:
                metadata.add(key)
    return {
        "feature_columns": sorted(features),
        "label_columns": sorted(labels),
        "metadata_columns": sorted(metadata),
    }


def summarize_dataset_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    label_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    feature_presence: Counter[str] = Counter()
    for row in rows:
        label_counts[_safe_str(row.get("label_classification"))] += 1
        symbol_counts[_safe_str(row.get("symbol"))] += 1
        side_counts[_safe_str(row.get("side"))] += 1
        for key, value in row.items():
            if key.startswith("feature_") and value is not None:
                feature_presence[key] += 1
    return {
        "row_count": len(rows),
        "classification_counts": dict(sorted(label_counts.items())),
        "symbol_counts": dict(sorted(symbol_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "feature_presence_counts": dict(sorted(feature_presence.items())),
    }


def _summarize_research_context(
    oos_validation_results: Sequence[Mapping[str, Any]],
    feedback_events: Sequence[Mapping[str, Any]],
    candidate_rules: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    oos_counts = Counter(_safe_str(item.get("oos_status")) for item in oos_validation_results)
    feedback_counts = Counter(_safe_str(item.get("feedback_type")) for item in feedback_events)
    rule_kind_counts = Counter(_safe_str(item.get("rule_kind")) for item in candidate_rules)
    return {
        "oos_validation_result_count": len(oos_validation_results),
        "feedback_event_count": len(feedback_events),
        "candidate_rule_count": len(candidate_rules),
        "oos_status_counts": dict(sorted(oos_counts.items())),
        "feedback_type_counts": dict(sorted(feedback_counts.items())),
        "candidate_rule_kind_counts": dict(sorted(rule_kind_counts.items())),
        "context_used_as_features": False,
        "context_used_for_training": False,
    }


def build_qlib_research_dataset(
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    oos_validation_results: Sequence[Mapping[str, Any]] | None = None,
    feedback_events: Sequence[Mapping[str, Any]] | None = None,
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Qlib research dataset from in-memory Daily Learning artifacts."""
    catalog = _as_list(catalog_entries)
    features = _as_list(feature_rows)
    oos_results = _as_list(oos_validation_results)
    feedback = _as_list(feedback_events)
    candidates = _as_list(candidate_rules)

    catalog_by_trade_id = _index_by_key(catalog, "trade_id")
    features_by_trade_id = _index_by_key(features, "trade_id")
    trade_ids = _collect_trade_ids(catalog, features)

    rows = [
        build_qlib_dataset_row(
            catalog_entry=catalog_by_trade_id.get(trade_id),
            feature_row=features_by_trade_id.get(trade_id),
            index=index,
        )
        for index, trade_id in enumerate(trade_ids)
    ]
    columns = separate_feature_label_columns(rows)
    feature_columns = columns["feature_columns"]
    label_columns = columns["label_columns"]

    dataset_scope = {
        "builds_qlib_research_dataset": True,
        "research_dataset_only": True,
        "uses_only_in_memory_inputs": True,
        "separates_features_and_labels": True,
        "uses_net_pnl_as_feature": False,
        "uses_outcome_as_feature": False,
        "uses_future_data": False,
        "runs_training": False,
        "updates_qlib_runtime": False,
        "updates_models": False,
        "promotes_model": False,
        "updates_ai_shadow_runtime": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "writes_reports": False,
        "writes_runtime": False,
        "writes_data": False,
    }
    return {
        "dataset_row_count": len(rows),
        "qlib_rows": rows,
        "qlib_rows_sample": rows[:DEFAULT_SAMPLE_LIMIT],
        "feature_columns": feature_columns,
        "label_columns": label_columns,
        "metadata_columns": columns["metadata_columns"],
        "feature_count": len(feature_columns),
        "label_count": len(label_columns),
        "dataset_summary": summarize_dataset_rows(rows),
        "research_context_summary": _summarize_research_context(
            oos_results, feedback, candidates
        ),
        "dataset_scope": dataset_scope,
        "dataset_quality_notes": [
            "research_dataset_only",
            "features_are_pre_entry_only_when_provided",
            "labels_are_separated_from_feature_columns",
            "net_pnl_is_not_a_feature",
            "does_not_train_model",
            "does_not_update_qlib_runtime",
        ],
    }


def _safety_flags() -> dict[str, Any]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "read_only": True,
        "operational_authority": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "live_release_allowed": False,
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
        "updates_ai_shadow_thresholds": False,
        "updates_ai_shadow_policy": False,
        "writes_runtime": False,
        "writes_data": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_ai_shadow_sqlite": False,
        "runs_training": False,
        "runs_ocr": False,
        "runs_ai_shadow_incremental": False,
        "applies_shadow_rules": False,
        "promotes_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
    }


def build_daily_learning_qlib_research_dataset_report(
    project_root: str | Path | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    oos_validation_results: Sequence[Mapping[str, Any]] | None = None,
    feedback_events: Sequence[Mapping[str, Any]] | None = None,
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the blocked research-only Qlib dataset report."""
    all_inputs_none = all(
        value is None
        for value in (
            catalog_entries,
            feature_rows,
            oos_validation_results,
            feedback_events,
            candidate_rules,
        )
    )
    input_mode = "no_runtime_rows_loaded" if all_inputs_none else "in_memory_inputs"
    dataset = build_qlib_research_dataset(
        catalog_entries=catalog_entries,
        feature_rows=feature_rows,
        oos_validation_results=oos_validation_results,
        feedback_events=feedback_events,
        candidate_rules=candidate_rules,
    )
    report: dict[str, Any] = {
        "schema_version": DAILY_LEARNING_QLIB_RESEARCH_DATASET_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "project_root": str(project_root) if project_root is not None else None,
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "qlib_research_dataset_without_runtime_or_training_authority",
        "input_mode": input_mode,
        "qlib_research_dataset": dataset,
        "dataset_summary": dataset["dataset_summary"],
        "dataset_scope": dataset["dataset_scope"],
        "readiness_policy": {
            "qlib_research_dataset_is_not_readiness_evidence": True,
            "qlib_research_dataset_outputs_do_not_release_live": True,
            "qlib_research_dataset_outputs_do_not_release_canary": True,
            "manual_go_no_go_required": True,
            "model_training_requires_separate_branch": True,
            "model_promotion_requires_separate_registry_and_oos_review": True,
            "thirty_day_gap_free_soak_required_for_future_canary_review": True,
        },
        "allowed_next_steps": [
            "criar daily learning orchestrator em branch futura",
            "criar scheduler paper em branch futura",
            "criar dashboard daily learning command center em branch futura",
            "criar evidence readiness integration em branch futura",
            "criar treinamento Qlib research-only em branch futura",
        ],
        "forbidden_actions": [
            "alterar Freqtrade",
            "alterar RiskManager",
            "alterar Qlib runtime",
            "alterar IA Shadow runtime",
            "alterar modelos",
            "alterar datasets operacionais",
            "habilitar live",
            "habilitar canary",
            "enviar ordem real",
            "usar exchange privada",
            "escrever artefatos em data/runtime/reports/logs/freqtrade",
            "usar dataset Qlib para liberar operacao",
            "treinar modelo nesta branch",
            "promover modelo",
            "promover regra candidata",
            "usar outcome como feature",
        ],
        "operator_decision": {
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
            "qlib_runtime_update_allowed": False,
            "training_allowed": False,
            "model_promotion_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "ai_shadow_feedback_application_allowed": False,
        },
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
    }
    report.update(_safety_flags())
    report["validation_errors"] = validate_daily_learning_qlib_research_dataset_report(
        report
    )
    return report


def _feature_columns_are_safe(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    dataset = payload.get("qlib_research_dataset", {})
    feature_columns = dataset.get("feature_columns", []) if isinstance(dataset, Mapping) else []
    if not isinstance(feature_columns, Sequence):
        return ["feature_columns_must_be_sequence"]
    for column in feature_columns:
        name = _safe_str(column).lower()
        if not name.startswith("feature_"):
            errors.append(f"feature_column_without_feature_prefix:{column}")
        for fragment in _FORBIDDEN_FEATURE_FRAGMENTS:
            if fragment in name:
                errors.append(f"unsafe_feature_column:{column}")
    return errors


def validate_daily_learning_qlib_research_dataset_report(
    payload: Mapping[str, Any]
) -> list[str]:
    """Return validation errors for an unsafe or malformed report."""
    errors: list[str] = []
    expected_true = (
        "research_only",
        "paper_only",
        "shadow_only",
        "read_only",
    )
    expected_false = (
        "operational_authority",
        "can_apply_to_freqtrade",
        "can_apply_to_risk_manager",
        "can_promote_rules",
        "can_promote_model",
        "live_trading_enabled",
        "canary_release_allowed",
        "live_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "writes_runtime",
        "writes_data",
        "writes_sqlite",
        "writes_parquet",
        "runs_training",
        "runs_ocr",
        "runs_ai_shadow_incremental",
        "applies_shadow_rules",
        "promotes_shadow_rules",
        "applies_feedback_to_ai_shadow",
    )
    for key in expected_true:
        if payload.get(key) is not True:
            errors.append(f"{key}_must_be_true")
    for key in expected_false:
        if payload.get(key) is not False:
            errors.append(f"{key}_must_be_false")
    if payload.get("status") != "blocked":
        errors.append("status_must_be_blocked")
    if payload.get("decision") != "MANTER_EM_RESEARCH":
        errors.append("decision_must_be_manter_em_research")

    dataset = payload.get("qlib_research_dataset")
    if not isinstance(dataset, Mapping):
        errors.append("qlib_research_dataset_missing")
    else:
        scope = dataset.get("dataset_scope", {})
        if isinstance(scope, Mapping):
            required_scope_false = (
                "uses_net_pnl_as_feature",
                "uses_outcome_as_feature",
                "uses_future_data",
                "runs_training",
                "updates_qlib_runtime",
                "updates_models",
                "promotes_model",
                "updates_ai_shadow_runtime",
                "updates_freqtrade",
                "updates_risk_manager",
                "writes_runtime",
                "writes_data",
            )
            for key in required_scope_false:
                if scope.get(key) is not False:
                    errors.append(f"dataset_scope_{key}_must_be_false")
            if scope.get("separates_features_and_labels") is not True:
                errors.append("dataset_scope_must_separate_features_and_labels")
        else:
            errors.append("dataset_scope_missing")
        errors.extend(_feature_columns_are_safe(payload))
        rows = dataset.get("qlib_rows", [])
        if isinstance(rows, Sequence):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if row.get("uses_net_pnl_as_feature") is not False:
                    errors.append("row_uses_net_pnl_as_feature_must_be_false")
                if row.get("uses_future_data") is not False:
                    errors.append("row_uses_future_data_must_be_false")
                for key in row:
                    if str(key).startswith("feature_"):
                        key_lower = str(key).lower()
                        if any(
                            fragment in key_lower
                            for fragment in _FORBIDDEN_FEATURE_FRAGMENTS
                        ):
                            errors.append(f"unsafe_row_feature:{key}")
        else:
            errors.append("qlib_rows_must_be_sequence")
    return sorted(set(errors))
