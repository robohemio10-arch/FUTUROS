from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.runtime_evidence_pack import (
    DEFAULT_OUTPUT_DIR,
    EVIDENCE_PATHS,
    RUNTIME_OBSERVABILITY_PATHS,
    build_runtime_evidence_pack_and_readiness_snapshot_v2,
)

SCHEMA_VERSION = "runtime_evidence_sidecar_bundle_v1"
DEFAULT_OUTPUT_ROOT = Path("data/evidence_packs")

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
    "writes_official_trades_master",
    "changes_model",
    "changes_config",
    "changes_risk_config",
)

SAFE_TRUE_FLAGS = (
    "paper_only",
    "shadow_only",
)

SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_training_dataset": False,
    "writes_trades_master": False,
}

CORE_SOURCES = {
    "project_manifest": "PROJECT_MANIFEST_CLEAN.json",
    "runtime_evidence_pack_v2": "data/reports/runtime_evidence_pack_v2.json",
    "readiness_snapshot_v2": "data/reports/readiness_snapshot_v2.json",
}


@dataclass(frozen=True)
class SidecarBuildResult:
    summary: dict[str, Any]
    manifest: dict[str, Any]
    bundle_dir: Path
    manifest_path: Path
    sha256s_path: Path
    validation_summary_path: Path
    write_performed: bool


def build_runtime_evidence_sidecar_bundle(
    *,
    project_root: str | Path = ".",
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    no_write: bool = False,
    refresh_runtime_evidence: bool = True,
    include_containers: bool = False,
    container_timeout_seconds: float = 3.0,
    now: datetime | None = None,
) -> SidecarBuildResult:
    root = Path(project_root).resolve()
    current_time = now or datetime.now(timezone.utc)
    generated_at = iso(current_time)
    bundle_id = f"runtime_evidence_sidecar_{current_time.strftime('%Y%m%d_%H%M%SZ')}"
    output_root_path = resolve_under_root(root, output_root)
    bundle_dir = output_root_path / bundle_id
    sources_dir = bundle_dir / "sources"

    runtime_build_result = None
    if refresh_runtime_evidence:
        runtime_build_result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
            project_root=root,
            output_dir=DEFAULT_OUTPUT_DIR,
            no_write=no_write,
            include_containers=include_containers,
            container_timeout_seconds=container_timeout_seconds,
            now=current_time,
        )

    source_specs = sidecar_source_specs()
    collected_sources: dict[str, dict[str, Any]] = {}
    missing_sources: list[str] = []
    unsafe_sources: dict[str, list[str]] = {}

    if not no_write:
        sources_dir.mkdir(parents=True, exist_ok=True)

    for source_name, relative_path in sorted(source_specs.items()):
        source_path = root / relative_path
        source_summary = collect_source(root, source_name, source_path)

        if source_summary["status"] == "missing":
            missing_sources.append(source_name)
            collected_sources[source_name] = source_summary
            continue

        unsafe_flags = unsafe_flags_from_file(source_path)
        if unsafe_flags:
            source_summary["unsafe_flags"] = unsafe_flags
            unsafe_sources[source_name] = unsafe_flags

        if not no_write:
            copied_path = sources_dir / relative_path
            copied_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, copied_path)
            source_summary["bundle_relative_path"] = str(copied_path.relative_to(bundle_dir)).replace("\\", "/")

        collected_sources[source_name] = source_summary

    git_snapshot = collect_git_snapshot(root)
    runtime_summary = summarize_runtime_evidence(root, runtime_build_result)
    status, reason = classify_sidecar_status(
        missing_sources=missing_sources,
        unsafe_sources=unsafe_sources,
        runtime_summary=runtime_summary,
    )

    validation_summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "bundle_id": bundle_id,
        "status": status,
        "reason": reason,
        "source_count": sum(1 for source in collected_sources.values() if source["status"] == "ok"),
        "missing_sources": missing_sources,
        "unsafe_sources": unsafe_sources,
        "runtime_summary": runtime_summary,
        "git_dirty": git_snapshot["dirty"],
        **SAFETY_FLAGS,
    }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "bundle_id": bundle_id,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "git_snapshot": git_snapshot,
        "runtime_summary": runtime_summary,
        "collected_sources": collected_sources,
        "missing_sources": missing_sources,
        "unsafe_sources": unsafe_sources,
        "validation_summary": validation_summary,
        "deterministic_json": True,
        **SAFETY_FLAGS,
    }

    manifest_path = bundle_dir / "MANIFEST.json"
    validation_summary_path = bundle_dir / "validation_summary.json"
    sha256s_path = bundle_dir / "SHA256SUMS.txt"

    if not no_write:
        write_json(manifest_path, manifest)
        write_json(validation_summary_path, validation_summary)
        write_sha256s(sha256s_path, bundle_dir)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "bundle_dir": str(bundle_dir),
        "manifest_path": str(manifest_path),
        "sha256s_path": str(sha256s_path),
        "validation_summary_path": str(validation_summary_path),
        "write_performed": not no_write,
        "source_count": validation_summary["source_count"],
        "missing_sources_count": len(missing_sources),
        "unsafe_sources_count": len(unsafe_sources),
        "runtime_observability_status": runtime_summary.get("runtime_observability_status"),
        "runtime_observability_reason": runtime_summary.get("runtime_observability_reason"),
        "evidence_pack_status": runtime_summary.get("evidence_pack_status"),
        "readiness_snapshot_status": runtime_summary.get("readiness_snapshot_status"),
        "readiness_snapshot_reason": runtime_summary.get("readiness_snapshot_reason"),
        **SAFETY_FLAGS,
    }

    return SidecarBuildResult(
        summary=summary,
        manifest=manifest,
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        sha256s_path=sha256s_path,
        validation_summary_path=validation_summary_path,
        write_performed=not no_write,
    )


def sidecar_source_specs() -> dict[str, Path]:
    specs: dict[str, Path] = {name: Path(path) for name, path in CORE_SOURCES.items()}

    for name, relative_path in EVIDENCE_PATHS.items():
        specs[f"evidence_{name}"] = Path(relative_path)

    for name, relative_path in RUNTIME_OBSERVABILITY_PATHS.items():
        specs[f"runtime_{name}"] = Path(relative_path)

    return specs


def collect_source(root: Path, source_name: str, source_path: Path) -> dict[str, Any]:
    relative_path = source_path.relative_to(root) if source_path.is_absolute() else source_path
    if not source_path.exists():
        return {
            "name": source_name,
            "status": "missing",
            "relative_path": str(relative_path).replace("\\", "/"),
        }

    stat = source_path.stat()
    return {
        "name": source_name,
        "status": "ok",
        "relative_path": str(relative_path).replace("\\", "/"),
        "size_bytes": int(stat.st_size),
        "modified_at_utc": iso(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
        "sha256": sha256_file(source_path),
    }


def unsafe_flags_from_file(path: Path) -> list[str]:
    if path.suffix.lower() != ".json":
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["invalid_json"]
    if not isinstance(payload, dict):
        return ["invalid_json_payload"]

    flags: list[str] = []
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            flags.append(flag)
    for flag in SAFE_TRUE_FLAGS:
        if flag in payload and payload.get(flag) is not True:
            flags.append(flag)
    return sorted(flags)


def summarize_runtime_evidence(root: Path, runtime_build_result: Any | None) -> dict[str, Any]:
    if runtime_build_result is not None:
        evidence_pack = runtime_build_result.evidence_pack
        readiness_snapshot = runtime_build_result.readiness_snapshot
        runtime_observability = evidence_pack.get("runtime_observability", {})
        container_snapshot = evidence_pack.get("container_snapshot", {})
        return {
            "evidence_pack_status": evidence_pack.get("status"),
            "evidence_pack_reason": evidence_pack.get("reason"),
            "readiness_snapshot_status": readiness_snapshot.get("status"),
            "readiness_snapshot_reason": readiness_snapshot.get("reason"),
            "runtime_observability_status": runtime_observability.get("status"),
            "runtime_observability_reason": runtime_observability.get("reason"),
            "container_snapshot_status": container_snapshot.get("status"),
            "container_snapshot_reason": container_snapshot.get("reason"),
            "write_performed": runtime_build_result.write_performed,
        }

    evidence_pack = read_json_or_empty(root / CORE_SOURCES["runtime_evidence_pack_v2"])
    readiness_snapshot = read_json_or_empty(root / CORE_SOURCES["readiness_snapshot_v2"])
    runtime_observability = dict_or_empty(evidence_pack.get("runtime_observability"))
    container_snapshot = dict_or_empty(evidence_pack.get("container_snapshot"))

    return {
        "evidence_pack_status": evidence_pack.get("status"),
        "evidence_pack_reason": evidence_pack.get("reason"),
        "readiness_snapshot_status": readiness_snapshot.get("status"),
        "readiness_snapshot_reason": readiness_snapshot.get("reason"),
        "runtime_observability_status": runtime_observability.get("status"),
        "runtime_observability_reason": runtime_observability.get("reason"),
        "container_snapshot_status": container_snapshot.get("status"),
        "container_snapshot_reason": container_snapshot.get("reason"),
        "write_performed": False,
    }


def classify_sidecar_status(
    *,
    missing_sources: list[str],
    unsafe_sources: dict[str, list[str]],
    runtime_summary: dict[str, Any],
) -> tuple[str, str]:
    missing_core = sorted(name for name in CORE_SOURCES if name in missing_sources)
    if unsafe_sources:
        return "blocked", "unsafe_source_flags_detected"
    if missing_core:
        return "blocked", "missing_core_sources:" + ",".join(missing_core)

    runtime_status = str(runtime_summary.get("runtime_observability_status") or "").lower()
    if runtime_status in {"blocked", "degraded"}:
        return "blocked", f"runtime_observability_{runtime_status}"

    return "ok", "sidecar_bundle_created"


def collect_git_snapshot(root: Path) -> dict[str, Any]:
    status_short = git_output(root, "status", "--short")
    return {
        "branch": git_output(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": git_output(root, "rev-parse", "HEAD"),
        "status_short": status_short,
        "dirty": bool(status_short.strip()),
        "remote_dev": git_output(root, "rev-parse", "origin/dev"),
    }


def git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def write_sha256s(path: Path, bundle_dir: Path) -> None:
    rows: list[str] = []
    for file_path in sorted(p for p in bundle_dir.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        relative = file_path.relative_to(bundle_dir).as_posix()
        rows.append(f"{sha256_file(file_path)}  {relative}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resolve_under_root(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
