from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from smartcrypto.ml.anti_leakage_audit import BLOCKED as LEAKAGE_BLOCKED
from smartcrypto.ml.anti_leakage_audit import audit_feature_leakage


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

SHADOW_ENTRY = "SHADOW_ENTRY"
SHADOW_SKIP = "SHADOW_SKIP"
DECISION_BLOCKED = "BLOCKED"

DEFAULT_MODEL_SOURCE = "logistic_regression"
SUPPORTED_MODEL_SOURCES = {"logistic_regression", "random_forest"}

FORBIDDEN_FEATURE_COLUMNS = {
    "target_win",
    "return_pct",
    "net_return_pct",
    "gross_return_pct",
    "leveraged_return_pct",
    "pnl",
    "pnl_resolved",
    "raw_return",
    "raw_return_resolved",
    "exit_price",
    "exit_price_repaired",
    "mfe_pct",
    "mae_pct",
    "path_candles",
}


class AIShadowEntryObserverError(ValueError):
    pass


def run_ai_shadow_entry_observer(
    features: pd.DataFrame,
    *,
    features_path: str | Path,
    model_report: dict[str, Any] | str | Path | None = None,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    time_column: str = "open_1m_ts",
    target_column: str = "target_win",
    probability_threshold: float = 0.60,
    max_rows: int = 500,
    dry_run: bool = True,
    shadow_only: bool = True,
    seed: int = 42,
    live_trading_enabled: bool = False,
    order_submission_enabled: bool = False,
    real_order_submission_enabled: bool = False,
    exchange_private_access: bool = False,
    min_train_rows: int = 20,
) -> dict[str, Any]:
    assert_shadow_safety(
        live_trading_enabled=live_trading_enabled,
        order_submission_enabled=order_submission_enabled,
        real_order_submission_enabled=real_order_submission_enabled,
        exchange_private_access=exchange_private_access,
        dry_run=dry_run,
        shadow_only=shadow_only,
    )
    validate_observer_inputs(
        features,
        id_column=id_column,
        symbol_column=symbol_column,
        time_column=time_column,
        target_column=target_column,
        probability_threshold=probability_threshold,
        max_rows=max_rows,
    )

    model_gate = normalize_model_report(model_report)
    working = features.sort_values(time_column, kind="stable").reset_index(drop=True).copy()
    feature_columns, excluded_columns = select_shadow_feature_columns(
        working,
        id_column=id_column,
        symbol_column=symbol_column,
        time_column=time_column,
        target_column=target_column,
    )
    leakage_status = None
    if feature_columns:
        leakage = audit_feature_leakage(
            working[[id_column, symbol_column, time_column, target_column, *feature_columns]].copy(),
            target_column=target_column,
            metadata_columns=[id_column, symbol_column, time_column],
            decision_mode="open",
            feature_columns=feature_columns,
        )
        leakage_status = leakage.status
        if leakage.status == LEAKAGE_BLOCKED:
            return blocked_report(
                reason="features_failed_anti_leakage_audit",
                features_path=features_path,
                rows_input=len(features),
                probability_threshold=probability_threshold,
                feature_columns=feature_columns,
                excluded_columns=excluded_columns,
                leakage_status=leakage.status,
                model_gate=model_gate,
            )

    if not feature_columns:
        return blocked_report(
            reason="no_open_decision_feature_columns_available",
            features_path=features_path,
            rows_input=len(features),
            probability_threshold=probability_threshold,
            feature_columns=[],
            excluded_columns=excluded_columns,
            leakage_status=leakage_status,
            model_gate=model_gate,
        )

    observe_count = min(int(max_rows), len(working))
    observed = working.tail(observe_count).copy()
    train = working.iloc[: max(0, len(working) - observe_count)].copy()
    if len(train) < int(min_train_rows):
        return blocked_report(
            reason=f"insufficient_historical_training_rows:{len(train)}",
            features_path=features_path,
            rows_input=len(features),
            probability_threshold=probability_threshold,
            feature_columns=feature_columns,
            excluded_columns=excluded_columns,
            leakage_status=leakage_status,
            model_gate=model_gate,
        )

    y_train = pd.to_numeric(train[target_column], errors="coerce")
    valid_train = y_train.notna()
    train = train.loc[valid_train].copy()
    y_train = y_train.loc[valid_train].astype(int).clip(0, 1).to_numpy()
    if len(train) < int(min_train_rows) or len(np.unique(y_train)) < 2:
        return blocked_report(
            reason="insufficient_target_class_diversity_for_shadow_model",
            features_path=features_path,
            rows_input=len(features),
            probability_threshold=probability_threshold,
            feature_columns=feature_columns,
            excluded_columns=excluded_columns,
            leakage_status=leakage_status,
            model_gate=model_gate,
        )

    x_train, x_observed = prepare_feature_matrices(train, observed, feature_columns)
    model_metadata = model_gate["model_metadata"]
    model = build_shadow_model(model_metadata["selected_model"], seed=seed)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_observed)[:, 1]
    decisions = build_decisions(
        observed,
        probabilities,
        id_column=id_column,
        symbol_column=symbol_column,
        time_column=time_column,
        probability_threshold=probability_threshold,
        feature_columns=feature_columns,
        model_metadata=model_metadata,
    )

    shadow_entry_count = sum(1 for item in decisions if item["decision"] == SHADOW_ENTRY)
    shadow_skip_count = sum(1 for item in decisions if item["decision"] == SHADOW_SKIP)
    blocked_count = sum(1 for item in decisions if item["decision"] == DECISION_BLOCKED)
    limitations = ["in_memory_shadow_model_not_persisted", "offline_observer_does_not_send_orders"]
    if model_gate["status"] in {WARNING, BLOCKED}:
        limitations.extend(model_gate["limitations"])
    status = OK if blocked_count == 0 and model_gate["status"] != BLOCKED else WARNING
    payload = {
        "status": status,
        "features_path": str(features_path),
        "rows_input": int(len(features)),
        "rows_observed": int(len(decisions)),
        "shadow_entry_count": int(shadow_entry_count),
        "shadow_skip_count": int(shadow_skip_count),
        "blocked_count": int(blocked_count),
        "probability_threshold": float(probability_threshold),
        "model_name": model_metadata["model_name"],
        "model_version": model_metadata["model_version"],
        "model_source": model_metadata["model_source"],
        "feature_columns_used": feature_columns,
        "feature_columns_excluded": excluded_columns,
        "leakage_status": leakage_status,
        "safety_status": safety_status(),
        "sample_decisions": decisions[:10],
        "limitations": limitations,
        "recommended_next_action": recommended_next_action(status),
        "created_at": utc_now(),
        "shadow_only": True,
        "dry_run": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
    }
    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return {"report": payload, "decisions": decisions}


def assert_shadow_safety(
    *,
    live_trading_enabled: bool = False,
    order_submission_enabled: bool = False,
    real_order_submission_enabled: bool = False,
    exchange_private_access: bool = False,
    dry_run: bool = True,
    shadow_only: bool = True,
) -> None:
    unsafe = {
        "live_trading_enabled": live_trading_enabled,
        "order_submission_enabled": order_submission_enabled,
        "real_order_submission_enabled": real_order_submission_enabled,
        "exchange_private_access": exchange_private_access,
    }
    enabled = [name for name, value in unsafe.items() if bool(value)]
    if enabled:
        raise AIShadowEntryObserverError(f"unsafe_runtime_flags_blocked:{','.join(enabled)}")
    if not bool(dry_run):
        raise AIShadowEntryObserverError("dry_run_required_for_ai_shadow_entry_observer")
    if not bool(shadow_only):
        raise AIShadowEntryObserverError("shadow_only_required_for_ai_shadow_entry_observer")


def validate_observer_inputs(
    frame: pd.DataFrame,
    *,
    id_column: str,
    symbol_column: str,
    time_column: str,
    target_column: str,
    probability_threshold: float,
    max_rows: int,
) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise AIShadowEntryObserverError("features_must_be_dataframe")
    if frame.empty:
        raise AIShadowEntryObserverError("features_empty")
    for column in (id_column, symbol_column, time_column, target_column):
        if column not in frame.columns:
            raise AIShadowEntryObserverError(f"required_column_missing:{column}")
    if frame[id_column].isna().any():
        raise AIShadowEntryObserverError(f"id_column_contains_nulls:{id_column}")
    if frame[id_column].duplicated(keep=False).any():
        raise AIShadowEntryObserverError(f"id_column_contains_duplicates:{id_column}")
    if not 0 <= float(probability_threshold) <= 1:
        raise AIShadowEntryObserverError("probability_threshold_out_of_range")
    if int(max_rows) <= 0:
        raise AIShadowEntryObserverError("max_rows_must_be_positive")


def normalize_model_report(model_report: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if model_report is None:
        metadata = build_model_metadata(DEFAULT_MODEL_SOURCE, from_model_report=False)
        return {
            "status": WARNING,
            "model_metadata": metadata,
            "limitations": ["model_report_missing_observer_is_diagnostic_only"],
        }
    payload: dict[str, Any]
    if isinstance(model_report, (str, Path)):
        path = Path(model_report)
        if not path.exists():
            metadata = build_model_metadata(DEFAULT_MODEL_SOURCE, from_model_report=False)
            return {
                "status": WARNING,
                "model_metadata": metadata,
                "limitations": ["model_report_path_missing_observer_is_diagnostic_only"],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif isinstance(model_report, dict):
        payload = dict(model_report)
    else:
        raise AIShadowEntryObserverError("model_report_must_be_mapping_or_path")
    status = str(payload.get("status") or "UNKNOWN").upper()
    selected = resolve_selected_model(payload)
    metadata = build_model_metadata(selected, from_model_report=True)
    return {
        "status": status if status in {OK, WARNING, BLOCKED} else WARNING,
        "model_metadata": metadata,
        "limitations": payload.get("limitations") if isinstance(payload.get("limitations"), list) else [],
    }


def resolve_selected_model(payload: dict[str, Any]) -> str:
    candidate = str(payload.get("best_model") or payload.get("model_source") or DEFAULT_MODEL_SOURCE)
    if ":" in candidate:
        candidate = candidate.rsplit(":", 1)[-1]
    candidate = candidate.strip().lower()
    return candidate if candidate in SUPPORTED_MODEL_SOURCES else DEFAULT_MODEL_SOURCE


def build_model_metadata(selected_model: str, *, from_model_report: bool) -> dict[str, str]:
    selected = selected_model if selected_model in SUPPORTED_MODEL_SOURCES else DEFAULT_MODEL_SOURCE
    source = f"model_vs_baseline_financial_evaluation:{selected}" if from_model_report else selected
    return {
        "selected_model": selected,
        "model_name": f"{selected}_shadow_observer",
        "model_source": source,
        "model_version": f"{selected}_in_memory_research_v1",
    }


def select_shadow_feature_columns(
    frame: pd.DataFrame,
    *,
    id_column: str = "trade_id",
    symbol_column: str = "symbol",
    time_column: str = "open_1m_ts",
    target_column: str = "target_win",
) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    excluded: list[str] = []
    metadata = {id_column, symbol_column, time_column, target_column}
    for column in frame.columns:
        name = str(column)
        if name in metadata or is_forbidden_feature(name):
            excluded.append(name)
            continue
        allowed_name = name.startswith("open_1m_") or name.startswith("open_5m_") or name == "duration_seconds"
        if not allowed_name or name.endswith("_ts"):
            excluded.append(name)
            continue
        numeric = pd.to_numeric(frame[name], errors="coerce")
        if numeric.notna().sum() == 0:
            excluded.append(name)
            continue
        selected.append(name)
    return selected, excluded


def is_forbidden_feature(column: str) -> bool:
    lower = column.lower()
    return (
        column in FORBIDDEN_FEATURE_COLUMNS
        or lower.startswith("close_")
        or lower.startswith("future_ret_")
        or lower.startswith("target_")
        or "return_pct" in lower
        or lower in {"target", "label"}
    )


def prepare_feature_matrices(
    train: pd.DataFrame,
    observed: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    x_train = train[feature_columns].apply(pd.to_numeric, errors="coerce")
    x_observed = observed[feature_columns].apply(pd.to_numeric, errors="coerce")
    medians = x_train.median(numeric_only=True).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return x_train.fillna(medians).replace([np.inf, -np.inf], 0.0), x_observed.fillna(medians).replace([np.inf, -np.inf], 0.0)


def build_shadow_model(selected_model: str, *, seed: int) -> Any:
    if selected_model == "random_forest":
        return RandomForestClassifier(
            n_estimators=80,
            max_depth=5,
            min_samples_leaf=10,
            random_state=seed,
            n_jobs=1,
        )
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=1000, random_state=seed, solver="lbfgs")),
        ]
    )


def build_decisions(
    observed: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    id_column: str,
    symbol_column: str,
    time_column: str,
    probability_threshold: float,
    feature_columns: list[str],
    model_metadata: dict[str, str],
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    created_at = utc_now()
    for offset, (_, row) in enumerate(observed.iterrows()):
        probability = float(probabilities[offset])
        decision = SHADOW_ENTRY if probability >= float(probability_threshold) else SHADOW_SKIP
        reason = "probability_above_or_equal_threshold" if decision == SHADOW_ENTRY else "probability_below_threshold"
        decision_id = stable_decision_id(row.get(id_column), row.get(time_column), probability, probability_threshold)
        decisions.append(
            {
                "decision_id": decision_id,
                "created_at": created_at,
                "trade_id": row.get(id_column),
                "symbol": row.get(symbol_column),
                "open_1m_ts": stringify_timestamp(row.get(time_column)),
                "model_name": model_metadata["model_name"],
                "model_version": model_metadata["model_version"],
                "model_source": model_metadata["model_source"],
                "probability_win": probability,
                "probability_threshold": float(probability_threshold),
                "decision": decision,
                "decision_reason": reason,
                "feature_count": int(len(feature_columns)),
                "feature_columns_used": feature_columns,
                "blocked_reason": None,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
            }
        )
    return decisions


def blocked_report(
    *,
    reason: str,
    features_path: str | Path,
    rows_input: int,
    probability_threshold: float,
    feature_columns: list[str],
    excluded_columns: list[str],
    leakage_status: str | None,
    model_gate: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "status": BLOCKED,
        "reason": reason,
        "features_path": str(features_path),
        "rows_input": int(rows_input),
        "rows_observed": 0,
        "shadow_entry_count": 0,
        "shadow_skip_count": 0,
        "blocked_count": 0,
        "probability_threshold": float(probability_threshold),
        "model_name": model_gate["model_metadata"]["model_name"],
        "model_version": model_gate["model_metadata"]["model_version"],
        "model_source": model_gate["model_metadata"]["model_source"],
        "feature_columns_used": feature_columns,
        "feature_columns_excluded": excluded_columns,
        "leakage_status": leakage_status,
        "safety_status": safety_status(),
        "sample_decisions": [],
        "limitations": [reason, *model_gate.get("limitations", [])],
        "recommended_next_action": recommended_next_action(BLOCKED),
        "created_at": utc_now(),
        "shadow_only": True,
        "dry_run": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
    }
    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return {"report": payload, "decisions": []}


def safety_status() -> dict[str, bool]:
    return {
        "shadow_only": True,
        "dry_run": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def recommended_next_action(status: str) -> str:
    if status == BLOCKED:
        return "block_ai_shadow_entry_observer_until_open_decision_features_are_valid"
    if status == WARNING:
        return "keep_ai_entry_observer_in_shadow_and_review_limitations_before_paper_use"
    return "collect_shadow_decisions_for_7_days_or_200_signals_before_any_paper_operational_change"


def stable_decision_id(trade_id: Any, timestamp: Any, probability: float, threshold: float) -> str:
    material = f"{trade_id}|{stringify_timestamp(timestamp)}|{probability:.10f}|{threshold:.4f}"
    return "shadow_decision_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def stringify_timestamp(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"features_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def write_jsonl(path: str | Path, decisions: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in decisions]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
