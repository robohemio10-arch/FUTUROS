"""Read-only evidence loading for the canonical auto-learning closeout."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceSource:
    """A single JSON evidence source used by the closeout pack."""

    stage_id: str
    stage_name: str
    relative_path: str
    required: bool = True


@dataclass(frozen=True)
class LoadedEvidence:
    """Loaded source plus deterministic file metadata."""

    source: EvidenceSource
    path: Path
    exists: bool
    sha256: str | None
    payload: dict[str, Any]
    load_error: str | None = None


CANONICAL_EVIDENCE_SOURCES: tuple[EvidenceSource, ...] = (
    EvidenceSource("foundation_loop", "Paper feedback foundation loop", "data/reports/paper_autolearning_foundation_summary.json"),
    EvidenceSource(
        "master_consolidation",
        "Paper feedback master consolidation",
        "data/reports/paper_feedback_master_consolidation_preview_v1.json",
    ),
    EvidenceSource("scheduler", "Paper auto-learning scheduler", "data/reports/paper_autolearning_foundation_summary.json"),
    EvidenceSource("feature_contract", "FeatureContract", "data/reports/ai_unified_feature_contract_v1.json"),
    EvidenceSource("dataset_manifest", "DatasetManifest", "data/reports/ai_unified_dataset_manifest_v1.json"),
    EvidenceSource("target_store", "Financial TargetStore", "data/reports/financial_label_target_store_v1.json"),
    EvidenceSource(
        "target_store_summary",
        "Financial TargetStore summary",
        "data/reports/financial_label_target_store_summary_v1.json",
    ),
    EvidenceSource(
        "walkforward_split",
        "WalkForward anti-leakage split engine",
        "data/reports/walkforward_anti_leakage_split_engine_v1.json",
    ),
    EvidenceSource("walkforward_baseline", "WalkForward baseline summary", "data/reports/walkforward_baseline_summary_v1.json"),
    EvidenceSource("qlib_backend_gate", "Qlib research backend gate", "data/reports/qlib_research_backend_gate_v1.json"),
    EvidenceSource("qlib_trainer", "Qlib institutional ranking trainer", "data/reports/qlib_institutional_ranking_trainer_v1.json"),
    EvidenceSource("ai_shadow_trainer", "AI Shadow quality veto trainer", "data/reports/ai_shadow_quality_veto_trainer_v1.json"),
    EvidenceSource("ai_shadow_metrics", "AI Shadow quality veto metrics", "data/reports/ai_shadow_quality_veto_metrics_v1.json"),
    EvidenceSource("project_manifest", "Versioned project manifest", "PROJECT_MANIFEST_CLEAN.json"),
)


def load_evidence_sources(project_root: Path, sources: tuple[EvidenceSource, ...] = CANONICAL_EVIDENCE_SOURCES) -> list[LoadedEvidence]:
    """Load all configured evidence JSON files without side effects."""

    loaded: list[LoadedEvidence] = []
    for source in sources:
        path = (project_root / source.relative_path).resolve()
        exists = path.exists() and path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None
        digest: str | None = None
        if exists:
            digest = file_sha256(path)
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
            except json.JSONDecodeError as exc:
                load_error = f"invalid_json:{exc.msg}"
            except OSError as exc:
                load_error = f"io_error:{exc.__class__.__name__}"
        loaded.append(
            LoadedEvidence(
                source=source,
                path=path,
                exists=exists,
                sha256=digest,
                payload=payload,
                load_error=load_error,
            )
        )
    return loaded


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
