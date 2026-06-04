from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import pandas as pd
except Exception:  # pragma: no cover - pandas is expected in this project.
    pd = None  # type: ignore[assignment]

try:
    import sklearn
except Exception:  # pragma: no cover - sklearn is expected in this project.
    sklearn = None  # type: ignore[assignment]

try:
    import yaml
except Exception:  # pragma: no cover - yaml is expected in this project.
    yaml = None  # type: ignore[assignment]


DEFAULT_REPORT_PATH = Path("data/reports/sklearn_model_compatibility_guard_report.json")
SKLEARN_VERSION_KEYS = (
    "sklearn_version",
    "trained_sklearn_version",
    "training_sklearn_version",
    "model_sklearn_version",
    "sklearn_artifact_version",
    "runtime_sklearn_version",
    "_sklearn_version",
)
MODEL_IDENTITY_KEYS = ("model_id", "model_version")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "auto_promote",
)


def run_sklearn_model_compatibility_guard(
    *,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    trainer_report_path: str | Path | None = None,
    logs_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    strict: bool = False,
    runtime_sklearn_version: str | None = None,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = ensure_utc(now or datetime.now(timezone.utc))
    runtime_version = runtime_sklearn_version if runtime_sklearn_version is not None else get_runtime_sklearn_version()
    model = Path(model_path) if model_path is not None else None
    metadata = Path(metadata_path) if metadata_path is not None else None
    registry = Path(registry_path) if registry_path is not None else None
    trainer_report = Path(trainer_report_path) if trainer_report_path is not None else None
    logs = Path(logs_path) if logs_path is not None else None

    metadata_payload = load_structured_payload(metadata)
    registry_payload = load_structured_payload(registry)
    trainer_payload = load_structured_payload(trainer_report)
    log_warnings = read_sklearn_warnings(logs)
    model_hash = sha256_file(model) if model is not None and model.exists() else None
    metadata_hash = sha256_file(metadata) if metadata is not None and metadata.exists() else None

    model_declared_version = first_version(metadata_payload)
    registry_declared_version = registry_sklearn_version(registry_payload)
    trainer_declared_version = first_version(trainer_payload)
    declared_version = first_text(model_declared_version, registry_declared_version, trainer_declared_version)

    safety = safety_payload(safety_overrides)
    for payload in (metadata_payload, registry_payload, trainer_payload):
        safety.update({key: bool(payload[key]) for key in safety if key in payload})
    findings: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []

    if model is not None and not model.exists():
        blockers.append(f"missing_model:{model}")
    if runtime_version is None:
        blockers.append("missing_runtime_sklearn_version")
    if not declared_version:
        target = "missing_model_sklearn_version"
        blockers.append(target) if strict else warnings.append(target)
    else:
        findings.extend(compare_versions(declared_version, runtime_version, strict=strict))
    warnings.extend(f"sklearn_warning_detected:{item}" for item in log_warnings)
    if log_warnings and not strict:
        findings.append("sklearn_warning_detected_in_logs")
    if model is not None and model.exists() and model_hash is None:
        blockers.append("missing_model_hash")
    if metadata is not None and metadata.exists() and metadata_hash is None:
        blockers.append("missing_metadata_hash")
    if strict and metadata is None and not registry_declared_version and not trainer_declared_version:
        blockers.append("missing_metadata_source")
    if strict and registry is not None:
        blockers.extend(registry_metadata_blockers(registry_payload))
    if strict and trainer_report is not None and not trainer_declared_version:
        blockers.append("trainer_report_missing_sklearn_version")
    blockers.extend(safety_blockers(safety, metadata_payload, registry_payload, trainer_payload))

    for finding in findings:
        if finding.startswith("blocked:"):
            blockers.append(finding.removeprefix("blocked:"))
        elif finding.startswith("warning:"):
            warnings.append(finding.removeprefix("warning:"))
    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    compatibility_status = "blocked" if blockers else "warning" if warnings else "ok"
    status = resolve_status(
        compatibility_status,
        model=model,
        metadata_path=metadata,
        registry_path=registry,
        trainer_report_path=trainer_report,
        declared_version=declared_version,
    )
    report = {
        "status": status,
        "reason": ";".join(blockers or warnings or ["sklearn_model_compatibility_ok"]),
        "generated_at_utc": iso(generated_at),
        "runtime_sklearn_version": runtime_version,
        "runtime_python_version": platform.python_version(),
        "model_path": str(model) if model is not None else None,
        "metadata_path": str(metadata) if metadata is not None else None,
        "registry_path": str(registry) if registry is not None else None,
        "trainer_report_path": str(trainer_report) if trainer_report is not None else None,
        "model_declared_sklearn_version": model_declared_version,
        "registry_declared_sklearn_version": registry_declared_version,
        "trainer_declared_sklearn_version": trainer_declared_version,
        "compatibility_policy": {
            "strict": bool(strict),
            "major_minor_mismatch": "blocked",
            "future_model_version": "blocked",
            "patch_mismatch_non_strict": "warning",
            "missing_model_version_strict": "blocked",
            "unsafe_safety_flags": "blocked",
        },
        "compatibility_findings": sorted(set(findings)),
        "blocking_findings": blockers,
        "warnings": warnings,
        "log_warnings": log_warnings,
        "model_hash": model_hash,
        "metadata_hash": metadata_hash,
        "promotion_allowed": False,
        "auto_promote": False,
        **safety,
    }
    write_report(report, report_path)
    return report


def compare_versions(model_version: str, runtime_version: str | None, *, strict: bool) -> list[str]:
    if not runtime_version:
        return ["blocked:missing_runtime_sklearn_version"]
    model_parts = parse_version(model_version)
    runtime_parts = parse_version(runtime_version)
    if model_parts is None or runtime_parts is None:
        return ["blocked:invalid_sklearn_version"]
    if model_parts[:2] != runtime_parts[:2]:
        return ["blocked:sklearn_major_minor_mismatch"]
    if model_parts > runtime_parts:
        return ["blocked:model_sklearn_version_future"]
    if model_parts[2] != runtime_parts[2]:
        return ["warning:sklearn_patch_version_mismatch"]
    return ["sklearn_versions_match"]


def registry_metadata_blockers(payload: Mapping[str, Any]) -> list[str]:
    if not payload:
        return ["registry_metadata_missing"]
    blockers: list[str] = []
    records = []
    champion = payload.get("champion")
    if isinstance(champion, Mapping):
        records.append(champion)
    champion_id = payload.get("champion_model_id")
    champion_version = payload.get("champion_model_version")
    if champion_id or champion_version:
        records.append({"model_id": champion_id, "model_version": champion_version, "metadata": payload.get("champion_metadata", {})})
    challengers = payload.get("challengers")
    if isinstance(challengers, list):
        records.extend(item for item in challengers if isinstance(item, Mapping))
    models = payload.get("models")
    if isinstance(models, list):
        records.extend(item for item in models if isinstance(item, Mapping))
    for index, record in enumerate(records):
        flattened = flatten_metadata(record)
        if not all(flattened.get(key) for key in MODEL_IDENTITY_KEYS):
            blockers.append(f"registry_model_missing_identity:{index}")
        if not first_version(flattened):
            blockers.append(f"registry_model_missing_sklearn_version:{index}")
    return blockers


def safety_blockers(*payloads: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    merged: dict[str, Any] = {}
    for payload in payloads:
        merged.update({key: payload[key] for key in ("paper_only", "shadow_only", "promotion_allowed", "promotion_gate_approved", "promotion_gate_status", *SAFE_FALSE_FLAGS) if key in payload})
    if merged.get("paper_only") is False:
        blockers.append("unsafe_safety_flag:paper_only")
    if merged.get("shadow_only") is False:
        blockers.append("unsafe_safety_flag:shadow_only")
    for flag in SAFE_FALSE_FLAGS:
        if bool(merged.get(flag)):
            blockers.append(f"unsafe_safety_flag:{flag}")
    promotion_allowed = bool(merged.get("promotion_allowed"))
    gate_approved = bool(merged.get("promotion_gate_approved")) or str(merged.get("promotion_gate_status", "")).lower() in {"approved", "ok", "passed"}
    if promotion_allowed and not gate_approved:
        blockers.append("promotion_allowed_without_explicit_gate")
    if promotion_allowed and gate_approved:
        blockers.append("promotion_allowed_requires_manual_review")
    return blockers


def safety_payload(overrides: Mapping[str, Any] | None = None) -> dict[str, bool]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update({key: bool(value) for key, value in overrides.items() if key in payload})
    return payload


def resolve_status(
    compatibility_status: str,
    *,
    model: Path | None,
    metadata_path: Path | None,
    registry_path: Path | None,
    trainer_report_path: Path | None,
    declared_version: str | None,
) -> str:
    if compatibility_status == "blocked":
        return "blocked"
    if model is not None and not model.exists():
        return "missing_model"
    has_metadata_source = any(path is not None and path.exists() for path in (metadata_path, registry_path, trainer_report_path))
    if not declared_version and not has_metadata_source:
        return "missing_metadata"
    return compatibility_status


def get_runtime_sklearn_version() -> str | None:
    return getattr(sklearn, "__version__", None) if sklearn is not None else None


def first_version(payload: Mapping[str, Any]) -> str | None:
    flattened = flatten_metadata(payload)
    for key in SKLEARN_VERSION_KEYS:
        value = flattened.get(key)
        if value:
            return str(value)
    return None


def registry_sklearn_version(payload: Mapping[str, Any]) -> str | None:
    direct = first_version(payload)
    if direct:
        return direct
    for key in ("champion", "champion_metadata"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            found = first_version(value)
            if found:
                return found
    for key in ("challengers", "models"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    found = first_version(item)
                    if found:
                        return found
    return None


def flatten_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    stack = [payload]
    while stack:
        current = stack.pop()
        for key, value in current.items():
            if isinstance(value, Mapping):
                stack.append(value)
            else:
                flattened[str(key)] = value
    return flattened


def first_text(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def load_structured_payload(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    suffix = path.suffix.lower()
    try:
        if suffix in {".json", ".jsonl"}:
            return load_json_or_jsonl(path)
        if suffix in {".yaml", ".yml"} and yaml is not None:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return payload if isinstance(payload, dict) else {"rows": payload}
        if suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            return rows[0] if rows else {}
        if suffix == ".parquet" and pd is not None:
            frame = pd.read_parquet(path)
            return frame.iloc[0].dropna().to_dict() if len(frame) else {}
    except Exception as exc:
        return {"status": "blocked", "load_error": str(exc)}
    return {}


def load_json_or_jsonl(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return rows[0] if rows and isinstance(rows[0], dict) else {"rows": rows}
    payload = json.loads(text or "{}")
    return payload if isinstance(payload, dict) else {"rows": payload}


def read_sklearn_warnings(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    warnings = []
    for line in text.splitlines():
        lower = line.lower()
        if "inconsistentversionwarning" in lower or "sklearn_version_mismatch" in lower or "scikit-learn" in lower:
            warnings.append(line.strip())
    return warnings


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")
