"""Pure contracts and boundary guards for the three canonical datasets."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, Sequence

CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION = "canonical_data_foundation_v2"

TerminalLineageStatus = Literal["VERIFIED", "PERMANENT_QUARANTINE"]
FieldVerificationStatus = Literal[
    "VERIFIED",
    "SOURCE_CONFLICT",
    "SOURCE_MISSING",
    "AMBIGUOUS_NAMESPACE",
    "UNRESOLVED_FINANCIAL_RECONCILIATION",
    "PERMANENT_QUARANTINE",
]
DatasetClass = Literal[
    "HistoricalResearchDataset",
    "PaperOutcomeDataset",
    "OperationalFeatureDataset",
]

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "automatic_promotion_allowed": False,
    "operational_authority": False,
}

FORBIDDEN_OPERATIONAL_FEATURE_COLUMNS = frozenset(
    {
        "close_time",
        "close_time_utc",
        "exit_price",
        "exit_reason",
        "funding_fee",
        "gross_pnl",
        "is_closed",
        "label",
        "net_pnl",
        "outcome",
        "pnl",
        "profit_ratio",
        "target",
        "trading_fee",
    }
)
ACTIVE_SIGNAL_NAMES = frozenset(
    {
        "active_freqtrade_signals.json",
        "active_signals.json",
        "signal.json",
    }
)


class DatasetBoundaryError(ValueError):
    """A fail-closed cross-dataset or schema boundary violation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FieldEvidence:
    """Evidence for one financial lineage field."""

    value: Any
    source_type: str
    source_reference: str
    source_hash: str | None
    confidence_class: str
    verification_status: FieldVerificationStatus
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass(frozen=True)
class DatasetBoundary:
    """Independent authority, reader, writer, and path contract."""

    dataset_class: DatasetClass
    dataset_id: str
    dataset_version: str
    authority: str
    schema_version: str
    root_path: str
    writer_id: str
    reader_id: str
    primary_key: tuple[str, ...]
    deduplication_contract: str
    timezone_contract: str
    point_in_time_contract: str
    allowed_source_classes: tuple[DatasetClass, ...]
    prohibited_destinations: tuple[str, ...]

    @property
    def schema_hash(self) -> str:
        return stable_hash(self.to_dict(include_schema_hash=False))

    def to_dict(self, *, include_schema_hash: bool = True) -> dict[str, Any]:
        payload = json_safe(asdict(self))
        if include_schema_hash:
            payload["schema_hash"] = self.schema_hash
        return payload


DATASET_CONTRACTS: dict[DatasetClass, DatasetBoundary] = {
    "HistoricalResearchDataset": DatasetBoundary(
        dataset_class="HistoricalResearchDataset",
        dataset_id="historical_research_dataset",
        dataset_version="2.0.0",
        authority="research_evidence_only",
        schema_version="historical_research_dataset_v2",
        root_path="data/research/canonical/historical",
        writer_id="historical_research_writer_v2",
        reader_id="historical_research_reader_v2",
        primary_key=("source_record_reference",),
        deduplication_contract="source_hash_plus_versioned_record_reference_v2",
        timezone_contract="all_event_times_utc_or_row_blocked",
        point_in_time_contract="historical_and_counterfactual_not_operational",
        allowed_source_classes=("HistoricalResearchDataset",),
        prohibited_destinations=("active_signals", "paper_outcomes", "operational_features"),
    ),
    "PaperOutcomeDataset": DatasetBoundary(
        dataset_class="PaperOutcomeDataset",
        dataset_id="paper_outcome_dataset",
        dataset_version="2.0.0",
        authority="closed_reconciled_paper_outcomes_only",
        schema_version="paper_outcome_dataset_v2",
        root_path="data/feedback/canonical/paper_outcomes",
        writer_id="paper_outcome_writer_v2",
        reader_id="paper_outcome_reader_v2",
        primary_key=("paper_trade_id", "close_time_utc"),
        deduplication_contract="paper_account_scope_trade_id_close_time_v2",
        timezone_contract="open_and_close_times_must_be_utc",
        point_in_time_contract="labels_only_after_authoritative_trade_close",
        allowed_source_classes=("PaperOutcomeDataset",),
        prohibited_destinations=("active_signals", "legacy_master", "operational_features"),
    ),
    "OperationalFeatureDataset": DatasetBoundary(
        dataset_class="OperationalFeatureDataset",
        dataset_id="operational_feature_dataset",
        dataset_version="2.0.0",
        authority="validated_public_market_data_point_in_time_only",
        schema_version="operational_feature_dataset_v2",
        root_path="data/features/canonical/operational",
        writer_id="operational_feature_writer_v2",
        reader_id="operational_feature_reader_v2",
        primary_key=("symbol", "timeframe", "feature_timestamp_utc"),
        deduplication_contract="symbol_timeframe_feature_timestamp_contract_hash_v2",
        timezone_contract="feature_timestamp_and_available_at_must_be_utc",
        point_in_time_contract="available_at_must_not_exceed_decision_time",
        allowed_source_classes=("OperationalFeatureDataset",),
        prohibited_destinations=("legacy_master", "paper_outcomes"),
    ),
}


def validate_dataset_write(
    *,
    contract: DatasetBoundary,
    writer_id: str,
    target_path: str | Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]] = (),
    source_dataset_class: DatasetClass | None = None,
    publishes_active_signal: bool = False,
) -> dict[str, Any]:
    """Validate a proposed write without performing it."""

    if writer_id != contract.writer_id:
        raise DatasetBoundaryError("dataset_writer_not_authorized")
    target = _normalized_relative_path(target_path)
    root = PurePosixPath(contract.root_path)
    if target != root and root not in target.parents:
        raise DatasetBoundaryError("dataset_target_outside_authorized_root")
    if source_dataset_class is not None:
        if source_dataset_class not in contract.allowed_source_classes:
            raise DatasetBoundaryError("cross_dataset_authority_forbidden")
    if publishes_active_signal or target.name.lower() in ACTIVE_SIGNAL_NAMES:
        raise DatasetBoundaryError("dataset_cannot_publish_active_signal")

    normalized_columns = {str(column).strip().lower() for column in columns}
    if contract.dataset_class == "PaperOutcomeDataset":
        _validate_paper_outcome_rows(normalized_columns, rows)
    elif contract.dataset_class == "OperationalFeatureDataset":
        _validate_operational_feature_columns(normalized_columns)
    elif "trades_master" in target.name.lower():
        raise DatasetBoundaryError("historical_dataset_cannot_replace_legacy_master")

    return {
        "status": "ok",
        "reason": "dataset_boundary_valid",
        "dataset_class": contract.dataset_class,
        "authority": contract.authority,
        "writer_id": contract.writer_id,
        "reader_id": contract.reader_id,
        "target_path": target.as_posix(),
        "schema_hash": stable_hash(sorted(normalized_columns)),
        "write_performed": False,
    }


def build_dataset_manifest(
    *,
    contract: DatasetBoundary,
    columns: Sequence[str],
    row_count: int,
    source_manifest: Mapping[str, Any],
    git_commit_sha: str,
    created_at_utc: str,
) -> dict[str, Any]:
    """Build a deterministic content-addressed dataset manifest."""

    if row_count < 0:
        raise ValueError("dataset_row_count_must_be_non_negative")
    schema_payload = {
        "columns": sorted(str(column) for column in columns),
        "schema_version": contract.schema_version,
    }
    content = {
        "dataset_id": contract.dataset_id,
        "dataset_version": contract.dataset_version,
        "dataset_class": contract.dataset_class,
        "authority": contract.authority,
        "schema_version": contract.schema_version,
        "schema_hash": stable_hash(schema_payload),
        "row_count": int(row_count),
        "column_count": len(set(str(column) for column in columns)),
        "primary_key": list(contract.primary_key),
        "deduplication_contract": contract.deduplication_contract,
        "timezone_contract": contract.timezone_contract,
        "point_in_time_contract": contract.point_in_time_contract,
        "source_manifest": json_safe(source_manifest),
        "writer_id": contract.writer_id,
        "reader_id": contract.reader_id,
        "git_commit_sha": git_commit_sha,
    }
    return {
        **content,
        "created_at_utc": created_at_utc,
        "immutable_content_hash": stable_hash(content),
    }


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_json(payload: Any) -> str:
    normalized = json_safe(payload)
    _reject_non_finite(normalized)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _validate_paper_outcome_rows(
    columns: set[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if "is_closed" not in columns:
        raise DatasetBoundaryError("paper_outcome_requires_is_closed")
    for row in rows:
        if row.get("is_closed") is not True:
            raise DatasetBoundaryError("open_trade_cannot_be_paper_outcome")
        if str(row.get("reconciliation_status", "")).upper() != "VERIFIED":
            raise DatasetBoundaryError("paper_outcome_requires_verified_reconciliation")


def _validate_operational_feature_columns(columns: set[str]) -> None:
    for column in sorted(columns):
        if (
            column in FORBIDDEN_OPERATIONAL_FEATURE_COLUMNS
            or column.startswith("future_ret_")
            or column.startswith("target_")
            or column.startswith("label_")
            or column.startswith("outcome_")
        ):
            raise DatasetBoundaryError(f"operational_feature_leakage_forbidden:{column}")


def _normalized_relative_path(value: str | Path) -> PurePosixPath:
    text = str(value).replace("\\", "/")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DatasetBoundaryError("unsafe_dataset_target_path")
    return candidate


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non_finite_value_forbidden")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite(item)
