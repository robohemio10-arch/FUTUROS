"""Build financial label target-store evidence without training or runtime writes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import file_sha256, frame_hash, read_frame
from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso

from .financial_labels import TARGET_COLUMNS, build_cost_components, build_target_frame, closed_trade_rows, validate_target_source
from .triple_barrier_schema import (
    CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER,
    INTRABAR_PRICE_PATH_AVAILABLE,
    TRIPLE_BARRIER_MODE,
    triple_barrier_config,
)

SCHEMA_VERSION = "financial_label_and_triple_barrier_target_store_v1"
TARGET_STORE_SCHEMA_VERSION = "financial_label_target_store_v1"

DEFAULT_FEATURE_CONTRACT_JSON = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_DATASET_MANIFEST_JSON = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_OUTPUT_JSON = Path("data/reports/financial_label_target_store_v1.json")
DEFAULT_OUTPUT_MD = Path("data/reports/financial_label_target_store_v1.md")
DEFAULT_SUMMARY_JSON = Path("data/reports/financial_label_target_store_summary_v1.json")
DEFAULT_SUMMARY_MD = Path("data/reports/financial_label_target_store_summary_v1.md")
DEFAULT_MICROBATCH_DIR = Path("data/feedback/training_microbatches")
FALLBACK_SOURCES = [
    Path("data/feedback/outcome_events.parquet"),
    Path("data/feedback/paper_closed_trades_incremental.parquet"),
    Path("data/reports/paper_feedback_master_consolidation_preview_v1.json"),
]


def build_financial_label_target_store_report(
    *,
    project_root: str | Path,
    write: bool = False,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    summary_json_path: str | Path | None = None,
    summary_markdown_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build report and optional JSON/Markdown target-store artifacts."""

    root = Path(project_root).resolve()
    feature_contract = read_json_if_exists(resolve(root, feature_contract_path, DEFAULT_FEATURE_CONTRACT_JSON))
    dataset_manifest = read_json_if_exists(resolve(root, dataset_manifest_path, DEFAULT_DATASET_MANIFEST_JSON))
    selection = select_dataset(root, dataset_path, dataset_manifest)
    selected_path = selection["path"]
    source_paths = discover_input_sources(root, selected_path, feature_contract_path, dataset_manifest_path, dataset_manifest)

    validation_errors: list[str] = []
    target_store: dict[str, Any] | None = None
    target_frame = pd.DataFrame()
    source_frame = selection["frame"]
    reason = "target_store_ready"
    if selected_path is None or source_frame is None:
        validation_errors.append("missing_selected_dataset")
        reason = "missing_selected_dataset"
    else:
        validation_errors.extend(validate_target_source(source_frame))
        closed = closed_trade_rows(source_frame)
        if closed.empty:
            validation_errors.append("no_closed_trade_rows")
        if validation_errors:
            reason = validation_errors[0]
        else:
            config = triple_barrier_config()
            target_frame = build_target_frame(
                source_frame,
                upper_barrier_pct=config["upper_barrier_pct"],
                lower_barrier_pct=config["lower_barrier_pct"],
                vertical_barrier_seconds=config["vertical_barrier_seconds"],
            )
            target_store = build_target_store(
                target_frame=target_frame,
                source_frame=source_frame,
                selected_path=selected_path,
                source_paths=source_paths,
                feature_contract=feature_contract,
                dataset_manifest=dataset_manifest,
                triple_barrier=config,
            )
            validation_errors.extend(target_store["validation_errors"])
            reason = "target_store_ready" if not validation_errors else validation_errors[0]

    status = "blocked" if validation_errors else "ok"
    output_paths = {
        "target_store_json": str(resolve(root, output_json_path, DEFAULT_OUTPUT_JSON)),
        "target_store_markdown": str(resolve(root, output_markdown_path, DEFAULT_OUTPUT_MD)),
        "summary_json": str(resolve(root, summary_json_path, DEFAULT_SUMMARY_JSON)),
        "summary_markdown": str(resolve(root, summary_markdown_path, DEFAULT_SUMMARY_MD)),
    }
    summary = build_summary(target_frame)
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "input_sources": public_sources(source_paths),
        "selected_dataset_path": str(selected_path) if selected_path is not None else None,
        "selected_dataset_rows": int(len(source_frame)) if source_frame is not None else 0,
        "feature_contract_hash": feature_contract.get("contract_hash"),
        "dataset_hash": dataset_manifest.get("dataset_hash") or (frame_hash(source_frame) if source_frame is not None else None),
        "target_store_status": "blocked" if validation_errors else "ok",
        "target_store_hash": target_store.get("target_store_hash") if target_store else None,
        "target_row_count": int(len(target_frame)),
        "target_column_count": len(TARGET_COLUMNS),
        "target_columns": list(TARGET_COLUMNS),
        "label_distribution": target_store.get("label_distribution", {}) if target_store else {},
        "triple_barrier_mode": TRIPLE_BARRIER_MODE,
        "intrabar_price_path_available": INTRABAR_PRICE_PATH_AVAILABLE,
        "candle_path_required_for_full_triple_barrier": CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER,
        "target_roi_hit_count": summary["target_roi_hit_count"],
        "target_stoploss_hit_count": summary["target_stoploss_hit_count"],
        "target_time_exit_count": summary["target_time_exit_count"],
        "positive_target_count": summary["positive_target_count"],
        "negative_target_count": summary["negative_target_count"],
        "breakeven_target_count": summary["breakeven_target_count"],
        "avg_target_net_pnl": summary["avg_target_net_pnl"],
        "avg_target_profit_ratio": summary["avg_target_profit_ratio"],
        "expected_value_proxy_total": summary["expected_value_proxy_total"],
        "expected_value_proxy_mean": summary["expected_value_proxy_mean"],
        "cost_total": summary["cost_total"],
        "risk_penalty_total": summary["risk_penalty_total"],
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": output_paths,
        "training_requested": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        **safety_flags(),
        "safety_flags": safety_flags(),
        "validation_errors": sorted(set(validation_errors)),
        "target_store": target_store or empty_target_store(feature_contract, dataset_manifest, source_paths, validation_errors),
    }

    if write:
        write_reports(
            target_store=report["target_store"],
            summary={key: value for key, value in report.items() if key != "target_store"},
            output_json=Path(output_paths["target_store_json"]),
            output_md=Path(output_paths["target_store_markdown"]),
            summary_json=Path(output_paths["summary_json"]),
            summary_md=Path(output_paths["summary_markdown"]),
        )
        report["write_performed"] = True
    return report


def build_target_store(
    *,
    target_frame: pd.DataFrame,
    source_frame: pd.DataFrame,
    selected_path: Path,
    source_paths: list[Path],
    feature_contract: Mapping[str, Any],
    dataset_manifest: Mapping[str, Any],
    triple_barrier: Mapping[str, Any],
) -> dict[str, Any]:
    records = records_for_json(target_frame)
    target_dtypes = {column: str(target_frame[column].dtype) for column in TARGET_COLUMNS}
    target_null_counts = {column: int(target_frame[column].isna().sum()) for column in TARGET_COLUMNS}
    label_distribution = build_label_distribution(target_frame)
    cost_policy = build_cost_policy(source_frame)
    risk_policy = {"risk_penalty": "max(0, -target_net_pnl)", "changes_risk": False}
    expected_value_config = {
        "gross_pnl_source": "gross_pnl_if_available_else_net_pnl",
        "net_pnl_source": "net_pnl",
        "profit_ratio_source": "profit_ratio",
        "cost_total": "trading_fee_abs + funding_fee_abs + slippage_estimate_abs + spread_estimate_abs",
        "risk_penalty": risk_policy["risk_penalty"],
        "expected_value_proxy": "target_net_pnl - target_cost_component - target_risk_penalty_component",
    }
    validation_errors = validate_target_store(target_frame, feature_contract)
    store: dict[str, Any] = {
        "schema_version": TARGET_STORE_SCHEMA_VERSION,
        "target_store_id": None,
        "target_store_hash": None,
        "generated_at_utc": utc_now_iso(),
        "feature_contract_hash": feature_contract.get("contract_hash"),
        "dataset_hash": dataset_manifest.get("dataset_hash") or frame_hash(source_frame),
        "source_paths": [str(path) for path in source_paths],
        "source_hashes": {str(path): file_sha256(path) for path in source_paths if path.exists() and path.is_file()},
        "selected_dataset_path": str(selected_path),
        "row_count": int(len(target_frame)),
        "target_columns": list(TARGET_COLUMNS),
        "target_dtypes": target_dtypes,
        "target_null_counts": target_null_counts,
        "label_distribution": label_distribution,
        "triple_barrier_config": dict(triple_barrier),
        "triple_barrier_mode": TRIPLE_BARRIER_MODE,
        "intrabar_price_path_available": INTRABAR_PRICE_PATH_AVAILABLE,
        "candle_path_required_for_full_triple_barrier": CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER,
        "expected_value_config": expected_value_config,
        "cost_policy": cost_policy,
        "risk_policy": risk_policy,
        "validation_status": "blocked" if validation_errors else "ok",
        "validation_errors": validation_errors,
        "safety_flags": safety_flags(),
        "target_records": records,
    }
    digest = target_store_hash(store)
    store["target_store_id"] = f"target_store_{digest[:16]}"
    store["target_store_hash"] = digest
    return store


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
    candidates.extend(root / path for path in FALLBACK_SOURCES)

    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        try:
            frame = read_frame(path)
        except (OSError, ValueError, ImportError, json.JSONDecodeError):
            continue
        return {"path": path, "frame": frame}
    return {"path": None, "frame": None}


def discover_input_sources(
    root: Path,
    selected_path: Path | None,
    feature_contract_path: str | Path | None,
    dataset_manifest_path: str | Path | None,
    dataset_manifest: Mapping[str, Any],
) -> list[Path]:
    paths = [
        resolve(root, feature_contract_path, DEFAULT_FEATURE_CONTRACT_JSON),
        resolve(root, dataset_manifest_path, DEFAULT_DATASET_MANIFEST_JSON),
    ]
    if selected_path is not None:
        paths.append(selected_path)
    for source in dataset_manifest.get("source_paths", []):
        if isinstance(source, str) and source:
            paths.append(resolve(root, source, Path("")))
    paths.extend(root / path for path in FALLBACK_SOURCES)
    seen: set[Path] = set()
    output: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        output.append(resolved)
    return output


def build_summary(target_frame: pd.DataFrame) -> dict[str, Any]:
    if target_frame.empty:
        return {
            "target_roi_hit_count": 0,
            "target_stoploss_hit_count": 0,
            "target_time_exit_count": 0,
            "positive_target_count": 0,
            "negative_target_count": 0,
            "breakeven_target_count": 0,
            "avg_target_net_pnl": 0.0,
            "avg_target_profit_ratio": 0.0,
            "expected_value_proxy_total": 0.0,
            "expected_value_proxy_mean": 0.0,
            "cost_total": 0.0,
            "risk_penalty_total": 0.0,
        }
    return {
        "target_roi_hit_count": int(target_frame["target_roi_hit"].sum()),
        "target_stoploss_hit_count": int(target_frame["target_stoploss_hit"].sum()),
        "target_time_exit_count": int(target_frame["target_time_exit"].sum()),
        "positive_target_count": int((target_frame["target_label_sign"] > 0).sum()),
        "negative_target_count": int((target_frame["target_label_sign"] < 0).sum()),
        "breakeven_target_count": int((target_frame["target_label_sign"] == 0).sum()),
        "avg_target_net_pnl": rounded_mean(target_frame["target_net_pnl"]),
        "avg_target_profit_ratio": rounded_mean(target_frame["target_profit_ratio"]),
        "expected_value_proxy_total": rounded_sum(target_frame["target_expected_value_component"]),
        "expected_value_proxy_mean": rounded_mean(target_frame["target_expected_value_component"]),
        "cost_total": rounded_sum(target_frame["target_cost_component"]),
        "risk_penalty_total": rounded_sum(target_frame["target_risk_penalty_component"]),
    }


def build_label_distribution(target_frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {}
    for column in ("target_label_sign", "target_win_loss", "target_triple_barrier_label", "target_net_pnl_bucket"):
        counts = target_frame[column].astype("string").fillna("<NA>").value_counts(dropna=False).sort_index()
        distribution[column] = {str(key): int(value) for key, value in counts.items()}
    return distribution


def build_cost_policy(source_frame: pd.DataFrame) -> dict[str, Any]:
    components = build_cost_components(source_frame)
    return {
        "trading_fee_available": "trading_fee" in source_frame.columns,
        "funding_fee_available": "funding_fee" in source_frame.columns,
        "slippage_estimate_available": "slippage_estimate" in source_frame.columns,
        "spread_estimate_available": "spread_estimate" in source_frame.columns,
        "slippage_estimated": False,
        "spread_estimated": False,
        "missing_slippage_policy": "zero_not_estimated",
        "missing_spread_policy": "zero_not_estimated",
        "cost_total": rounded_sum(components["cost_total"]),
    }


def validate_target_store(target_frame: pd.DataFrame, feature_contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if target_frame.empty:
        errors.append("target_store_empty")
    missing_targets = [column for column in TARGET_COLUMNS if column not in target_frame.columns]
    if missing_targets:
        errors.append(f"missing_target_columns:{','.join(sorted(missing_targets))}")
    feature_columns = feature_contract.get("feature_columns", [])
    if isinstance(feature_columns, list):
        target_features = [column for column in feature_columns if str(column).startswith("target_")]
        if target_features:
            errors.append(f"target_columns_in_feature_contract:{','.join(sorted(target_features))}")
    return sorted(set(errors))


def empty_target_store(
    feature_contract: Mapping[str, Any],
    dataset_manifest: Mapping[str, Any],
    source_paths: list[Path],
    validation_errors: list[str],
) -> dict[str, Any]:
    store = {
        "schema_version": TARGET_STORE_SCHEMA_VERSION,
        "target_store_id": None,
        "target_store_hash": None,
        "generated_at_utc": utc_now_iso(),
        "feature_contract_hash": feature_contract.get("contract_hash"),
        "dataset_hash": dataset_manifest.get("dataset_hash"),
        "source_paths": [str(path) for path in source_paths],
        "source_hashes": {str(path): file_sha256(path) for path in source_paths if path.exists() and path.is_file()},
        "row_count": 0,
        "target_columns": list(TARGET_COLUMNS),
        "target_dtypes": {},
        "target_null_counts": {},
        "label_distribution": {},
        "triple_barrier_config": triple_barrier_config(),
        "triple_barrier_mode": TRIPLE_BARRIER_MODE,
        "intrabar_price_path_available": INTRABAR_PRICE_PATH_AVAILABLE,
        "candle_path_required_for_full_triple_barrier": CANDLE_PATH_REQUIRED_FOR_FULL_TRIPLE_BARRIER,
        "expected_value_config": {},
        "cost_policy": {},
        "risk_policy": {},
        "validation_status": "blocked",
        "validation_errors": sorted(set(validation_errors)),
        "safety_flags": safety_flags(),
        "target_records": [],
    }
    return store


def write_reports(
    *,
    target_store: Mapping[str, Any],
    summary: Mapping[str, Any],
    output_json: Path,
    output_md: Path,
    summary_json: Path,
    summary_md: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(stable_pretty_json(target_store), encoding="utf-8")
    summary_json.write_text(stable_pretty_json(summary), encoding="utf-8")
    output_md.write_text(render_target_store_markdown(target_store), encoding="utf-8")
    summary_md.write_text(render_summary_markdown(summary), encoding="utf-8")


def render_target_store_markdown(target_store: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Financial Label Target Store V1",
            "",
            f"- Status: `{target_store.get('validation_status')}`",
            f"- Target store hash: `{target_store.get('target_store_hash')}`",
            f"- Rows: `{target_store.get('row_count')}`",
            f"- Triple barrier mode: `{target_store.get('triple_barrier_mode')}`",
            f"- Intrabar price path available: `{target_store.get('intrabar_price_path_available')}`",
            f"- Full triple barrier requires candle path: `{target_store.get('candle_path_required_for_full_triple_barrier')}`",
            "",
            "This artifact is report-only evidence. It does not train, register, promote, trade, or change runtime state.",
            "",
        ]
    )


def render_summary_markdown(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Financial Label Target Store Summary V1",
            "",
            f"- Status: `{summary.get('status')}`",
            f"- Reason: `{summary.get('reason')}`",
            f"- Target rows: `{summary.get('target_row_count')}`",
            f"- Target columns: `{summary.get('target_column_count')}`",
            f"- Positive targets: `{summary.get('positive_target_count')}`",
            f"- Negative targets: `{summary.get('negative_target_count')}`",
            f"- Breakeven targets: `{summary.get('breakeven_target_count')}`",
            f"- Expected value proxy total: `{summary.get('expected_value_proxy_total')}`",
            "",
            "Closed-trade-derived labels are separated from feature columns and are not authorized as model inputs.",
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


def public_sources(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path),
            "exists": path.exists(),
            "sha256": file_sha256(path) if path.exists() and path.is_file() else None,
        }
        for path in paths
    ]


def records_for_json(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append({str(key): json_safe(value) for key, value in row.items()})
    return records


def target_store_hash(store: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in store.items()
        if key not in {"generated_at_utc", "target_store_id", "target_store_hash"}
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def stable_pretty_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n"


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=json_safe)


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


def rounded_mean(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).mean()), 10)


def rounded_sum(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum()), 10)


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if not str(path):
        return root
    return path if path.is_absolute() else (root / path)


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "training_requested": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "writes_parquet": False,
    }
