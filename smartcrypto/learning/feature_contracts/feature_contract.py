"""Feature role inference and anti-leakage contract generation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

import pandas as pd

from smartcrypto.learning.paper_autolearning.outcome_schema import utc_now_iso

FEATURE_CONTRACT_SCHEMA_VERSION = "ai_unified_feature_contract_v1"

FeatureRole = Literal["feature", "label", "outcome", "metadata", "identifier", "forbidden"]

FORBIDDEN_PATTERNS = (
    "future_ret_*",
    "target_*",
)

IDENTIFIER_COLUMNS = {
    "order_id",
    "internal_order_id",
    "trade_id",
    "event_id",
    "row_fingerprint",
    "record_hash",
    "source_sha256",
    "ingestion_run_id",
}

OUTCOME_COLUMNS = {
    "net_pnl",
    "gross_pnl",
    "profit_ratio",
    "exit_reason",
    "exit_price",
    "close_time",
    "close_time_utc",
    "liquidation_flag",
    "roi_hit",
    "stoploss_hit",
    "forced_exit",
    "qlib_prediction_id",
    "ai_shadow_decision_id",
    "pnl_on_margin_pct",
    "pnl_on_notional_pct",
    "duration_seconds",
    "is_closed",
    "trading_fee",
    "funding_fee",
    "liquidation_price",
    "distance_to_liquidation_pct",
}

METADATA_COLUMNS = {
    "symbol",
    "symbol_norm",
    "side",
    "position_side",
    "market_type",
    "margin_mode",
    "open_time",
    "open_time_utc",
    "created_at_utc",
    "source",
    "source_file",
    "validation_status",
    "validation_errors",
    "paper_candidate_filter_decision",
    "strategy_id",
}

EXPLICIT_FEATURE_COLUMNS = {
    "entry_price",
    "quantity",
    "notional",
    "leverage",
    "paper_candidate_filter_called",
}

POST_TRADE_LEAKAGE_COLUMNS = {
    *OUTCOME_COLUMNS,
    "label_win_loss",
    "label_sign",
    "label_net_pnl_bucket",
    "label_holding_time_bucket",
    "label_quality_bucket",
}


def classify_feature_roles(columns: list[str]) -> dict[str, FeatureRole]:
    """Classify columns into deterministic feature contract roles."""

    roles: dict[str, FeatureRole] = {}
    for column in sorted(str(item) for item in columns):
        roles[column] = classify_column(column)
    return roles


def classify_column(column: str) -> FeatureRole:
    lower = column.lower()
    if any(fnmatch.fnmatch(lower, pattern) for pattern in FORBIDDEN_PATTERNS):
        return "forbidden"
    if lower.startswith("label_"):
        return "label"
    if lower.startswith("outcome_"):
        return "outcome"
    if lower.startswith("feature_"):
        return "feature"
    if lower in IDENTIFIER_COLUMNS:
        return "identifier"
    if lower in OUTCOME_COLUMNS:
        return "outcome"
    if lower in EXPLICIT_FEATURE_COLUMNS:
        return "feature"
    if lower in METADATA_COLUMNS:
        return "metadata"
    if lower.endswith("_id"):
        return "identifier"
    return "metadata"


def build_feature_contract(
    frame: pd.DataFrame,
    *,
    source_datasets: list[str],
) -> dict[str, Any]:
    roles = classify_feature_roles([str(column) for column in frame.columns])
    role_columns = columns_by_role(roles)
    dtype_map = {str(column): str(frame[column].dtype) for column in frame.columns}
    null_counts = {str(column): int(frame[column].isna().sum()) for column in frame.columns}
    feature_columns = sorted(role_columns["feature"])
    label_columns = sorted(role_columns["label"])
    outcome_columns = sorted(role_columns["outcome"])
    metadata_columns = sorted(role_columns["metadata"])
    identifier_columns = sorted(role_columns["identifier"])
    forbidden_columns = sorted(role_columns["forbidden"])
    leakage_columns = sorted(set(outcome_columns + label_columns + forbidden_columns) & set(feature_columns))
    validation_errors = validate_feature_contract(
        feature_columns=feature_columns,
        label_columns=label_columns,
        leakage_columns=leakage_columns,
    )
    schema_payload = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": dtype_map,
        "feature_roles": roles,
        "feature_columns": feature_columns,
        "label_columns": label_columns,
    }
    schema_hash = stable_hash(schema_payload)
    base: dict[str, Any] = {
        "schema_version": FEATURE_CONTRACT_SCHEMA_VERSION,
        "contract_id": f"feature_contract_{schema_hash[:16]}",
        "generated_at_utc": utc_now_iso(),
        "source_datasets": source_datasets,
        "feature_columns": feature_columns,
        "label_columns": label_columns,
        "outcome_columns": outcome_columns,
        "metadata_columns": metadata_columns,
        "identifier_columns": identifier_columns,
        "forbidden_columns": forbidden_columns,
        "feature_roles": roles,
        "feature_dtypes": {column: dtype_map[column] for column in feature_columns},
        "required_columns": sorted(set(feature_columns + label_columns)),
        "optional_columns": sorted(set(frame.columns.astype(str)) - set(feature_columns) - set(label_columns)),
        "nullable_columns": sorted(column for column, count in null_counts.items() if count > 0),
        "non_nullable_columns": sorted(column for column, count in null_counts.items() if count == 0),
        "deterministic_feature_order": True,
        "leakage_policy": {
            "future_ret_as_feature": "blocked",
            "target_as_feature": "blocked",
            "label_as_feature": "blocked",
            "outcome_as_feature": "blocked",
            "post_trade_columns_as_feature": "blocked",
        },
        "forbidden_patterns": list(FORBIDDEN_PATTERNS),
        "schema_hash": schema_hash,
        "validation_status": "blocked" if validation_errors else "ok",
        "validation_errors": validation_errors,
        "forbidden_columns_detected": forbidden_columns,
        "leakage_columns_detected": leakage_columns,
        "future_ret_columns_detected": sorted(column for column in frame.columns.astype(str) if column.lower().startswith("future_ret_")),
    }
    base["contract_hash"] = contract_hash(base)
    return base


def columns_by_role(roles: Mapping[str, FeatureRole]) -> dict[str, list[str]]:
    grouped = {role: [] for role in ("feature", "label", "outcome", "metadata", "identifier", "forbidden")}
    for column, role in roles.items():
        grouped[role].append(column)
    return {role: sorted(columns) for role, columns in grouped.items()}


def validate_feature_contract(
    *,
    feature_columns: list[str],
    label_columns: list[str],
    leakage_columns: list[str],
) -> list[str]:
    errors: list[str] = []
    if not feature_columns:
        errors.append("missing_valid_feature_columns")
    if not label_columns:
        errors.append("missing_valid_label_columns")
    for column in feature_columns:
        lower = column.lower()
        if lower.startswith("future_ret_"):
            errors.append(f"future_ret_feature_forbidden:{column}")
        if lower.startswith("label_"):
            errors.append(f"label_feature_forbidden:{column}")
        if lower in POST_TRADE_LEAKAGE_COLUMNS:
            errors.append(f"post_trade_feature_forbidden:{column}")
    for column in leakage_columns:
        errors.append(f"leakage_feature_forbidden:{column}")
    return sorted(set(errors))


def contract_hash(contract: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in contract.items()
        if key not in {"generated_at_utc", "contract_hash"}
    }
    return stable_hash(payload)


def stable_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=json_safe)


def json_safe(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)
