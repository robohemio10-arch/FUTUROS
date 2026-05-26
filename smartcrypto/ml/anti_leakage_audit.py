from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

TIME_METADATA_COLUMNS = {
    "date",
    "datetime",
    "timestamp",
    "time",
    "ts",
    "ts_ms",
    "open_time",
    "close_time",
    "open_ts",
    "close_ts",
    "entry_time",
    "exit_time",
    "horario_abertura",
    "horario_fechamento",
    "horario_transacao",
}

DEFAULT_METADATA_COLUMNS = {
    "trade_id",
    "client_order_id",
    "correlation_id",
    "symbol",
    "pair",
    "instrument",
    "side",
    "moeda",
    "tf",
    "timeframe",
    *TIME_METADATA_COLUMNS,
}

FORBIDDEN_EXACT_COLUMNS = {
    "pnl",
    "pnl_pct",
    "return_pct",
    "mfe_pct",
    "mae_pct",
    "profit",
    "realized_pnl",
    "closed_pnl",
}

FORBIDDEN_PATTERNS = (
    re.compile(r"^future_ret(?:_|$)", re.IGNORECASE),
    re.compile(r"^target(?:_|$)", re.IGNORECASE),
    re.compile(r"^label(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)future(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)forward(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)next(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)after(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)post(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)outcome(?:_|$)", re.IGNORECASE),
)


class AntiLeakageAuditError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureLeakageReport:
    total_columns: int
    allowed_features: list[str]
    forbidden_features: list[str]
    suspicious_features: list[str]
    target_column: str
    leakage_detected: bool
    status: str
    feature_columns: list[str]
    metadata_columns: list[str]
    forbidden_columns: list[str]
    dropped_columns: list[str]
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_feature_leakage(
    frame: pd.DataFrame,
    *,
    target_column: str = "target_win",
    feature_columns: list[str] | tuple[str, ...] | None = None,
    metadata_columns: list[str] | tuple[str, ...] | None = None,
    decision_mode: str = "open",
) -> FeatureLeakageReport:
    if not isinstance(frame, pd.DataFrame):
        raise AntiLeakageAuditError("anti_leakage_input_must_be_dataframe")
    if not target_column:
        raise AntiLeakageAuditError("target_column_required")
    if target_column not in frame.columns:
        raise AntiLeakageAuditError(f"target_column_missing:{target_column}")

    columns = [str(column) for column in frame.columns]
    metadata = resolve_metadata_columns(columns, metadata_columns)
    target = str(target_column)

    if feature_columns is None:
        features = [
            column
            for column in columns
            if column != target and column not in metadata
        ]
    else:
        features = [str(column) for column in feature_columns]

    unknown_features = [column for column in features if column not in columns]
    if unknown_features:
        raise AntiLeakageAuditError(f"feature_columns_missing:{unknown_features}")

    forbidden: list[str] = []
    suspicious: list[str] = []

    for column in features:
        reason = forbidden_reason(
            column,
            target_column=target,
            decision_mode=decision_mode,
        )
        if reason:
            forbidden.append(f"{column}:{reason}")
            continue
        if is_suspicious_feature(column):
            suspicious.append(column)

    allowed = [
        column
        for column in features
        if not forbidden_reason(column, target_column=target, decision_mode=decision_mode)
    ]
    leakage_detected = bool(forbidden)
    status = BLOCKED if leakage_detected else WARNING if suspicious else OK
    dropped = [column for column in columns if column not in allowed]

    return FeatureLeakageReport(
        total_columns=len(columns),
        allowed_features=allowed,
        forbidden_features=forbidden,
        suspicious_features=suspicious,
        target_column=target,
        leakage_detected=leakage_detected,
        status=status,
        feature_columns=features,
        metadata_columns=[column for column in columns if column in metadata],
        forbidden_columns=[item.split(":", 1)[0] for item in forbidden],
        dropped_columns=dropped,
    )


def resolve_metadata_columns(
    columns: list[str],
    metadata_columns: list[str] | tuple[str, ...] | None,
) -> set[str]:
    metadata = {str(column) for column in (metadata_columns or [])}
    metadata.update(column for column in columns if column in DEFAULT_METADATA_COLUMNS)
    metadata.update(column for column in columns if column.endswith("_ts"))
    metadata.update(column for column in columns if column.endswith("_time"))
    return metadata


def forbidden_reason(
    column: str,
    *,
    target_column: str,
    decision_mode: str,
) -> str | None:
    normalized = column.strip()
    lower = normalized.lower()
    if normalized == target_column:
        return "target_column_used_as_feature"
    if lower in FORBIDDEN_EXACT_COLUMNS:
        return "post_event_outcome_column"
    if decision_mode.lower() == "open" and lower.startswith("close_"):
        return "close_feature_for_open_decision"
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(lower):
            return "forbidden_future_or_label_pattern"
    return None


def is_suspicious_feature(column: str) -> bool:
    lower = column.lower()
    markers = ("exit", "closed", "filled", "resolved", "settled")
    return any(marker in lower for marker in markers)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
