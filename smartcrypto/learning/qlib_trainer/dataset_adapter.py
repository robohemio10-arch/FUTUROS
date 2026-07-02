"""Dataset adapter for institutional ranking challenger training evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import frame_hash, read_frame
from smartcrypto.learning.walkforward.purged_split_engine import build_walkforward_splits, derive_embargo_seconds
from smartcrypto.learning.walkforward.split_schema import (
    DEFAULT_DATASET_MANIFEST_JSON,
    DEFAULT_FEATURE_CONTRACT_JSON,
    DEFAULT_MICROBATCH_DIR,
    DEFAULT_TARGET_STORE_JSON,
)

DEFAULT_WALKFORWARD_JSON = Path("data/reports/walkforward_anti_leakage_split_engine_v1.json")
DEFAULT_WALKFORWARD_BASELINE_JSON = Path("data/reports/walkforward_baseline_summary_v1.json")

FEATURE_ROLE_BLOCKLIST = {"label", "outcome", "identifier", "forbidden"}


@dataclass(frozen=True)
class RankingDatasetBundle:
    project_root: Path
    selected_dataset_path: Path | None
    dataset: pd.DataFrame
    feature_contract: dict[str, Any]
    dataset_manifest: dict[str, Any]
    target_store: dict[str, Any]
    walkforward: dict[str, Any]
    baseline_summary: dict[str, Any]
    feature_columns: list[str]
    primary_target: str
    auxiliary_targets: list[str]
    reconstructed_splits: list[dict[str, Any]]
    lineage_drift_detected: bool
    validation_errors: list[str]


def load_ranking_dataset_bundle(
    *,
    project_root: str | Path,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    target_store_path: str | Path | None = None,
    walkforward_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
) -> RankingDatasetBundle:
    root = Path(project_root).resolve()
    feature_contract = read_json_if_exists(resolve(root, feature_contract_path, DEFAULT_FEATURE_CONTRACT_JSON))
    dataset_manifest = read_json_if_exists(resolve(root, dataset_manifest_path, DEFAULT_DATASET_MANIFEST_JSON))
    target_store = read_json_if_exists(resolve(root, target_store_path, DEFAULT_TARGET_STORE_JSON))
    walkforward = read_json_if_exists(resolve(root, walkforward_path, DEFAULT_WALKFORWARD_JSON))
    baseline_summary = read_json_if_exists(resolve(root, baseline_path, DEFAULT_WALKFORWARD_BASELINE_JSON))
    selected_path = select_dataset_path(root, dataset_path, dataset_manifest)
    dataset = read_frame(selected_path) if selected_path is not None and selected_path.exists() else pd.DataFrame()
    feature_columns = [str(column) for column in feature_contract.get("feature_columns", [])]
    validation_errors = validate_lineage_and_schema(
        dataset=dataset,
        feature_contract=feature_contract,
        dataset_manifest=dataset_manifest,
        target_store=target_store,
        walkforward=walkforward,
        feature_columns=feature_columns,
    )
    adapted = adapt_dataset(dataset, target_store)
    reconstructed_splits: list[dict[str, Any]] = []
    if not validation_errors:
        embargo_seconds = derive_embargo_seconds(target_store)
        reconstructed_splits = build_walkforward_splits(adapted, embargo_seconds=embargo_seconds)
        validation_errors.extend(validate_reconstructed_splits(reconstructed_splits, walkforward))
    lineage_drift = bool([error for error in validation_errors if "hash_drift" in error or "split_hash_mismatch" in error])
    return RankingDatasetBundle(
        project_root=root,
        selected_dataset_path=selected_path,
        dataset=adapted,
        feature_contract=feature_contract,
        dataset_manifest=dataset_manifest,
        target_store=target_store,
        walkforward=walkforward,
        baseline_summary=baseline_summary,
        feature_columns=feature_columns,
        primary_target="target_expected_value_component",
        auxiliary_targets=["target_label_sign", "target_profit_ratio", "target_triple_barrier_label", "target_net_pnl"],
        reconstructed_splits=reconstructed_splits,
        lineage_drift_detected=lineage_drift,
        validation_errors=sorted(set(validation_errors)),
    )


def adapt_dataset(dataset: pd.DataFrame, target_store: Mapping[str, Any]) -> pd.DataFrame:
    frame = dataset.copy().reset_index(drop=True)
    target_records = target_store.get("target_records")
    if isinstance(target_records, list) and target_records:
        target_frame = pd.DataFrame(target_records)
        merge_keys = [key for key in ("order_id", "event_id", "trade_id") if key in frame.columns and key in target_frame.columns]
        target_columns = [column for column in target_frame.columns.astype(str) if column.startswith("target_")]
        if merge_keys:
            frame = frame.merge(target_frame[merge_keys + target_columns], on=merge_keys, how="left", suffixes=("", "_target_store"))
        elif len(target_frame) == len(frame):
            for column in target_columns:
                frame[column] = target_frame[column].to_numpy()
    if "open_time_utc" in frame.columns:
        frame["open_time_utc"] = pd.to_datetime(frame["open_time_utc"], utc=True, errors="coerce")
    if "close_time_utc" in frame.columns:
        frame["close_time_utc"] = pd.to_datetime(frame["close_time_utc"], utc=True, errors="coerce")
    elif "target_holding_seconds" in frame.columns and "open_time_utc" in frame.columns:
        holding = pd.to_numeric(frame["target_holding_seconds"], errors="coerce").fillna(0.0)
        frame["close_time_utc"] = frame["open_time_utc"] + pd.to_timedelta(holding, unit="s")
    if "target_expected_value_component" not in frame.columns and "net_pnl" in frame.columns:
        frame["target_expected_value_component"] = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0)
    return frame.sort_values(["open_time_utc", "close_time_utc"], kind="mergesort").reset_index(drop=True)


def validate_lineage_and_schema(
    *,
    dataset: pd.DataFrame,
    feature_contract: Mapping[str, Any],
    dataset_manifest: Mapping[str, Any],
    target_store: Mapping[str, Any],
    walkforward: Mapping[str, Any],
    feature_columns: list[str],
) -> list[str]:
    errors: list[str] = []
    if dataset.empty:
        errors.append("missing_or_empty_selected_dataset")
    missing_features = [column for column in feature_columns if column not in dataset.columns]
    if missing_features:
        errors.append(f"missing_feature_columns:{','.join(sorted(missing_features))}")
    roles = feature_contract.get("feature_roles", {})
    if isinstance(roles, Mapping):
        bad_roles = [column for column in feature_columns if roles.get(column) in FEATURE_ROLE_BLOCKLIST]
        if bad_roles:
            errors.append(f"forbidden_role_columns_in_features:{','.join(sorted(bad_roles))}")
    future_columns = [column for column in feature_columns if column.lower().startswith("future_ret_")]
    target_columns = [column for column in feature_columns if column.lower().startswith("target_")]
    label_columns = [column for column in feature_columns if column.lower().startswith("label_")]
    outcome_columns = set(str(column) for column in feature_contract.get("outcome_columns", []))
    identifier_columns = set(str(column) for column in feature_contract.get("identifier_columns", []))
    outcome_or_identifier_features = [column for column in feature_columns if column in outcome_columns or column in identifier_columns]
    if future_columns:
        errors.append(f"future_ret_columns_in_features:{','.join(sorted(future_columns))}")
    if target_columns:
        errors.append(f"target_columns_in_features:{','.join(sorted(target_columns))}")
    if label_columns:
        errors.append(f"label_columns_in_features:{','.join(sorted(label_columns))}")
    if outcome_or_identifier_features:
        errors.append(f"outcome_or_identifier_columns_in_features:{','.join(sorted(outcome_or_identifier_features))}")
    contract_hash = feature_contract.get("contract_hash")
    dataset_hash = dataset_manifest.get("dataset_hash")
    target_store_hash = target_store.get("target_store_hash")
    if target_store.get("feature_contract_hash") != contract_hash:
        errors.append("feature_contract_hash_drift:target_store")
    if target_store.get("dataset_hash") != dataset_hash:
        errors.append("dataset_hash_drift:target_store")
    if walkforward.get("feature_contract_hash") != contract_hash:
        errors.append("feature_contract_hash_drift:walkforward")
    if walkforward.get("dataset_hash") != dataset_hash:
        errors.append("dataset_hash_drift:walkforward")
    if walkforward.get("target_store_hash") != target_store_hash:
        errors.append("target_store_hash_drift:walkforward")
    if not dataset.empty and dataset_hash and frame_hash(dataset) != dataset_hash:
        errors.append("dataset_hash_drift:selected_dataset")
    leakage_audit = walkforward.get("leakage_audit") if isinstance(walkforward.get("leakage_audit"), Mapping) else {}
    leakage = walkforward.get("leakage_status") or leakage_audit.get("leakage_status")
    if leakage != "ok":
        errors.append("walkforward_leakage_not_ok")
    for key in ("embargo_violation_count", "temporal_overlap_count", "label_interval_overlap_count"):
        value = walkforward.get(key, leakage_audit.get(key, 0))
        if int(value or 0) > 0:
            errors.append(f"walkforward_{key}_nonzero")
    split_status = walkforward.get("split_engine_status") or walkforward.get("validation_status")
    if split_status != "ok":
        errors.append("walkforward_split_engine_not_ok")
    return sorted(set(errors))


def validate_reconstructed_splits(reconstructed: list[dict[str, Any]], walkforward: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = walkforward.get("splits")
    if expected is None and isinstance(walkforward.get("split_engine"), Mapping):
        expected = walkforward.get("split_engine", {}).get("splits")
    if not isinstance(expected, list):
        errors.append("missing_walkforward_splits")
        return errors
    if len(reconstructed) != len(expected):
        errors.append("split_count_drift")
        return errors
    for got, want in zip(reconstructed, expected, strict=True):
        for key in ("split_hash", "train_indices_hash", "validation_indices_hash", "test_indices_hash"):
            if got.get(key) != want.get(key):
                errors.append(f"{key}_mismatch:{want.get('split_id')}")
    return sorted(set(errors))


def select_dataset_path(root: Path, dataset_path: str | Path | None, dataset_manifest: Mapping[str, Any]) -> Path | None:
    candidates: list[Path] = []
    if dataset_path is not None:
        candidates.append(resolve(root, dataset_path, Path("")))
    manifest_dataset = dataset_manifest.get("selected_training_dataset")
    if isinstance(manifest_dataset, str) and manifest_dataset:
        candidates.append(resolve(root, manifest_dataset, Path("")))
    microbatch_dir = root / DEFAULT_MICROBATCH_DIR
    if microbatch_dir.exists():
        candidates.extend(sorted(microbatch_dir.glob("*.parquet"), reverse=True))
    for candidate in candidates:
        path = candidate.resolve()
        if path.exists() and path.is_file():
            return path
    return None


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if not str(path):
        return root
    return path if path.is_absolute() else (root / path)
