"""Research-only paper auto-train feedback loop orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from smartcrypto.learning.ai_shadow_trainer import build_ai_shadow_quality_veto_trainer_report
from smartcrypto.learning.qlib_trainer import build_qlib_institutional_ranking_trainer_report

SCHEMA_VERSION = "paper_autotrain_feedback_loop_v1"
DEFAULT_REPORT_JSON = Path("data/reports/paper_autotrain_feedback_loop_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_autotrain_feedback_loop_v1.md")

QlibTrainer = Callable[..., dict[str, Any]]
AiShadowTrainer = Callable[..., dict[str, Any]]

INPUT_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("paper_feedback_master", "data/reports/paper_feedback_master_consolidation_preview_v1.json", True),
    ("foundation_loop", "data/reports/paper_autolearning_foundation_summary.json", True),
    ("feature_contract", "data/reports/ai_unified_feature_contract_v1.json", True),
    ("dataset_manifest", "data/reports/ai_unified_dataset_manifest_v1.json", True),
    ("target_store", "data/reports/financial_label_target_store_v1.json", True),
    ("target_store_summary", "data/reports/financial_label_target_store_summary_v1.json", False),
    ("walkforward", "data/reports/walkforward_anti_leakage_split_engine_v1.json", True),
    ("walkforward_baseline", "data/reports/walkforward_baseline_summary_v1.json", False),
    ("qlib_backend_environment_lock", "data/reports/qlib_research_backend_environment_lock_v1.json", False),
    ("qlib_backend_gate", "data/reports/qlib_research_backend_gate_v1.json", False),
    ("qlib_trainer", "data/reports/qlib_institutional_ranking_trainer_v1.json", False),
    ("ai_shadow_trainer", "data/reports/ai_shadow_quality_veto_trainer_v1.json", False),
    ("ai_shadow_metrics", "data/reports/ai_shadow_quality_veto_metrics_v1.json", False),
    ("autolearning_closeout", "data/reports/autolearning_canonical_loop_closeout_evidence_v1.json", False),
)


def build_paper_autotrain_feedback_loop_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    allow_runtime_read: bool = False,
    run_qlib_train: bool = False,
    run_ai_shadow_train: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    qlib_trainer: QlibTrainer | None = None,
    ai_shadow_trainer: AiShadowTrainer | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the consolidated paper auto-train loop report.

    Default mode reads JSON evidence only. Training functions are invoked only by
    explicit `run_qlib_train` or `run_ai_shadow_train`.
    """

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    sources = load_input_sources(root)
    payloads = {source["source_id"]: source["payload"] for source in sources if isinstance(source.get("payload"), dict)}
    missing_required = [source["relative_path"] for source in sources if source["required"] and not source["exists"]]
    blockers = [f"missing_required_source:{path}" for path in missing_required]
    warnings: list[str] = []
    if not allow_runtime_read and (run_qlib_train or run_ai_shadow_train):
        warnings.append("runtime_read_allowed_by_explicit_training_flag")

    qlib_section = build_qlib_section(
        root=root,
        payloads=payloads,
        run_train=run_qlib_train,
        trainer=qlib_trainer or build_qlib_institutional_ranking_trainer_report,
    )
    ai_shadow_section = build_ai_shadow_section(
        root=root,
        payloads=payloads,
        run_train=run_ai_shadow_train,
        trainer=ai_shadow_trainer or build_ai_shadow_quality_veto_trainer_report,
    )
    warnings.extend(qlib_section.pop("_warnings", []))
    warnings.extend(ai_shadow_section.pop("_warnings", []))
    blockers.extend(qlib_section.pop("_blockers", []))
    blockers.extend(ai_shadow_section.pop("_blockers", []))

    lineage_hashes = collect_lineage_hashes(payloads)
    feature_section = feature_contract_section(payloads)
    target_section = target_store_section(payloads)
    walkforward_section = walkforward_section_payload(payloads)
    aggregate = aggregate_decision(qlib_section, ai_shadow_section, blockers)
    safety = safety_flags()
    output_paths = {
        "report_json": str(resolve(root, report_json_path, DEFAULT_REPORT_JSON)),
        "report_markdown": str(resolve(root, report_markdown_path, DEFAULT_REPORT_MD)),
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": aggregate["status"],
        "reason": aggregate["reason"],
        "decision": aggregate["decision"],
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": strip_payloads(sources),
        "lineage_hashes": lineage_hashes,
        "qlib_section": qlib_section,
        "ai_shadow_section": ai_shadow_section,
        "feature_contract_section": feature_section,
        "target_store_section": target_section,
        "walkforward_section": walkforward_section_payload(payloads),
        "aggregate_decision": aggregate,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": output_paths,
        "run_qlib_train_requested": bool(run_qlib_train),
        "run_ai_shadow_train_requested": bool(run_ai_shadow_train),
        "allow_runtime_read": bool(allow_runtime_read),
        **safety,
        "safety_flags": safety,
    }
    if write_report:
        write_reports(report, Path(output_paths["report_json"]), Path(output_paths["report_markdown"]))
        report["write_performed"] = True
        write_json(Path(output_paths["report_json"]), report)
    return report


def load_input_sources(project_root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source_id, relative_path, required in INPUT_SOURCES:
        path = project_root / relative_path
        exists = path.exists() and path.is_file()
        payload: dict[str, Any] | None = None
        load_error: str | None = None
        digest: str | None = None
        if exists:
            digest = file_sha256(path)
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
                payload = parsed if isinstance(parsed, dict) else None
                if payload is None:
                    load_error = "json_root_not_object"
            except json.JSONDecodeError as exc:
                load_error = f"invalid_json:{exc.msg}"
        sources.append(
            {
                "source_id": source_id,
                "relative_path": relative_path,
                "path": str(path.resolve()),
                "required": required,
                "exists": exists,
                "sha256": digest,
                "load_error": load_error,
                "payload": payload,
            }
        )
    return sources


def build_qlib_section(*, root: Path, payloads: dict[str, dict[str, Any]], run_train: bool, trainer: QlibTrainer) -> dict[str, Any]:
    source = payloads.get("qlib_trainer", {})
    if run_train:
        source = trainer(project_root=root, train=True, write_report=False, write_challenger_artifact=False)
    section = {
        "source": "trainer_run" if run_train else "existing_report",
        "status": source.get("status"),
        "reason": source.get("reason"),
        "qlib_backend_status": source.get("qlib_backend_status") or payloads.get("qlib_backend_gate", {}).get("qlib_backend_status"),
        "qlib_importable": source.get("qlib_importable") or payloads.get("qlib_backend_gate", {}).get("qlib_importable"),
        "qlib_version": source.get("qlib_version") or payloads.get("qlib_backend_gate", {}).get("qlib_version"),
        "trained_split_count": int(source.get("trained_split_count", 0) or 0),
        "candidate_decision": source.get("candidate_decision"),
        "aggregate_metrics": source.get("aggregate_metrics", {}),
        "metrics_by_split_count": len(source.get("metrics_by_split", []) or []),
        "selected_top_k_expected_value_total": nested(source, "aggregate_metrics", "selected_top_k_expected_value_total"),
        "beats_no_trade_split_count": nested(source, "baseline_comparison", "beats_no_trade_split_count"),
        "existing_report_qlib_training_performed": bool(source.get("qlib_training_performed", False)),
        "existing_report_qlib_challenger_training_performed": bool(source.get("qlib_challenger_training_performed", False)),
        "qlib_training_performed": bool(run_train and source.get("qlib_training_performed", False)),
        "qlib_challenger_training_performed": bool(run_train and source.get("qlib_challenger_training_performed", False)),
        "model_promotion_performed": bool(source.get("model_promotion_performed", False)),
        "registry_write_performed": bool(source.get("registry_write_performed", False)),
        "qlib_runtime_updated": bool(source.get("qlib_runtime_updated", False)),
        "sends_orders": bool(source.get("sends_orders", False)),
        "promotion_eligible": False,
        "_warnings": [],
        "_blockers": [],
    }
    if section["qlib_backend_status"] in {"unavailable", None}:
        section["_warnings"].append("qlib_backend_unavailable")
    if bool(section["sends_orders"]) or bool(section["model_promotion_performed"]) or bool(section["registry_write_performed"]) or bool(section["qlib_runtime_updated"]):
        section["_blockers"].append("qlib_safety_violation")
    return section


def build_ai_shadow_section(
    *,
    root: Path,
    payloads: dict[str, dict[str, Any]],
    run_train: bool,
    trainer: AiShadowTrainer,
) -> dict[str, Any]:
    source = payloads.get("ai_shadow_trainer", {})
    if run_train:
        source = trainer(project_root=root, train=True, write_report=False, write_challenger_artifact=False)
    aggregate = source.get("aggregate_metrics", {})
    section = {
        "source": "trainer_run" if run_train else "existing_report",
        "status": source.get("status"),
        "reason": source.get("reason"),
        "candidate_decision": source.get("candidate_decision"),
        "probability_output": source.get("probability_output") or source.get("probability_column"),
        "aggregate_metrics": aggregate,
        "metrics_by_split_count": len(source.get("metrics_by_split", []) or []),
        "net_ev_delta_if_applied_research_only_total": aggregate.get("net_ev_delta_if_applied_research_only_total"),
        "existing_report_ai_shadow_training_performed": bool(source.get("ai_shadow_challenger_training_performed", False)),
        "ai_shadow_training_performed": bool(run_train and source.get("ai_shadow_challenger_training_performed", False)),
        "registry_write_performed": bool(source.get("registry_write_performed", False)),
        "veto_registry_write_performed": bool(source.get("veto_registry_write_performed", False)),
        "ai_shadow_runtime_updated": bool(source.get("ai_shadow_runtime_updated", False)),
        "active_model_changed": bool(source.get("active_model_changed", False)),
        "sends_orders": bool(source.get("sends_orders", False)),
        "promotion_eligible": False,
        "_warnings": [],
        "_blockers": [],
    }
    if bool(section["sends_orders"]) or bool(section["registry_write_performed"]) or bool(section["veto_registry_write_performed"]) or bool(section["ai_shadow_runtime_updated"]) or bool(section["active_model_changed"]):
        section["_blockers"].append("ai_shadow_safety_violation")
    return section


def aggregate_decision(qlib_section: dict[str, Any], ai_shadow_section: dict[str, Any], blockers: list[str]) -> dict[str, str]:
    if blockers:
        return {"status": "blocked", "reason": blockers[0], "decision": "BLOCKED"}
    if qlib_section.get("qlib_backend_status") == "unavailable":
        return {"status": "warning", "reason": "qlib_backend_unavailable", "decision": "MANTER_EM_RESEARCH"}
    if qlib_section.get("candidate_decision") in {"MANTER_EM_RESEARCH", "BLOCKED_BACKEND_UNAVAILABLE"}:
        return {"status": "ok", "reason": "research_candidate_not_promoted", "decision": "MANTER_EM_RESEARCH"}
    if ai_shadow_section.get("candidate_decision") in {"MANTER_EM_RESEARCH", "BLOCKED_BACKEND_UNAVAILABLE"}:
        return {"status": "ok", "reason": "ai_shadow_candidate_not_promoted", "decision": "MANTER_EM_RESEARCH"}
    return {"status": "ok", "reason": "paper_autotrain_feedback_loop_ready_for_research_review", "decision": "MANTER_EM_RESEARCH"}


def collect_lineage_hashes(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "feature_contract_hash": payloads.get("feature_contract", {}).get("contract_hash"),
        "dataset_hash": payloads.get("dataset_manifest", {}).get("dataset_hash"),
        "target_store_hash": payloads.get("target_store", {}).get("target_store_hash"),
        "split_engine_hash": payloads.get("walkforward", {}).get("split_engine_hash"),
        "qlib_trainer_feature_contract_hash": payloads.get("qlib_trainer", {}).get("feature_contract_hash"),
        "qlib_trainer_dataset_hash": payloads.get("qlib_trainer", {}).get("dataset_hash"),
        "ai_shadow_feature_contract_hash": payloads.get("ai_shadow_trainer", {}).get("feature_contract_hash"),
        "ai_shadow_dataset_hash": payloads.get("ai_shadow_trainer", {}).get("dataset_hash"),
    }


def feature_contract_section(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    feature = payloads.get("feature_contract", {})
    manifest = payloads.get("dataset_manifest", {})
    return {
        "feature_contract_hash": feature.get("contract_hash"),
        "dataset_hash": manifest.get("dataset_hash"),
        "feature_column_count": len(feature.get("feature_columns", []) or []),
        "dataset_rows": manifest.get("row_count") or manifest.get("selected_training_dataset_rows"),
        "status": feature.get("validation_status") or feature.get("status"),
    }


def target_store_section(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    target = payloads.get("target_store", {})
    return {
        "target_store_hash": target.get("target_store_hash"),
        "row_count": target.get("row_count"),
        "status": target.get("validation_status") or target.get("status"),
        "target_columns": target.get("target_columns", []),
    }


def walkforward_section_payload(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    walkforward = payloads.get("walkforward", {})
    return {
        "split_engine_hash": walkforward.get("split_engine_hash"),
        "split_count": walkforward.get("split_count"),
        "status": walkforward.get("validation_status") or walkforward.get("status"),
    }


def strip_payloads(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in source.items() if key != "payload"} for source in sources]


def nested(payload: dict[str, Any], first: str, second: str) -> Any:
    value = payload.get(first, {})
    return value.get(second) if isinstance(value, dict) else None


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "promotion_eligible": False,
        "model_promotion_requested": False,
        "model_promotion_performed": False,
        "registry_write_requested": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def write_reports(report: dict[str, Any], report_json: Path, report_md: Path) -> None:
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Auto-train Feedback Loop V1",
            "",
            f"- Status: `{report['status']}`",
            f"- Reason: `{report['reason']}`",
            f"- Decision: `{report['decision']}`",
            f"- Qlib backend: `{report['qlib_section'].get('qlib_backend_status')}`",
            f"- Qlib trained: `{report['qlib_section'].get('qlib_training_performed')}`",
            f"- IA Shadow trained: `{report['ai_shadow_section'].get('ai_shadow_training_performed')}`",
            f"- Blockers: `{len(report['blockers'])}`",
            f"- Warnings: `{len(report['warnings'])}`",
            "",
            "This report is research-only. It does not promote models, write registry, update runtime, alter risk, access private exchange, or send orders.",
            "",
        ]
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path
