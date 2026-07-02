"""Purged walk-forward split engine with embargo and no-training baselines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import file_sha256, frame_hash, read_frame
from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso

from .baselines import build_baseline_summary
from .leakage_audit import audit_leakage, interval_intersects
from .split_schema import (
    DEFAULT_BASELINE_JSON,
    DEFAULT_BASELINE_MD,
    DEFAULT_DATASET_MANIFEST_JSON,
    DEFAULT_FEATURE_CONTRACT_JSON,
    DEFAULT_MICROBATCH_DIR,
    DEFAULT_OUTPUT_JSON,
    DEFAULT_OUTPUT_MD,
    DEFAULT_OUTCOME_EVENTS,
    DEFAULT_TARGET_STORE_JSON,
    DEFAULT_TARGET_STORE_SUMMARY_JSON,
    MINIMUM_EMBARGO_SECONDS,
    SAFETY_FALSE_FIELDS,
    SCHEMA_VERSION,
)


def build_walkforward_anti_leakage_report(
    *,
    project_root: str | Path,
    write: bool = False,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    target_store_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    baseline_json_path: str | Path | None = None,
    baseline_markdown_path: str | Path | None = None,
    embargo_seconds_override: int | None = None,
) -> dict[str, Any]:
    """Build walk-forward split evidence without fitting or promoting models."""

    root = Path(project_root).resolve()
    feature_contract_file = resolve(root, feature_contract_path, DEFAULT_FEATURE_CONTRACT_JSON)
    dataset_manifest_file = resolve(root, dataset_manifest_path, DEFAULT_DATASET_MANIFEST_JSON)
    target_store_file = resolve(root, target_store_path, DEFAULT_TARGET_STORE_JSON)
    feature_contract = read_json_if_exists(feature_contract_file)
    dataset_manifest = read_json_if_exists(dataset_manifest_file)
    target_store = read_json_if_exists(target_store_file)
    selection = select_dataset(root, dataset_path, dataset_manifest)
    selected_path = selection["path"]
    source_frame = selection["frame"]
    validation_errors: list[str] = []

    if source_frame is None or selected_path is None:
        validation_errors.append("missing_selected_dataset")
        split_frame = pd.DataFrame()
    else:
        validation_errors.extend(validate_raw_source(source_frame))
        split_frame = prepare_split_frame(source_frame, target_store)
        validation_errors.extend(validate_sources(split_frame, feature_contract))

    target_store_hash = target_store.get("target_store_hash")
    feature_contract_hash = feature_contract.get("contract_hash")
    dataset_hash = dataset_manifest.get("dataset_hash") or (frame_hash(source_frame) if source_frame is not None else None)
    embargo_seconds = derive_embargo_seconds(target_store, override=embargo_seconds_override)

    splits: list[dict[str, Any]] = []
    leakage_audit = empty_leakage_audit(feature_contract)
    baseline_summary = build_baseline_summary(split_frame)
    split_engine: dict[str, Any] | None = None
    if not validation_errors:
        splits = build_walkforward_splits(split_frame, embargo_seconds=embargo_seconds)
        if not splits:
            validation_errors.append("unable_to_build_walkforward_splits")
        else:
            leakage_audit = audit_leakage(split_frame, splits, feature_contract=feature_contract, embargo_seconds=embargo_seconds)
            if leakage_audit["leakage_status"] != "ok":
                validation_errors.append("leakage_audit_blocked")
            split_engine = build_split_engine(
                splits=splits,
                frame=split_frame,
                selected_path=selected_path,
                feature_contract_hash=feature_contract_hash,
                dataset_hash=dataset_hash,
                target_store_hash=target_store_hash,
                embargo_seconds=embargo_seconds,
                leakage=leakage_audit,
                baseline=baseline_summary,
                validation_errors=validation_errors,
            )

    status = "blocked" if validation_errors else "ok"
    reason = "walkforward_split_engine_ready" if status == "ok" else validation_errors[0]
    public_splits = [public_split(split) for split in splits]
    total_train_rows_after_purge = int(sum(split["train_row_count_after_purge"] for split in public_splits))
    total_validation_rows = int(sum(split["validation_row_count"] for split in public_splits))
    total_test_rows = int(sum(split["test_row_count"] for split in public_splits))
    output_paths = {
        "split_engine_json": str(resolve(root, output_json_path, DEFAULT_OUTPUT_JSON)),
        "split_engine_markdown": str(resolve(root, output_markdown_path, DEFAULT_OUTPUT_MD)),
        "baseline_json": str(resolve(root, baseline_json_path, DEFAULT_BASELINE_JSON)),
        "baseline_markdown": str(resolve(root, baseline_markdown_path, DEFAULT_BASELINE_MD)),
    }
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "selected_dataset_path": str(selected_path) if selected_path is not None else None,
        "selected_dataset_rows": int(len(source_frame)) if source_frame is not None else 0,
        "feature_contract_hash": feature_contract_hash,
        "dataset_hash": dataset_hash,
        "target_store_hash": target_store_hash,
        "split_engine_status": "blocked" if validation_errors else "ok",
        "split_engine_hash": split_engine.get("split_engine_hash") if split_engine else None,
        "split_count": len(public_splits),
        "total_train_rows_after_purge": total_train_rows_after_purge,
        "total_validation_rows": total_validation_rows,
        "total_test_rows": total_test_rows,
        "purge_applied": bool(public_splits),
        "embargo_applied": bool(public_splits),
        "embargo_seconds": int(embargo_seconds),
        "temporal_overlap_count": leakage_audit["temporal_overlap_count"],
        "embargo_violation_count": leakage_audit["embargo_violation_count"],
        "label_interval_overlap_count": leakage_audit["label_interval_overlap_count"],
        "leakage_status": leakage_audit["leakage_status"],
        "future_columns_in_features_count": leakage_audit["future_columns_in_features_count"],
        "target_columns_in_features_count": leakage_audit["target_columns_in_features_count"],
        "outcome_columns_in_features_count": leakage_audit["outcome_columns_in_features_count"],
        "baseline_status": baseline_summary["baseline_status"],
        "no_trade_expected_value": baseline_summary["no_trade_expected_value"],
        "random_deterministic_expected_value": baseline_summary["random_deterministic_expected_value"],
        "always_long_expected_value": baseline_summary["always_long_expected_value"],
        "always_short_expected_value": baseline_summary["always_short_expected_value"],
        "always_allow_expected_value": baseline_summary["always_allow_expected_value"],
        "always_block_expected_value": baseline_summary["always_block_expected_value"],
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": output_paths,
        **safety_flags(),
        "safety_flags": safety_flags(),
        "validation_errors": sorted(set(validation_errors)),
        "split_engine": split_engine
        or empty_split_engine(
            selected_path=selected_path,
            feature_contract_hash=feature_contract_hash,
            dataset_hash=dataset_hash,
            target_store_hash=target_store_hash,
            embargo_seconds=embargo_seconds,
            leakage=leakage_audit,
            baseline=baseline_summary,
            validation_errors=validation_errors,
        ),
    }
    if write:
        write_reports(
            split_engine=report["split_engine"],
            baseline_summary=baseline_summary,
            output_json=Path(output_paths["split_engine_json"]),
            output_md=Path(output_paths["split_engine_markdown"]),
            baseline_json=Path(output_paths["baseline_json"]),
            baseline_md=Path(output_paths["baseline_markdown"]),
        )
        report["write_performed"] = True
    return report


def prepare_split_frame(source_frame: pd.DataFrame, target_store: Mapping[str, Any]) -> pd.DataFrame:
    frame = source_frame.copy().reset_index(drop=True)
    target_records = target_store.get("target_records")
    if isinstance(target_records, list) and target_records:
        target_frame = pd.DataFrame(target_records)
        merge_keys = [key for key in ("order_id", "event_id", "trade_id") if key in frame.columns and key in target_frame.columns]
        if merge_keys:
            frame = frame.merge(target_frame[merge_keys + target_columns(target_frame)], on=merge_keys, how="left", suffixes=("", "_target_store"))
        elif len(target_frame) == len(frame):
            for column in target_columns(target_frame):
                frame[column] = target_frame[column].to_numpy()
    if "target_expected_value_component" not in frame.columns and "net_pnl" in frame.columns:
        frame["target_expected_value_component"] = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0)
    frame["open_time_utc"] = pd.to_datetime(frame.get("open_time_utc"), utc=True, errors="coerce") if "open_time_utc" in frame.columns else pd.NaT
    if "close_time_utc" in frame.columns:
        frame["close_time_utc"] = pd.to_datetime(frame["close_time_utc"], utc=True, errors="coerce")
    elif "target_holding_seconds" in frame.columns:
        holding = pd.to_numeric(frame["target_holding_seconds"], errors="coerce").fillna(0)
        frame["close_time_utc"] = frame["open_time_utc"] + pd.to_timedelta(holding, unit="s")
    return frame.sort_values(["open_time_utc", "close_time_utc"], kind="mergesort").reset_index(drop=True)


def validate_sources(frame: pd.DataFrame, feature_contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if "open_time_utc" not in frame.columns or frame["open_time_utc"].isna().any():
        errors.append("missing_open_time_utc")
    has_close_time = "close_time_utc" in frame.columns and not frame["close_time_utc"].isna().any()
    has_holding = "target_holding_seconds" in frame.columns or "duration_seconds" in frame.columns
    if not has_close_time and not has_holding:
        errors.append("missing_close_time_or_target_holding_seconds")
    if frame.empty:
        errors.append("selected_dataset_empty")
    feature_columns = [str(column) for column in feature_contract.get("feature_columns", []) if isinstance(column, str)]
    future_columns = [column for column in feature_columns if column.lower().startswith("future_ret_")]
    target_features = [column for column in feature_columns if column.lower().startswith("target_")]
    label_features = [column for column in feature_columns if column.lower().startswith("label_")]
    outcome_columns = set(str(column) for column in feature_contract.get("outcome_columns", []))
    outcome_features = [column for column in feature_columns if column in outcome_columns]
    if future_columns:
        errors.append(f"future_ret_columns_in_features:{','.join(sorted(future_columns))}")
    if target_features:
        errors.append(f"target_columns_in_features:{','.join(sorted(target_features))}")
    if label_features:
        errors.append(f"label_columns_in_features:{','.join(sorted(label_features))}")
    if outcome_features:
        errors.append(f"outcome_columns_in_features:{','.join(sorted(outcome_features))}")
    return sorted(set(errors))


def validate_raw_source(frame: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    if "open_time_utc" not in frame.columns:
        errors.append("missing_open_time_utc")
    if "close_time_utc" not in frame.columns and "target_holding_seconds" not in frame.columns and "duration_seconds" not in frame.columns:
        errors.append("missing_close_time_or_target_holding_seconds")
    return errors


def build_walkforward_splits(frame: pd.DataFrame, *, embargo_seconds: int) -> list[dict[str, Any]]:
    row_count = len(frame)
    if row_count < 6:
        return []
    split_count = max(1, min(3, row_count // 6))
    holdout_size = max(1, row_count // 10)
    total_holdout = split_count * holdout_size * 2
    if total_holdout >= row_count:
        holdout_size = max(1, row_count // (split_count * 3))
    splits: list[dict[str, Any]] = []
    for split_number in range(split_count):
        validation_start_idx = row_count - (split_count - split_number) * holdout_size * 2
        validation_end_idx = validation_start_idx + holdout_size
        test_start_idx = validation_end_idx
        test_end_idx = test_start_idx + holdout_size
        if validation_start_idx <= 0 or test_end_idx > row_count:
            continue
        validation_indices = list(range(validation_start_idx, validation_end_idx))
        test_indices = list(range(test_start_idx, test_end_idx))
        train_candidates = list(range(0, validation_start_idx))
        eval_indices = validation_indices + test_indices
        purged_indices = purge_indices(frame, train_candidates, eval_indices)
        after_purge = [index for index in train_candidates if index not in purged_indices]
        embargoed_indices = embargo_indices(frame, after_purge, eval_indices, embargo_seconds)
        train_indices = [index for index in after_purge if index not in embargoed_indices]
        if not train_indices or not validation_indices or not test_indices:
            continue
        split = build_split_record(
            split_number=split_number + 1,
            frame=frame,
            train_candidates=train_candidates,
            train_indices=train_indices,
            validation_indices=validation_indices,
            test_indices=test_indices,
            purged_count=len(purged_indices),
            embargoed_count=len(embargoed_indices),
        )
        splits.append(split)
    return splits


def purge_indices(frame: pd.DataFrame, train_indices: list[int], eval_indices: list[int]) -> set[int]:
    purged: set[int] = set()
    if not train_indices or not eval_indices:
        return purged
    eval_intervals = [(frame.loc[index, "open_time_utc"], frame.loc[index, "close_time_utc"]) for index in eval_indices]
    for train_index in train_indices:
        start = frame.loc[train_index, "open_time_utc"]
        end = frame.loc[train_index, "close_time_utc"]
        if any(interval_intersects(start, end, eval_start, eval_end) for eval_start, eval_end in eval_intervals):
            purged.add(train_index)
    return purged


def embargo_indices(frame: pd.DataFrame, train_indices: list[int], eval_indices: list[int], embargo_seconds: int) -> set[int]:
    embargoed: set[int] = set()
    if not train_indices or not eval_indices:
        return embargoed
    eval_close = frame.loc[eval_indices, "close_time_utc"]
    for eval_end in eval_close:
        embargo_end = eval_end + pd.Timedelta(seconds=embargo_seconds)
        for train_index in train_indices:
            train_start = frame.loc[train_index, "open_time_utc"]
            if eval_end < train_start <= embargo_end:
                embargoed.add(train_index)
    return embargoed


def build_split_record(
    *,
    split_number: int,
    frame: pd.DataFrame,
    train_candidates: list[int],
    train_indices: list[int],
    validation_indices: list[int],
    test_indices: list[int],
    purged_count: int,
    embargoed_count: int,
) -> dict[str, Any]:
    split_id = f"wf_split_{split_number:03d}"
    record: dict[str, Any] = {
        "split_id": split_id,
        "train_start_utc": iso_for_index(frame, train_indices[0], "open_time_utc"),
        "train_end_utc": iso_for_index(frame, train_indices[-1], "close_time_utc"),
        "validation_start_utc": iso_for_index(frame, validation_indices[0], "open_time_utc"),
        "validation_end_utc": iso_for_index(frame, validation_indices[-1], "close_time_utc"),
        "test_start_utc": iso_for_index(frame, test_indices[0], "open_time_utc"),
        "test_end_utc": iso_for_index(frame, test_indices[-1], "close_time_utc"),
        "train_row_count_before_purge": len(train_candidates),
        "train_row_count_after_purge": len(train_indices),
        "purged_row_count": int(purged_count),
        "embargoed_row_count": int(embargoed_count),
        "validation_row_count": len(validation_indices),
        "test_row_count": len(test_indices),
        "train_indices_hash": indices_hash(train_indices),
        "validation_indices_hash": indices_hash(validation_indices),
        "test_indices_hash": indices_hash(test_indices),
        "_train_indices": train_indices,
        "_validation_indices": validation_indices,
        "_test_indices": test_indices,
    }
    record["split_hash"] = split_hash(record)
    return record


def build_split_engine(
    *,
    splits: list[dict[str, Any]],
    frame: pd.DataFrame,
    selected_path: Path,
    feature_contract_hash: str | None,
    dataset_hash: str | None,
    target_store_hash: str | None,
    embargo_seconds: int,
    leakage: Mapping[str, Any],
    baseline: Mapping[str, Any],
    validation_errors: list[str],
) -> dict[str, Any]:
    public_splits = [public_split(split) for split in splits]
    engine: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "split_engine_id": None,
        "split_engine_hash": None,
        "generated_at_utc": utc_now_iso(),
        "feature_contract_hash": feature_contract_hash,
        "dataset_hash": dataset_hash,
        "target_store_hash": target_store_hash,
        "selected_dataset_path": str(selected_path),
        "row_count": int(len(frame)),
        "split_count": len(public_splits),
        "split_policy": {
            "type": "deterministic_walkforward",
            "random_split_used": False,
            "shuffle_used": False,
            "train_window": "strictly_before_validation",
        },
        "purge_policy": {"enabled": True, "method": "label_interval_intersection"},
        "embargo_policy": {"enabled": True, "embargo_seconds": int(embargo_seconds), "minimum_source": "target_barrier_vertical_seconds"},
        "splits": public_splits,
        "leakage_audit": dict(leakage),
        "baseline_summary": dict(baseline),
        "validation_status": "blocked" if validation_errors else "ok",
        "validation_errors": sorted(set(validation_errors)),
        "safety_flags": safety_flags(),
    }
    digest = split_engine_hash(engine)
    engine["split_engine_id"] = f"walkforward_split_engine_{digest[:16]}"
    engine["split_engine_hash"] = digest
    return engine


def empty_split_engine(
    *,
    selected_path: Path | None,
    feature_contract_hash: str | None,
    dataset_hash: str | None,
    target_store_hash: str | None,
    embargo_seconds: int,
    leakage: Mapping[str, Any],
    baseline: Mapping[str, Any],
    validation_errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "split_engine_id": None,
        "split_engine_hash": None,
        "generated_at_utc": utc_now_iso(),
        "feature_contract_hash": feature_contract_hash,
        "dataset_hash": dataset_hash,
        "target_store_hash": target_store_hash,
        "selected_dataset_path": str(selected_path) if selected_path is not None else None,
        "row_count": 0,
        "split_count": 0,
        "split_policy": {"type": "deterministic_walkforward", "random_split_used": False, "shuffle_used": False},
        "purge_policy": {"enabled": True},
        "embargo_policy": {"enabled": True, "embargo_seconds": int(embargo_seconds)},
        "splits": [],
        "leakage_audit": dict(leakage),
        "baseline_summary": dict(baseline),
        "validation_status": "blocked",
        "validation_errors": sorted(set(validation_errors)),
        "safety_flags": safety_flags(),
    }


def select_dataset(root: Path, dataset_path: str | Path | None, dataset_manifest: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[Path] = []
    if dataset_path is not None:
        candidates.append(resolve(root, dataset_path, Path("")))
    manifest_dataset = dataset_manifest.get("selected_training_dataset")
    if isinstance(manifest_dataset, str) and manifest_dataset:
        candidates.append(resolve(root, manifest_dataset, Path("")))
    microbatch_dir = root / DEFAULT_MICROBATCH_DIR
    if microbatch_dir.exists():
        candidates.extend(sorted(microbatch_dir.glob("*.parquet"), reverse=True))
    candidates.append(root / DEFAULT_OUTCOME_EVENTS)
    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        try:
            return {"path": path, "frame": read_frame(path)}
        except (OSError, ValueError, ImportError, json.JSONDecodeError):
            continue
    return {"path": None, "frame": None}


def derive_embargo_seconds(target_store: Mapping[str, Any], *, override: int | None = None) -> int:
    if override is not None:
        return max(int(override), 0)
    config = target_store.get("triple_barrier_config")
    if isinstance(config, Mapping):
        value = config.get("vertical_barrier_seconds")
        if value is not None:
            return max(MINIMUM_EMBARGO_SECONDS, int(value))
    records = target_store.get("target_records")
    if isinstance(records, list) and records:
        values = [int(row.get("target_barrier_vertical_seconds", 0) or 0) for row in records if isinstance(row, Mapping)]
        if values:
            return max(MINIMUM_EMBARGO_SECONDS, max(values))
    return MINIMUM_EMBARGO_SECONDS


def target_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns.astype(str) if column.startswith("target_")]


def indices_hash(indices: list[int]) -> str:
    return hashlib.sha256(json.dumps(list(indices), separators=(",", ":")).encode("utf-8")).hexdigest()


def split_hash(record: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "split_hash" and not key.startswith("_")}
    return stable_hash(payload)


def split_engine_hash(engine: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in engine.items() if key not in {"generated_at_utc", "split_engine_id", "split_engine_hash"}}
    return stable_hash(payload)


def public_split(split: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in split.items() if not key.startswith("_")}


def iso_for_index(frame: pd.DataFrame, index: int, column: str) -> str:
    value = frame.loc[index, column]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return pd.Timestamp(value).isoformat()


def empty_leakage_audit(feature_contract: Mapping[str, Any]) -> dict[str, Any]:
    feature_columns = [str(column) for column in feature_contract.get("feature_columns", []) if isinstance(column, str)]
    outcome_candidates = set(str(column) for column in feature_contract.get("outcome_columns", []))
    outcome_candidates.update(str(column) for column in feature_contract.get("label_columns", []))
    outcome_columns = [column for column in feature_columns if column in outcome_candidates or column.lower().startswith("label_")]
    return {
        "temporal_overlap_count": 0,
        "train_validation_overlap_count": 0,
        "train_test_overlap_count": 0,
        "embargo_violation_count": 0,
        "duplicated_order_id_across_splits_count": 0,
        "label_interval_overlap_count": 0,
        "future_columns_in_features_count": len([column for column in feature_columns if column.lower().startswith("future_ret_")]),
        "target_columns_in_features_count": len([column for column in feature_columns if column.lower().startswith("target_")]),
        "outcome_columns_in_features_count": len(outcome_columns),
        "future_columns_in_features": [],
        "target_columns_in_features": [],
        "outcome_columns_in_features": outcome_columns,
        "leakage_status": "blocked" if outcome_columns else "ok",
    }


def write_reports(
    *,
    split_engine: Mapping[str, Any],
    baseline_summary: Mapping[str, Any],
    output_json: Path,
    output_md: Path,
    baseline_json: Path,
    baseline_md: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    baseline_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(stable_pretty_json(split_engine), encoding="utf-8")
    baseline_json.write_text(stable_pretty_json(baseline_summary), encoding="utf-8")
    output_md.write_text(render_split_engine_markdown(split_engine), encoding="utf-8")
    baseline_md.write_text(render_baseline_markdown(baseline_summary), encoding="utf-8")


def render_split_engine_markdown(split_engine: Mapping[str, Any]) -> str:
    leakage = split_engine.get("leakage_audit", {})
    return "\n".join(
        [
            "# Walk-Forward Anti-Leakage Split Engine V1",
            "",
            f"- Status: `{split_engine.get('validation_status')}`",
            f"- Split engine hash: `{split_engine.get('split_engine_hash')}`",
            f"- Splits: `{split_engine.get('split_count')}`",
            f"- Embargo seconds: `{split_engine.get('embargo_policy', {}).get('embargo_seconds')}`",
            f"- Leakage status: `{leakage.get('leakage_status') if isinstance(leakage, Mapping) else None}`",
            "",
            "This artifact defines auditable splits only. It does not train, register, promote, trade, or change runtime state.",
            "",
        ]
    )


def render_baseline_markdown(baseline: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Walk-Forward Baseline Summary V1",
            "",
            f"- Status: `{baseline.get('baseline_status')}`",
            f"- Rows: `{baseline.get('baseline_row_count')}`",
            f"- Seed: `{baseline.get('baseline_seed')}`",
            f"- No-trade EV: `{baseline.get('no_trade_expected_value')}`",
            f"- Random deterministic EV: `{baseline.get('random_deterministic_expected_value')}`",
            f"- Always allow EV: `{baseline.get('always_allow_expected_value')}`",
            "",
            "Baselines are deterministic accounting references, not trained models.",
            "",
        ]
    )


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=json_safe)


def stable_pretty_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n"


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if not str(path):
        return root
    return path if path.is_absolute() else (root / path)


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        **SAFETY_FALSE_FIELDS,
    }
