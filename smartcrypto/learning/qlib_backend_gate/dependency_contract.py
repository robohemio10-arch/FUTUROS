"""Dependency contract builder for Qlib research backend availability."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso

from .backend_probe import REQUIRED_MODULES, probe_qlib_backend
from .environment_audit import build_environment_audit
from .runtime_isolation import audit_runtime_isolation, snapshot_runtime_state

SCHEMA_VERSION = "qlib_research_backend_runtime_dependency_gate_v1"
CONTRACT_SCHEMA_VERSION = "qlib_research_backend_dependency_contract_v1"
DEFAULT_REPORT_JSON = Path("data/reports/qlib_research_backend_gate_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/qlib_research_backend_gate_v1.md")


def build_qlib_research_backend_gate_report(
    *,
    project_root: str | Path,
    write: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    probe_func: Callable[[list[str] | None], dict[str, Any]] | None = None,
    required_modules: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    modules = required_modules or list(REQUIRED_MODULES)
    before = snapshot_runtime_state()
    probe = (probe_func or probe_qlib_backend)(modules)
    after = snapshot_runtime_state()
    isolation = audit_runtime_isolation(before, after)
    environment = build_environment_audit(root)
    environment["qlib_backend_available"] = probe["qlib_backend_status"] == "available"
    dependency_contract = build_dependency_contract(
        project_root=root,
        probe=probe,
        environment=environment,
        isolation=isolation,
        required_modules=modules,
    )
    qlib_status = dependency_contract["backend_capabilities"]["qlib_backend_status"]
    status = "blocked" if qlib_status == "blocked" else "ok"
    reason = {
        "available": "qlib_backend_available",
        "unavailable": "qlib_backend_unavailable",
        "partial": "qlib_backend_partial",
        "blocked": "qlib_backend_blocked",
    }[qlib_status]
    output_paths = {
        "report_json": str(resolve(root, report_json_path, DEFAULT_REPORT_JSON)),
        "report_markdown": str(resolve(root, report_markdown_path, DEFAULT_REPORT_MD)),
    }
    validation_errors = list(dependency_contract["validation_errors"])
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "project_root": str(root),
        "qlib_backend_status": qlib_status,
        "qlib_importable": dependency_contract["qlib_importable"],
        "qlib_version": dependency_contract["qlib_version"],
        "qlib_package_path": dependency_contract["qlib_package_path"],
        "required_modules": modules,
        "module_probe_results": dependency_contract["module_probe_results"],
        "dependency_contract_status": dependency_contract["validation_status"],
        "dependency_contract_hash": dependency_contract["contract_hash"],
        "runtime_isolation_status": dependency_contract["runtime_isolation_status"],
        "environment_audit_status": environment["environment_audit_status"],
        "backend_capabilities": dependency_contract["backend_capabilities"],
        "unsupported_reasons": dependency_contract["unsupported_reasons"],
        "recommended_installation_notes": dependency_contract["recommended_installation_notes"],
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": output_paths,
        **safety_flags(),
        "safety_flags": safety_flags(),
        "validation_errors": validation_errors,
        "dependency_contract": dependency_contract,
        "environment_audit": environment,
    }
    if write:
        report["write_performed"] = True
        write_reports(
            report=report,
            contract=dependency_contract,
            output_json=Path(output_paths["report_json"]),
            output_md=Path(output_paths["report_markdown"]),
        )
    return report


def build_dependency_contract(
    *,
    project_root: Path,
    probe: Mapping[str, Any],
    environment: Mapping[str, Any],
    isolation: Mapping[str, Any],
    required_modules: list[str],
) -> dict[str, Any]:
    unsupported = list(probe.get("unsupported_reasons", []))
    validation_errors = list(environment.get("validation_errors", []))
    validation_errors.extend(isolation.get("side_effects_detected", []))
    qlib_status = str(probe.get("qlib_backend_status", "unavailable"))
    if isolation.get("runtime_isolation_status") != "ok":
        qlib_status = "blocked"
    if environment.get("environment_audit_status") != "ok":
        qlib_status = "blocked"
    capabilities = backend_capabilities(qlib_status)
    contract: dict[str, Any] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_id": None,
        "contract_hash": None,
        "generated_at_utc": utc_now_iso(),
        "python_executable": environment["executable"],
        "python_version": environment["python_version"],
        "platform": environment["platform"],
        "project_root": str(project_root),
        "qlib_importable": bool(probe.get("qlib_importable", False)),
        "qlib_version": probe.get("qlib_version"),
        "qlib_package_path": probe.get("qlib_package_path"),
        "required_modules": required_modules,
        "module_probe_results": probe.get("module_probe_results", {}),
        "backend_capabilities": capabilities,
        "unsupported_reasons": unsupported,
        "recommended_installation_notes": recommended_notes(qlib_status),
        "runtime_isolation_status": isolation["runtime_isolation_status"],
        "side_effects_detected": isolation["side_effects_detected"],
        "validation_status": "blocked" if qlib_status == "blocked" else "ok",
        "validation_errors": sorted(set(validation_errors)),
        "safety_flags": safety_flags(),
    }
    digest = dependency_contract_hash(contract)
    contract["contract_id"] = f"qlib_backend_dependency_contract_{digest[:16]}"
    contract["contract_hash"] = digest
    return contract


def backend_capabilities(qlib_status: str) -> dict[str, Any]:
    available = qlib_status == "available"
    return {
        "qlib_backend_status": qlib_status,
        "can_import_qlib": available,
        "can_use_research_backend": available,
        "can_train_ranker": available,
        "can_save_research_artifact": available,
        "can_update_runtime": False,
        "can_promote_model": False,
        "can_write_registry": False,
        "can_send_orders": False,
    }


def recommended_notes(qlib_status: str) -> list[str]:
    if qlib_status == "available":
        return ["Qlib research backend is available for explicit research-only training."]
    if qlib_status == "partial":
        return ["Qlib is importable but required research modules or version metadata are incomplete.", "Review locked dev dependencies before enabling research training."]
    if qlib_status == "unavailable":
        return ["Qlib is not importable in this environment.", "Install/lock the research dependency in a separate dependency-management change; this gate does not install packages."]
    return ["Qlib backend probe was blocked by runtime isolation or environment validation."]


def dependency_contract_hash(contract: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in contract.items() if key not in {"generated_at_utc", "contract_id", "contract_hash"}}
    return stable_hash(payload)


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=json_safe).encode("utf-8")).hexdigest()


def write_reports(*, report: Mapping[str, Any], contract: Mapping[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(stable_pretty_json(report), encoding="utf-8")
    output_md.write_text(render_markdown(report, contract), encoding="utf-8")


def render_markdown(report: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Qlib Research Backend Runtime Dependency Gate V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Qlib backend status: `{report.get('qlib_backend_status')}`",
            f"- Qlib importable: `{report.get('qlib_importable')}`",
            f"- Qlib version: `{report.get('qlib_version')}`",
            f"- Dependency contract hash: `{contract.get('contract_hash')}`",
            f"- Runtime isolation: `{report.get('runtime_isolation_status')}`",
            "",
            "This gate audits dependency availability only. It does not install packages, train models, update runtime, write registry, promote models, access exchange, or send orders.",
            "",
        ]
    )


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
        "training_requested": False,
        "qlib_challenger_training_performed": False,
        "qlib_training_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_training_performed": False,
        "registry_write_requested": False,
        "registry_write_performed": False,
        "model_promotion_requested": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
