"""Fail-closed data quality and anti-leakage gates for B04."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .contracts import DatasetAuthority, StepEvidence, StepStatus, json_safe

REQUIRED_COLUMNS = (
    "trade_id",
    "symbol",
    "side",
    "open_time_utc",
    "close_time_utc",
    "gross_pnl",
    "net_pnl",
    "total_cost",
    "execution_engine_version",
    "cost_model_hash",
    "execution_config_hash",
)

FORBIDDEN_FEATURE_PREFIXES = (
    "target_",
    "label_",
    "future_",
    "realized_",
    "outcome_",
    "exit_",
    "close_",
)

FORBIDDEN_FEATURE_NAMES = {
    "net_pnl",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "total_cost",
    "mfe",
    "mae",
    "exit_price",
    "close_time_utc",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DataQualityResult:
    frame: pd.DataFrame
    evidence: StepEvidence
    leakage_evidence: StepEvidence
    findings: tuple[dict[str, Any], ...]


def validate_dataset(
    frame: pd.DataFrame,
    *,
    dataset_authority: DatasetAuthority,
    feature_columns: Sequence[str] = (),
    feature_availability: Mapping[str, str] | None = None,
    required_columns: Sequence[str] = REQUIRED_COLUMNS,
) -> DataQualityResult:
    if not isinstance(frame, pd.DataFrame):
        empty = pd.DataFrame()
        finding = _finding("input_not_dataframe", 0, "critical", True, "input must be a DataFrame")
        evidence = StepEvidence(
            step="data_quality",
            status=StepStatus.BLOCKED,
            reason="input_not_dataframe",
            blockers=("input_not_dataframe",),
        )
        return DataQualityResult(empty, evidence, _blocked_leakage("input_not_dataframe"), (finding,))

    normalized = frame.copy(deep=True)
    findings: list[dict[str, Any]] = []

    missing = [column for column in required_columns if column not in normalized.columns]
    if missing:
        findings.append(
            _finding(
                "missing_required_columns",
                len(normalized),
                "critical",
                True,
                ",".join(sorted(missing)),
            )
        )

    for column in ("open_time_utc", "close_time_utc", "feature_time_utc", "label_end_time_utc"):
        if column in normalized.columns:
            normalized[column] = pd.to_datetime(normalized[column], utc=True, errors="coerce")

    for column in ("gross_pnl", "net_pnl", "total_cost", "trading_fee", "funding_fee", "slippage_cost", "market_impact_cost"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    if normalized.empty:
        findings.append(_finding("empty_dataset", 0, "critical", True, "dataset contains no rows"))

    for column in required_columns:
        if column in normalized.columns:
            null_count = int(normalized[column].isna().sum())
            if null_count:
                findings.append(
                    _finding(
                        f"required_column_null:{column}",
                        null_count,
                        "critical",
                        True,
                        "required column contains null values",
                    )
                )

    for hash_column in ("cost_model_hash", "execution_config_hash"):
        if hash_column not in normalized.columns:
            continue
        invalid_hash = ~normalized[hash_column].astype(str).str.fullmatch(SHA256_PATTERN)
        invalid_hash_count = int(invalid_hash.sum())
        if invalid_hash_count:
            findings.append(
                _finding(
                    f"invalid_sha256:{hash_column}",
                    invalid_hash_count,
                    "critical",
                    True,
                    f"{hash_column} must be a lowercase SHA-256 digest",
                )
            )

    if "trade_id" in normalized.columns:
        duplicate_count = int(normalized["trade_id"].astype(str).duplicated(keep=False).sum())
        if duplicate_count:
            findings.append(
                _finding(
                    "duplicate_trade_identity",
                    duplicate_count,
                    "critical",
                    True,
                    "trade_id must be unique",
                )
            )

    if {"open_time_utc", "close_time_utc"}.issubset(normalized.columns):
        invalid_order = normalized["close_time_utc"] < normalized["open_time_utc"]
        invalid_count = int(invalid_order.fillna(False).sum())
        if invalid_count:
            findings.append(
                _finding(
                    "close_before_open",
                    invalid_count,
                    "critical",
                    True,
                    "close time precedes open time",
                )
            )
        if not normalized["open_time_utc"].is_monotonic_increasing:
            findings.append(
                _finding(
                    "open_time_not_monotonic",
                    len(normalized),
                    "high",
                    True,
                    "rows must be supplied in temporal order",
                )
            )

    if {"gross_pnl", "net_pnl", "total_cost"}.issubset(normalized.columns):
        residual = (
            normalized["gross_pnl"].astype(float)
            - normalized["total_cost"].astype(float)
            - normalized["net_pnl"].astype(float)
        ).abs()
        mismatch_count = int((residual > 1e-8).fillna(True).sum())
        if mismatch_count:
            findings.append(
                _finding(
                    "cost_reconciliation_mismatch",
                    mismatch_count,
                    "critical",
                    True,
                    "gross_pnl - total_cost must equal net_pnl",
                )
            )

    authority_blocker = dataset_authority is DatasetAuthority.PERMANENT_QUARANTINE
    if authority_blocker:
        findings.append(
            _finding(
                "input_not_authoritative",
                len(normalized),
                "critical",
                True,
                "permanent quarantine cannot produce candidate authority",
            )
        )

    leakage_findings = audit_feature_leakage(
        normalized,
        feature_columns=feature_columns,
        feature_availability=feature_availability or {},
    )
    findings.extend(leakage_findings)

    data_findings = [item for item in findings if not str(item["leakage_type"]).startswith("leakage:")]
    data_blockers = tuple(sorted({str(item["leakage_type"]) for item in data_findings if item["blocking"]}))
    leakage_blockers = tuple(
        sorted({str(item["leakage_type"]) for item in leakage_findings if item["blocking"]})
    )

    data_status = StepStatus.BLOCKED if data_blockers else StepStatus.PASS
    leakage_status = StepStatus.BLOCKED if leakage_blockers else StepStatus.PASS
    data_evidence = StepEvidence(
        step="data_quality",
        status=data_status,
        reason="data_quality_blocked" if data_blockers else "data_quality_ok",
        metrics={
            "row_count": int(len(normalized)),
            "column_count": int(len(normalized.columns)),
            "dataset_authority": dataset_authority.value,
            "finding_count": len(data_findings),
        },
        blockers=data_blockers,
    )
    leakage_evidence = StepEvidence(
        step="anti_leakage",
        status=leakage_status,
        reason="anti_leakage_blocked" if leakage_blockers else "anti_leakage_ok",
        metrics={
            "feature_column_count": len(feature_columns),
            "finding_count": len(leakage_findings),
        },
        blockers=leakage_blockers,
    )
    return DataQualityResult(normalized, data_evidence, leakage_evidence, tuple(findings))


def audit_feature_leakage(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    feature_availability: Mapping[str, str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    normalized_features = [str(column) for column in feature_columns]
    for column in normalized_features:
        lowered = column.lower()
        if lowered in FORBIDDEN_FEATURE_NAMES or any(
            lowered.startswith(prefix) for prefix in FORBIDDEN_FEATURE_PREFIXES
        ):
            findings.append(
                _finding(
                    f"leakage:forbidden_feature:{column}",
                    len(frame),
                    "critical",
                    True,
                    "future outcome or target field declared as feature",
                )
            )
        if column not in frame.columns:
            findings.append(
                _finding(
                    f"leakage:missing_feature_column:{column}",
                    len(frame),
                    "critical",
                    True,
                    "declared feature is missing from dataset",
                )
            )

    if "feature_time_utc" in frame.columns and "open_time_utc" in frame.columns:
        mask = frame["feature_time_utc"] > frame["open_time_utc"]
        count = int(mask.fillna(False).sum())
        if count:
            findings.append(
                _finding(
                    "leakage:feature_available_after_entry",
                    count,
                    "critical",
                    True,
                    "feature timestamp is after trade entry",
                )
            )

    for column, availability_column in feature_availability.items():
        if column not in frame.columns or availability_column not in frame.columns:
            findings.append(
                _finding(
                    f"leakage:availability_contract_missing:{column}",
                    len(frame),
                    "critical",
                    True,
                    "feature availability column is missing",
                )
            )
            continue
        availability = pd.to_datetime(frame[availability_column], utc=True, errors="coerce")
        if "open_time_utc" not in frame.columns:
            continue
        mask = availability.isna() | (availability > frame["open_time_utc"])
        count = int(mask.sum())
        if count:
            findings.append(
                _finding(
                    f"leakage:point_in_time_violation:{column}",
                    count,
                    "critical",
                    True,
                    "feature was not available point-in-time",
                )
            )

    if "label_end_time_utc" in frame.columns and "open_time_utc" in frame.columns:
        invalid = frame["label_end_time_utc"] < frame["open_time_utc"]
        count = int(invalid.fillna(False).sum())
        if count:
            findings.append(
                _finding(
                    "leakage:invalid_label_interval",
                    count,
                    "critical",
                    True,
                    "label interval ends before entry",
                )
            )

    for dimension in ("regime", "volatility", "liquidity", "funding"):
        availability_column = f"{dimension}_time_utc"
        if availability_column not in frame.columns or "open_time_utc" not in frame.columns:
            continue
        availability = pd.to_datetime(frame[availability_column], utc=True, errors="coerce")
        mask = availability.isna() | (availability > frame["open_time_utc"])
        count = int(mask.sum())
        if count:
            findings.append(
                _finding(
                    f"leakage:future_{dimension}_classification",
                    count,
                    "critical",
                    True,
                    f"{dimension} classification was not available point-in-time",
                )
            )

    findings.extend(detect_non_finite_features(frame, normalized_features))
    return findings


def detect_non_finite_features(frame: pd.DataFrame, feature_columns: Iterable[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for column in feature_columns:
        if column not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        count = int((~np.isfinite(numeric)).sum())
        if count:
            findings.append(
                _finding(
                    f"non_finite_feature:{column}",
                    count,
                    "high",
                    True,
                    "feature contains NaN or infinity",
                )
            )
    return findings


def _finding(
    leakage_type: str,
    affected_rows: int,
    severity: str,
    blocking: bool,
    reason: str,
    *,
    affected_folds: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "leakage_type": leakage_type,
        "affected_rows": int(affected_rows),
        "affected_folds": list(affected_folds),
        "severity": severity,
        "blocking": bool(blocking),
        "reason": reason,
        "evidence": json_safe({"affected_rows": int(affected_rows)}),
    }


def _blocked_leakage(reason: str) -> StepEvidence:
    return StepEvidence(
        step="anti_leakage",
        status=StepStatus.BLOCKED,
        reason=reason,
        blockers=(reason,),
    )
