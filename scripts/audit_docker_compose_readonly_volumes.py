from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


POLICY_PATH = "docs/DOCKER_COMPOSE_READONLY_VOLUME_TIGHTENING_V1.md"
SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "sends_orders": False,
    "changes_risk": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}
READ_ONLY_TARGETS = (
    "/app/config",
    "/app/docs",
    "/app/scripts",
    "/app/smartcrypto",
    "/freqtrade/user_data/config.paper.json",
    "/freqtrade/user_data/strategies",
)
WRITABLE_TARGETS = (
    "/app/data",
    "/app/logs",
    "/freqtrade/user_data/data",
    "/freqtrade/user_data/db",
    "/freqtrade/user_data/logs",
)
SENSITIVE_MARKERS = (".env", "credential", "private_key", "secret", "token", ".pem", ".key", ".crt")
EXCEPTION_PREFIX = "writable_exception:"


def load_versioned_file_discovery() -> ModuleType:
    module_name = "smartcrypto.ops.versioned_file_discovery"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"smartcrypto", "smartcrypto.ops", module_name}:
            raise

    discovery_path = Path(__file__).resolve().parents[1] / "smartcrypto" / "ops" / "versioned_file_discovery.py"
    spec = importlib.util.spec_from_file_location(module_name, discovery_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot_load_standalone_module:{discovery_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


_DISCOVERY_MODULE = load_versioned_file_discovery()
discover_versioned_files = _DISCOVERY_MODULE.discover_versioned_files


def is_compose_file(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()
    return (name.startswith("docker-compose") or name.startswith("compose")) and name.endswith((".yml", ".yaml"))


def policy_state(project_root: Path) -> tuple[bool, set[tuple[str, str, str]]]:
    path = project_root / POLICY_PATH
    if not path.is_file():
        return False, set()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False, set()
    lowered = [line.strip().lower() for line in lines]
    required = (
        "policy_status: active",
        "paper_only: true",
        "shadow_only: true",
        "live_trading_enabled: false",
        "order_submission_enabled: false",
        "real_order_submission_enabled: false",
        "exchange_private_access: false",
        "sends_orders: false",
        "changes_risk: false",
    )
    documented = all(marker in lowered for marker in required)
    exceptions: set[tuple[str, str, str]] = set()
    for line in lowered:
        if not line.startswith(EXCEPTION_PREFIX):
            continue
        parts = [part.strip() for part in line.removeprefix(EXCEPTION_PREFIX).split("|")]
        if len(parts) == 3 and all(parts):
            exceptions.add((parts[0], parts[1], parts[2]))
    return documented, exceptions


def unquote(value: str) -> str:
    return value.strip().strip("\"'")


def mount_type(source: str) -> str:
    if not source:
        return "volume"
    if source.startswith((".", "/", "~", "${")) or re.match(r"^[A-Za-z]:[\\/]", source):
        return "bind"
    return "volume"


def parse_short_mount(value: str, line_number: int) -> dict[str, Any] | None:
    raw = unquote(value)
    parts = raw.rsplit(":", maxsplit=2)
    if len(parts) < 2:
        return None
    source, target = parts[0], parts[1]
    mode = parts[2] if len(parts) == 3 else ""
    return {
        "source": source,
        "target": target,
        "type": mount_type(source),
        "read_only": "ro" in {item.strip().lower() for item in mode.split(",")},
        "line": line_number,
    }


def parse_compose_mounts(path: Path, relative_path: str) -> tuple[list[str], list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    services: list[str] = []
    mounts: list[dict[str, Any]] = []
    in_services = False
    service: str | None = None
    in_volumes = False
    long_mount: dict[str, Any] | None = None

    def flush_long() -> None:
        nonlocal long_mount
        if long_mount and long_mount.get("target"):
            source = str(long_mount.get("source", ""))
            long_mount["type"] = str(long_mount.get("type") or mount_type(source))
            long_mount["read_only"] = bool(long_mount.get("read_only", False))
            mounts.append({"compose_file": relative_path, "service": service, **long_mount})
        long_mount = None

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            flush_long()
            in_services = stripped == "services:"
            service = None
            in_volumes = False
            continue
        if in_services and indent == 2 and stripped.endswith(":"):
            flush_long()
            service = stripped[:-1]
            services.append(service)
            in_volumes = False
            continue
        if service and indent == 4:
            flush_long()
            in_volumes = stripped == "volumes:"
            continue
        if not (service and in_volumes):
            continue
        if indent == 6 and stripped.startswith("-"):
            flush_long()
            item = stripped[1:].strip()
            if not item:
                long_mount = {"line": line_number}
            elif re.match(r"^(type|source|target|read_only):", item):
                key, value = item.split(":", maxsplit=1)
                long_mount = {"line": line_number, key: unquote(value)}
            else:
                mount = parse_short_mount(item, line_number)
                if mount:
                    mounts.append({"compose_file": relative_path, "service": service, **mount})
            continue
        if long_mount is not None and indent >= 8 and ":" in stripped:
            key, value = stripped.split(":", maxsplit=1)
            parsed: Any = unquote(value)
            if key == "read_only":
                parsed = parsed.lower() == "true"
            long_mount[key] = parsed
    flush_long()
    return services, mounts


def target_matches(target: str, candidates: tuple[str, ...]) -> bool:
    normalized = target.rstrip("/")
    return any(normalized == candidate or normalized.startswith(candidate + "/") for candidate in candidates)


def classify_mount(
    mount: dict[str, Any],
    *,
    policy_documented: bool,
    writable_exceptions: set[tuple[str, str, str]],
) -> dict[str, Any]:
    source = str(mount["source"])
    target = str(mount["target"])
    read_only = bool(mount["read_only"])
    exception_key = (str(mount["compose_file"]).lower(), str(mount["service"]).lower(), target.lower())
    has_exception = exception_key in writable_exceptions
    combined = f"{source}|{target}".lower()

    if read_only:
        classification, severity = "read_only_ok", "ok"
        reason = "Mount is explicitly read-only."
        recommendation = "Keep the read-only mount unless a documented write requirement is introduced."
    elif any(marker in combined for marker in SENSITIVE_MARKERS):
        classification, severity = "writable_unjustified", "critical"
        reason = "Sensitive configuration or credential material is mounted writable."
        recommendation = "Make the mount read-only and keep secrets outside writable container paths."
    elif target_matches(target, READ_ONLY_TARGETS):
        if has_exception and policy_documented:
            classification, severity = "unknown_requires_review", "medium"
            reason = "Code/config mount remains writable under an explicit temporary exception."
            recommendation = "Remove the exception after separating required writable state."
        else:
            classification, severity = "writable_unjustified", "high"
            reason = "Code, scripts, configuration, or strategy content is mounted writable without justification."
            recommendation = "Add read-only mode unless a precise write requirement is documented."
    elif target_matches(target, WRITABLE_TARGETS):
        classification, severity = "writable_required", "ok"
        reason = "Target stores operational data, logs, reports, runtime state, or SQLite files."
        recommendation = "Keep writable and constrain the mount to the narrowest operational path."
    elif has_exception and policy_documented:
        classification, severity = "unknown_requires_review", "medium"
        reason = "Broad or mixed-purpose writable mount is covered by an explicit temporary exception."
        recommendation = "Split immutable code/config from writable state in a dedicated follow-up."
    elif mount["type"] == "volume" and any(marker in target.lower() for marker in ("db", "data", "runtime", "state")):
        classification, severity = "writable_required", "ok"
        reason = "Named volume stores operational state."
        recommendation = "Keep writable while preserving least-privilege access."
    else:
        classification, severity = "unknown_requires_review", "medium"
        reason = "Writable mount purpose is not covered by the current path policy."
        recommendation = "Document the write requirement or make the mount read-only."

    return {
        **mount,
        "classification": classification,
        "severity": severity,
        "reason": reason,
        "recommendation": recommendation,
        "documented_exception": has_exception,
    }


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    compose_files = sorted(path for path in discovery.files if is_compose_file(path) and (root / path).is_file())
    policy_documented, writable_exceptions = policy_state(root)
    services: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    for relative_path in compose_files:
        try:
            file_services, mounts = parse_compose_mounts(root / relative_path, relative_path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            parse_errors.append({"compose_file": relative_path, "reason": type(exc).__name__})
            continue
        services.extend({"compose_file": relative_path, "service": service} for service in file_services)
        findings.extend(
            classify_mount(
                mount,
                policy_documented=policy_documented,
                writable_exceptions=writable_exceptions,
            )
            for mount in mounts
        )
    services.sort(key=lambda item: (item["compose_file"], item["service"]))
    findings.sort(key=lambda item: (item["compose_file"], item["service"], item["line"], item["target"]))
    counts = {
        classification: sum(item["classification"] == classification for item in findings)
        for classification in ("read_only_ok", "writable_required", "writable_unjustified", "unknown_requires_review")
    }
    blocking = any(item["severity"] in {"critical", "high"} for item in findings)
    if parse_errors:
        status, reason = "blocked", "compose_parse_failed"
    elif blocking:
        status, reason = "blocked", "writable_sensitive_or_immutable_mount_detected"
    elif not policy_documented:
        status, reason = "blocked", "readonly_volume_policy_missing_or_incomplete"
    elif counts["unknown_requires_review"]:
        status, reason = "warning", "documented_writable_volume_exceptions_require_review"
    else:
        status, reason = "ok", "compose_volume_policy_satisfied"
    return {
        "status": status,
        "reason": reason,
        "compose_files": compose_files,
        "services": services,
        "volume_findings": findings,
        "read_only_count": counts["read_only_ok"],
        "writable_required_count": counts["writable_required"],
        "writable_unjustified_count": counts["writable_unjustified"],
        "unknown_requires_review_count": counts["unknown_requires_review"],
        "policy_documented": policy_documented,
        "parse_errors": parse_errors,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Docker Compose mount read-only policy without running Docker.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']} ({len(report['volume_findings'])} mounts)")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
