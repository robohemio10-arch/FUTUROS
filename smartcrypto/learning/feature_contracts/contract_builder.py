"""Unified feature contract and dataset manifest orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .dataset_manifest import build_dataset_manifest, file_sha256, read_frame
from .feature_contract import build_feature_contract
from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso

COMBINED_SCHEMA_VERSION = "ai_unified_feature_contract_and_dataset_manifest_v1"
DEFAULT_CONTRACT_JSON = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_CONTRACT_MD = Path("data/reports/ai_unified_feature_contract_v1.md")
DEFAULT_MANIFEST_JSON = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_MANIFEST_MD = Path("data/reports/ai_unified_dataset_manifest_v1.md")


def build_unified_feature_contract_report(
    *,
    project_root: str | Path,
    write: bool = False,
    contract_json_path: str | Path | None = None,
    contract_markdown_path: str | Path | None = None,
    manifest_json_path: str | Path | None = None,
    manifest_markdown_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    sources = discover_sources(root)
    selected = select_training_dataset(sources)
    selected_path = selected["path"]
    selected_frame = selected["frame"]

    if selected_path is None or selected_frame is None:
        contract = empty_contract(sources)
        manifest = empty_manifest(sources)
    else:
        contract = build_feature_contract(
            selected_frame,
            source_datasets=[str(source["path"]) for source in sources if source["exists"]],
        )
        manifest = build_dataset_manifest(
            selected_frame,
            selected_dataset_path=selected_path,
            source_paths=[source["path"] for source in sources if source["exists"]],
            feature_contract_hash=contract["contract_hash"],
            label_columns=contract["label_columns"],
        )

    validation_errors = sorted(set([*contract["validation_errors"], *manifest["validation_errors"]]))
    status = "blocked" if validation_errors else "ok"
    reason = "feature_contract_and_dataset_manifest_ready" if status == "ok" else validation_errors[0]
    output_paths = {
        "feature_contract_json": str(resolve(root, contract_json_path, DEFAULT_CONTRACT_JSON)),
        "feature_contract_markdown": str(resolve(root, contract_markdown_path, DEFAULT_CONTRACT_MD)),
        "dataset_manifest_json": str(resolve(root, manifest_json_path, DEFAULT_MANIFEST_JSON)),
        "dataset_manifest_markdown": str(resolve(root, manifest_markdown_path, DEFAULT_MANIFEST_MD)),
    }

    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": COMBINED_SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "input_sources": public_sources(sources),
        "selected_dataset_path": str(selected_path) if selected_path is not None else None,
        "selected_dataset_rows": int(len(selected_frame)) if selected_frame is not None else 0,
        "selected_dataset_columns": int(len(selected_frame.columns)) if selected_frame is not None else 0,
        "feature_contract_status": contract["validation_status"],
        "dataset_manifest_status": manifest["validation_status"],
        "contract_hash": contract.get("contract_hash"),
        "schema_hash": contract.get("schema_hash"),
        "dataset_hash": manifest.get("dataset_hash"),
        "feature_column_count": len(contract["feature_columns"]),
        "label_column_count": len(contract["label_columns"]),
        "outcome_column_count": len(contract["outcome_columns"]),
        "metadata_column_count": len(contract["metadata_columns"]),
        "identifier_column_count": len(contract["identifier_columns"]),
        "forbidden_column_count": len(contract["forbidden_columns"]),
        "forbidden_columns_detected": contract["forbidden_columns"],
        "leakage_columns_detected": sorted(set(contract["outcome_columns"] + contract["label_columns"] + contract["forbidden_columns"])),
        "future_ret_columns_detected": contract["future_ret_columns_detected"],
        "feature_columns": contract["feature_columns"],
        "label_columns": contract["label_columns"],
        "outcome_columns": contract["outcome_columns"],
        "metadata_columns": contract["metadata_columns"],
        "identifier_columns": contract["identifier_columns"],
        "forbidden_columns": contract["forbidden_columns"],
        "deterministic_feature_order": contract["deterministic_feature_order"],
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
        "validation_errors": validation_errors,
        "feature_contract": contract,
        "dataset_manifest": manifest,
    }

    if write:
        write_reports(
            contract=contract,
            manifest=manifest,
            contract_json=Path(output_paths["feature_contract_json"]),
            contract_md=Path(output_paths["feature_contract_markdown"]),
            manifest_json=Path(output_paths["dataset_manifest_json"]),
            manifest_md=Path(output_paths["dataset_manifest_markdown"]),
        )
        report["write_performed"] = True
    return report


def discover_sources(root: Path) -> list[dict[str, Any]]:
    paths: list[tuple[str, Path]] = []
    microbatch_dir = root / "data/feedback/training_microbatches"
    if microbatch_dir.exists():
        for path in sorted(microbatch_dir.glob("*.parquet"), reverse=True):
            paths.append(("training_microbatch", path))
    paths.extend(
        [
            ("outcome_events", root / "data/feedback/outcome_events.parquet"),
            ("paper_closed_trades_incremental", root / "data/feedback/paper_closed_trades_incremental.parquet"),
            ("paper_autolearning_foundation_summary", root / "data/reports/paper_autolearning_foundation_summary.json"),
            ("paper_feedback_master_consolidation_preview", root / "data/reports/paper_feedback_master_consolidation_preview_v1.json"),
        ]
    )
    sources: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for source_id, path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        frame, status, reason = try_read_frame(resolved)
        sources.append(
            {
                "source_id": source_id,
                "path": resolved,
                "exists": resolved.exists(),
                "status": status,
                "reason": reason,
                "rows": int(len(frame)) if frame is not None else 0,
                "columns": int(len(frame.columns)) if frame is not None else 0,
                "frame": frame,
                "sha256": file_sha256(resolved) if resolved.exists() and resolved.is_file() else None,
            }
        )
    return sources


def try_read_frame(path: Path) -> tuple[pd.DataFrame | None, str, str]:
    if not path.exists() or not path.is_file():
        return None, "missing", "source_missing"
    try:
        frame = read_frame(path)
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        return None, "blocked", f"source_unreadable:{type(exc).__name__}"
    return frame, "ok", "source_loaded"


def select_training_dataset(sources: list[dict[str, Any]]) -> dict[str, Any]:
    fallback: dict[str, Any] = {"path": None, "frame": None}
    for source in sources:
        frame = source.get("frame")
        if frame is None or frame.empty:
            continue
        if fallback["frame"] is None:
            fallback = {"path": source["path"], "frame": frame}
        contract = build_feature_contract(frame, source_datasets=[str(source["path"])])
        if contract["validation_status"] == "ok":
            return {"path": source["path"], "frame": frame}
    return fallback


def empty_contract(sources: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame()
    contract = build_feature_contract(frame, source_datasets=[str(source["path"]) for source in sources if source["exists"]])
    return contract


def empty_manifest(sources: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame()
    return build_dataset_manifest(
        frame,
        selected_dataset_path=Path("<missing_dataset>"),
        source_paths=[source["path"] for source in sources if source["exists"]],
        feature_contract_hash="",
        label_columns=[],
    )


def public_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("source_id", "path", "exists", "status", "reason", "rows", "columns", "sha256")
    return [{key: str(source[key]) if key == "path" else source[key] for key in keys} for source in sources]


def write_reports(
    *,
    contract: dict[str, Any],
    manifest: dict[str, Any],
    contract_json: Path,
    contract_md: Path,
    manifest_json: Path,
    manifest_md: Path,
) -> None:
    for path, payload in ((contract_json, contract), (manifest_json, manifest)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    contract_md.parent.mkdir(parents=True, exist_ok=True)
    manifest_md.parent.mkdir(parents=True, exist_ok=True)
    contract_md.write_text(render_contract_markdown(contract), encoding="utf-8")
    manifest_md.write_text(render_manifest_markdown(manifest), encoding="utf-8")


def render_contract_markdown(contract: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# AI Unified Feature Contract V1",
            "",
            f"- Status: `{contract.get('validation_status')}`",
            f"- Contract hash: `{contract.get('contract_hash')}`",
            f"- Schema hash: `{contract.get('schema_hash')}`",
            f"- Feature columns: `{len(contract.get('feature_columns', []))}`",
            f"- Label columns: `{len(contract.get('label_columns', []))}`",
            f"- Outcome columns: `{len(contract.get('outcome_columns', []))}`",
            f"- Forbidden columns: `{len(contract.get('forbidden_columns', []))}`",
            "",
            "Outcome, label, identifier and forbidden columns are not authorized as model features.",
            "",
        ]
    )


def render_manifest_markdown(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# AI Unified Dataset Manifest V1",
            "",
            f"- Status: `{manifest.get('validation_status')}`",
            f"- Dataset hash: `{manifest.get('dataset_hash')}`",
            f"- Selected dataset: `{manifest.get('selected_training_dataset')}`",
            f"- Rows: `{manifest.get('row_count')}`",
            f"- Columns: `{manifest.get('column_count')}`",
            f"- Symbols: `{', '.join(manifest.get('symbols', []))}`",
            f"- Sides: `{', '.join(manifest.get('sides', []))}`",
            "",
            "The manifest is read-only evidence. It does not train, promote, register or change runtime state.",
            "",
        ]
    )


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "live_trading_enabled": False,
        "training_requested": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
    }
