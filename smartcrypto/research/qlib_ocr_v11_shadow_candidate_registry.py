"""Research-only registry for the Qlib OCR V1.1 shadow candidate."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY_VERSION = 1
REGISTRY_SCOPE = "qlib_ocr_v11_research_shadow_only"
MODEL_ID = "qlib_ocr_v11_supervised_candidate"
MODEL_VERSION = "research_shadow_v1"
SOURCE_BRANCH = "codex/qlib-ocr-v11-supervised-training-lab-v1"
REGISTRY_BRANCH = "codex/qlib-ocr-v11-shadow-model-candidate-registry-v1"
APPROVED_RESEARCH_DECISIONS = {
    "CANDIDATO_RESEARCH_ONLY",
    "APPROVED_FOR_RESEARCH",
    "APROVADO_PARA_RESEARCH",
}

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_training": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "registers_model": False,
    "auto_promote": False,
    "production_enabled": False,
}

UNSAFE_TRUE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "updates_freqtrade",
    "updates_qlib_runtime",
    "updates_risk_manager",
    "runs_ai_shadow_incremental",
    "cleans_sqlite",
    "registers_model",
    "auto_promote",
    "production_enabled",
)


@dataclass(frozen=True)
class ShadowCandidateRegistryPaths:
    project_root: Path
    training_summary_path: Path
    executive_pack_path: Path
    model_path: Path
    registry_output_path: Path
    report_output_path: Path


@dataclass(frozen=True)
class ShadowCandidateRegistryConfig:
    strict: bool = False
    model_id: str = MODEL_ID
    model_version: str = MODEL_VERSION
    registry_scope: str = REGISTRY_SCOPE


@dataclass(frozen=True)
class ShadowCandidateRegistryResult:
    report: dict[str, Any]
    registry: dict[str, Any]


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_paths(
    project_root: str | Path,
    *,
    training_summary: str | Path | None = None,
    executive_pack: str | Path | None = None,
    model_path: str | Path | None = None,
    registry_output: str | Path | None = None,
    report_output: str | Path | None = None,
) -> ShadowCandidateRegistryPaths:
    root = Path(project_root).expanduser().resolve()
    return ShadowCandidateRegistryPaths(
        project_root=root,
        training_summary_path=_resolve(
            root,
            training_summary,
            root / "data" / "reports" / "qlib_ocr_v11_supervised_training_summary.json",
        ),
        executive_pack_path=_resolve(
            root,
            executive_pack,
            root
            / "data"
            / "reports"
            / "training_reports"
            / "smart_futuros_training_executive_pack.json",
        ),
        model_path=_resolve(
            root,
            model_path,
            root
            / "data"
            / "models"
            / "qlib_ocr_v11"
            / "research"
            / "qlib_ocr_v11_supervised_candidate.joblib",
        ),
        registry_output_path=_resolve(
            root,
            registry_output,
            root
            / "data"
            / "models"
            / "qlib_ocr_v11"
            / "research"
            / "qlib_ocr_v11_shadow_candidate_registry.json",
        ),
        report_output_path=_resolve(
            root,
            report_output,
            root
            / "data"
            / "reports"
            / "qlib_ocr_v11_shadow_model_candidate_registry_report.json",
        ),
    )


def load_json_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_report_must_be_object:{path}")
    return payload


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _metrics(training_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "training_rows": _number(_value(training_summary, "training_rows")),
        "prediction_rows": _number(_value(training_summary, "prediction_rows")),
        "feature_count": _number(_value(training_summary, "feature_count")),
        "valid_folds": _number(
            _value(training_summary, "aggregate_metrics.valid_folds", "valid_folds")
        ),
        "mean_accuracy": _number(
            _value(training_summary, "aggregate_metrics.mean_accuracy", "mean_accuracy")
        ),
        "mean_f1": _number(
            _value(training_summary, "aggregate_metrics.mean_f1", "mean_f1")
        ),
        "mean_roc_auc": _number(
            _value(training_summary, "aggregate_metrics.mean_roc_auc", "mean_roc_auc")
        ),
        "all_test_net_pnl": _number(
            _value(
                training_summary,
                "aggregate_metrics.all_test_net_pnl",
                "all_test_net_pnl",
            )
        ),
        "selected_net_pnl": _number(
            _value(
                training_summary,
                "aggregate_metrics.selected_net_pnl",
                "selected_net_pnl",
            )
        ),
        "selected_rows": _number(
            _value(training_summary, "aggregate_metrics.selected_rows", "selected_rows")
        ),
    }


def build_candidate_identity(
    training_summary: dict[str, Any],
    model_path: Path,
) -> dict[str, Any]:
    artifact_hash = sha256_file(model_path)
    model_id = MODEL_ID
    model_version = MODEL_VERSION
    identity_suffix = artifact_hash[:16] if artifact_hash else "missing_artifact"
    return {
        "candidate_id": f"{model_id}:{model_version}:{identity_suffix}",
        "model_id": model_id,
        "model_version": model_version,
        "model_family": str(
            _value(
                training_summary,
                "model_family_effective",
                "model_family_requested",
            )
            or "unknown"
        ),
        "model_artifact_path": str(model_path),
        "model_artifact_sha256": artifact_hash,
        "model_artifact_size_bytes": model_path.stat().st_size if model_path.is_file() else None,
    }


def _unsafe_input_flags(payload: dict[str, Any], source: str) -> list[str]:
    blockers: list[str] = []
    for flag in ("paper_only", "shadow_only"):
        if flag in payload and payload.get(flag) is not True:
            blockers.append(f"unsafe_safety_flag:{source}:{flag}={payload.get(flag)!r}")
    for flag in UNSAFE_TRUE_FLAGS:
        if payload.get(flag) is True:
            blockers.append(f"unsafe_safety_flag:{source}:{flag}=true")
    return blockers


def evaluate_candidate_gate(
    training_summary: dict[str, Any],
    executive_pack: dict[str, Any],
    model_exists: bool,
    config: ShadowCandidateRegistryConfig,
) -> dict[str, Any]:
    blockers: list[str] = []
    status04 = str(training_summary.get("status") or "missing")
    decision04 = str(training_summary.get("decision") or "missing")
    reason04 = str(training_summary.get("reason") or "missing")
    if status04 != "ok":
        blockers.append(f"branch04_status_not_ok:{status04}")
    if decision04 not in APPROVED_RESEARCH_DECISIONS:
        blockers.append(f"branch04_decision_not_approved:{decision04}")
    if reason04 == "selector_does_not_beat_all_test_baseline":
        blockers.append("branch04_selector_does_not_beat_all_test_baseline")
    if training_summary.get("suspicious_perfect_metrics") is True:
        blockers.append("branch04_suspicious_perfect_metrics")
    metrics = _metrics(training_summary)
    selected = metrics["selected_net_pnl"]
    all_test = metrics["all_test_net_pnl"]
    if selected is not None and all_test is not None and float(selected) <= float(all_test):
        blockers.append(f"selected_net_pnl_not_above_all_test_net_pnl:{selected}<={all_test}")
    status05 = str(executive_pack.get("status") or "missing")
    decision05 = str(executive_pack.get("decision") or "missing")
    if status05 in {"warning", "blocked"}:
        blockers.append(f"branch05_status_not_ok:{status05}")
    if decision05 == "MANTER_EM_RESEARCH":
        blockers.append(f"branch05_decision_requires_research:{decision05}")
    if not model_exists:
        blockers.append("model_artifact_missing")
    blockers.extend(_unsafe_input_flags(training_summary, "branch04"))
    blockers.extend(_unsafe_input_flags(executive_pack, "branch05"))
    blockers.append("research_registry_scope_forbids_promotion")
    return {
        "status": "blocked" if config.strict else "warning",
        "promotion_status": "blocked",
        "promotion_eligible": False,
        "promotion_blockers": list(dict.fromkeys(blockers)),
        "strict": config.strict,
        "auto_promote": False,
        "production_enabled": False,
        "updates_qlib_runtime": False,
    }


def build_shadow_candidate_record(
    identity: dict[str, Any],
    training_summary: dict[str, Any],
    executive_pack: dict[str, Any],
    gate: dict[str, Any],
    paths: ShadowCandidateRegistryPaths,
    *,
    registered_at_utc: str,
) -> dict[str, Any]:
    return {
        **identity,
        "registered_at_utc": registered_at_utc,
        "training_summary_path": str(paths.training_summary_path),
        "executive_pack_path": str(paths.executive_pack_path),
        "source_branch": SOURCE_BRANCH,
        "registry_branch": REGISTRY_BRANCH,
        "candidate_registry_status": "registered_research_only",
        "promotion_status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "promotion_blockers": list(gate["promotion_blockers"]),
        "metrics": _metrics(training_summary),
        "source_status": {
            "branch04_status": training_summary.get("status"),
            "branch04_reason": training_summary.get("reason"),
            "branch04_decision": training_summary.get("decision"),
            "branch05_status": executive_pack.get("status"),
            "branch05_reason": executive_pack.get("reason"),
            "branch05_decision": executive_pack.get("decision"),
        },
        "safety": dict(SAFETY_FLAGS),
    }


def _empty_registry() -> dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "registry_scope": REGISTRY_SCOPE,
        "updated_at_utc": None,
        "champion_model_id": None,
        "champion_model_version": None,
        "candidates": [],
        "registration_events": [],
        "rejected_promotions": [],
        **SAFETY_FLAGS,
    }


def build_registry_payload(
    existing_registry: dict[str, Any],
    candidate_record: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    registry = _empty_registry()
    registry.update(deepcopy(existing_registry))
    registry["registry_version"] = REGISTRY_VERSION
    registry["registry_scope"] = REGISTRY_SCOPE
    registry.setdefault("champion_model_id", None)
    registry.setdefault("champion_model_version", None)
    candidates = registry.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("registry_candidates_must_be_list")
    updated_candidates: list[dict[str, Any]] = []
    replaced = False
    for current in candidates:
        if current.get("candidate_id") == candidate_record["candidate_id"]:
            updated_candidates.append(deepcopy(candidate_record))
            replaced = True
        else:
            updated_candidates.append(current)
    if not replaced:
        updated_candidates.append(deepcopy(candidate_record))
    registry["candidates"] = updated_candidates

    events = registry.setdefault("registration_events", [])
    rejected = registry.setdefault("rejected_promotions", [])
    if not isinstance(events, list) or not isinstance(rejected, list):
        raise ValueError("registry_history_fields_must_be_lists")
    event = {
        "event_id": hashlib.sha256(
            (
                f"{candidate_record['candidate_id']}|"
                f"{candidate_record['registered_at_utc']}|registration"
            ).encode("utf-8")
        ).hexdigest()[:24],
        "event_type": "candidate_registered_research_only",
        "candidate_id": candidate_record["candidate_id"],
        "created_at_utc": candidate_record["registered_at_utc"],
        "promotion_status": "blocked",
    }
    if not any(current.get("event_id") == event["event_id"] for current in events):
        events.append(event)
    rejection = {
        "candidate_id": candidate_record["candidate_id"],
        "created_at_utc": candidate_record["registered_at_utc"],
        "promotion_status": "blocked",
        "promotion_blockers": list(gate["promotion_blockers"]),
    }
    blocker_signature = tuple(rejection["promotion_blockers"])
    if not any(
        current.get("candidate_id") == rejection["candidate_id"]
        and tuple(current.get("promotion_blockers") or []) == blocker_signature
        for current in rejected
    ):
        rejected.append(rejection)
    registry["updated_at_utc"] = candidate_record["registered_at_utc"]
    registry.update(SAFETY_FLAGS)
    return registry


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(
                _json_safe(payload),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_existing_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_registry()
    return load_json_report(path)


def _controlled_missing_report(
    paths: ShadowCandidateRegistryPaths,
    missing_sources: list[str],
    config: ShadowCandidateRegistryConfig,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "missing_required_sources",
        "decision": "MANTER_EM_RESEARCH",
        "candidate_registry_status": "not_registered",
        "promotion_status": "blocked",
        "promotion_blockers": [f"missing_source:{source}" for source in missing_sources],
        "missing_sources": missing_sources,
        "strict": config.strict,
        "write_requested": False,
        "write_performed": False,
        "training_summary_path": str(paths.training_summary_path),
        "executive_pack_path": str(paths.executive_pack_path),
        "model_artifact_path": str(paths.model_path),
        "registry_output_path": str(paths.registry_output_path),
        "report_output_path": str(paths.report_output_path),
        **SAFETY_FLAGS,
    }


def run_qlib_ocr_v11_shadow_candidate_registry(
    paths: ShadowCandidateRegistryPaths,
    config: ShadowCandidateRegistryConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> ShadowCandidateRegistryResult:
    missing_sources = [
        name
        for name, path in (
            ("training_summary", paths.training_summary_path),
            ("executive_pack", paths.executive_pack_path),
        )
        if not path.exists()
    ]
    if missing_sources:
        report = _controlled_missing_report(paths, missing_sources, config)
        report["write_requested"] = write
        return ShadowCandidateRegistryResult(report=report, registry=_empty_registry())
    training_summary = load_json_report(paths.training_summary_path)
    executive_pack = load_json_report(paths.executive_pack_path)
    identity = build_candidate_identity(training_summary, paths.model_path)
    gate = evaluate_candidate_gate(
        training_summary,
        executive_pack,
        paths.model_path.is_file(),
        config,
    )
    timestamp = analysis_date_utc or (
        "not_recorded_no_write" if not write else datetime.now(timezone.utc).isoformat()
    )
    candidate = build_shadow_candidate_record(
        identity,
        training_summary,
        executive_pack,
        gate,
        paths,
        registered_at_utc=timestamp,
    )
    registry = build_registry_payload(
        _load_existing_registry(paths.registry_output_path),
        candidate,
        gate,
    )
    report = {
        "status": "blocked" if config.strict else "warning",
        "reason": "research_candidate_registered_without_promotion",
        "decision": "MANTER_EM_RESEARCH",
        "candidate_registry_status": "registered_research_only",
        "promotion_status": "blocked",
        "promotion_eligible": False,
        "promotion_blockers": list(gate["promotion_blockers"]),
        "candidate_id": candidate["candidate_id"],
        "model_id": candidate["model_id"],
        "model_version": candidate["model_version"],
        "model_family": candidate["model_family"],
        "model_artifact_path": candidate["model_artifact_path"],
        "model_artifact_sha256": candidate["model_artifact_sha256"],
        "model_artifact_size_bytes": candidate["model_artifact_size_bytes"],
        "training_summary_path": str(paths.training_summary_path),
        "executive_pack_path": str(paths.executive_pack_path),
        "registry_output_path": str(paths.registry_output_path),
        "report_output_path": str(paths.report_output_path),
        "metrics": candidate["metrics"],
        "strict": config.strict,
        "write_requested": write,
        "write_performed": False,
        "analysis_date_utc": timestamp,
        **SAFETY_FLAGS,
    }
    if write:
        _atomic_write_json(paths.registry_output_path, registry)
        report["write_performed"] = True
        _atomic_write_json(paths.report_output_path, report)
    return ShadowCandidateRegistryResult(report=report, registry=registry)
