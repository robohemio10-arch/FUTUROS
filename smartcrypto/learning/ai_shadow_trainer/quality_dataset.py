"""Dataset adapter for AI Shadow quality veto challenger research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_trainer.dataset_adapter import (
    RankingDatasetBundle,
    load_ranking_dataset_bundle,
    read_json_if_exists,
    resolve,
)

DEFAULT_QLIB_TRAINER_REPORT = Path("data/reports/qlib_institutional_ranking_trainer_v1.json")


def load_quality_dataset_bundle(
    *,
    project_root: str | Path,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    target_store_path: str | Path | None = None,
    walkforward_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    qlib_trainer_report_path: str | Path | None = None,
) -> tuple[RankingDatasetBundle, dict[str, Any], str | None]:
    root = Path(project_root).resolve()
    bundle = load_ranking_dataset_bundle(
        project_root=root,
        feature_contract_path=feature_contract_path,
        dataset_manifest_path=dataset_manifest_path,
        target_store_path=target_store_path,
        walkforward_path=walkforward_path,
        baseline_path=baseline_path,
        dataset_path=dataset_path,
    )
    qlib_report_path = resolve(root, qlib_trainer_report_path, DEFAULT_QLIB_TRAINER_REPORT)
    qlib_report = read_json_if_exists(qlib_report_path)
    qlib_hash = file_sha256(qlib_report_path) if qlib_report_path.exists() else None
    return bundle, qlib_report, qlib_hash


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_entries(root: Path, bundle: RankingDatasetBundle, qlib_report_hash: str | None) -> list[dict[str, Any]]:
    paths = [
        root / "data/reports/ai_unified_feature_contract_v1.json",
        root / "data/reports/ai_unified_dataset_manifest_v1.json",
        root / "data/reports/financial_label_target_store_v1.json",
        root / "data/reports/financial_label_target_store_summary_v1.json",
        root / "data/reports/walkforward_anti_leakage_split_engine_v1.json",
        root / "data/reports/walkforward_baseline_summary_v1.json",
        root / DEFAULT_QLIB_TRAINER_REPORT,
    ]
    if bundle.selected_dataset_path is not None:
        paths.append(bundle.selected_dataset_path)
    return [
        {
            "path": str(path.resolve()),
            "exists": path.exists(),
            "sha256": qlib_report_hash if path == root / DEFAULT_QLIB_TRAINER_REPORT and path.exists() else None,
        }
        for path in paths
    ]


def stable_json_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
