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


POLICY_PATH = "docs/LOCKFILE_HASH_INTEGRITY_HARDENING_V1.md"
FOLLOW_UP_BRANCH = "codex/lockfile-full-hash-resolution-v1"
HASH_PATTERN = re.compile(r"--hash=sha256:([^\s\\]+)", re.IGNORECASE)
VALID_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
PIN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==[^;\s]+")
URL_PATTERN = re.compile(r"(?:https?|git\+https?|git\+ssh)://", re.IGNORECASE)
REMOTE_INSTALLER_PATTERN = re.compile(r"(?:curl|wget).*(?:\||python|sh|bash)", re.IGNORECASE)
PIP_INSTALL_PATTERN = re.compile(r"(?:python\s+-m\s+pip|\bpip\d*)\s+install\b", re.IGNORECASE)
UPGRADE_PATTERN = re.compile(r"\bpip(?:\d*)?\s+install\b[^\n]*\s--upgrade\b|python\s+-m\s+pip\s+install\b[^\n]*\s--upgrade\b", re.IGNORECASE)
REQUIREMENT_OPTION_PATTERN = re.compile(r"(?:^|\s)-(?:r|c)\s+([^\s\\]+)")
OBVIOUS_FAKE_HASHES = {
    "0" * 64,
    "1" * 64,
    "a" * 64,
    "f" * 64,
    "deadbeef" * 8,
    "abcdef0123456789" * 4,
}
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
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
LOCKFILE_NAMES = {"poetry.lock", "pdm.lock", "uv.lock", "pipfile.lock"}


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


def is_dependency_file(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()
    return (
        name == "pyproject.toml"
        or name in LOCKFILE_NAMES
        or name.startswith("requirements") and name.endswith((".txt", ".lock"))
        or name.startswith("constraints") and name.endswith((".txt", ".lock"))
    )


def is_requirements_style(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()
    return name.startswith(("requirements", "constraints")) and name.endswith((".txt", ".lock"))


def is_lockfile(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()
    return name in LOCKFILE_NAMES or name.endswith(".lock")


def is_dockerfile(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def policy_state(project_root: Path) -> tuple[bool, bool]:
    path = project_root / POLICY_PATH
    if not path.is_file():
        return False, False
    try:
        text = path.read_text(encoding="utf-8-sig").lower()
    except OSError:
        return False, False
    markers = (
        "policy_status: temporary_exception",
        "temporary_exception_allowed: true",
        f"follow_up_branch: {FOLLOW_UP_BRANCH}",
        "paper_only: true",
        "shadow_only: true",
        "live_trading_enabled: false",
        "order_submission_enabled: false",
        "real_order_submission_enabled: false",
        "exchange_private_access: false",
        "sends_orders: false",
        "changes_risk: false",
    )
    documented = all(marker in text for marker in markers)
    return documented, documented


def logical_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    buffer: list[str] = []
    start_line = 0
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start_line = line_number
        buffer.append(stripped.rstrip("\\").strip())
        if stripped.endswith("\\"):
            continue
        combined = " ".join(part for part in buffer if part).split(" #", maxsplit=1)[0].strip()
        if combined:
            result.append((start_line, combined))
        buffer = []
    if buffer:
        result.append((start_line, " ".join(buffer).strip()))
    return result


def hash_state(value: str) -> str:
    if not VALID_HASH_PATTERN.fullmatch(value):
        return "invalid"
    if value.lower() in OBVIOUS_FAKE_HASHES:
        return "placeholder"
    return "valid"


def finding(severity: str, file: str, line: int, pattern: str, reason: str, recommendation: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "file": file,
        "line": line,
        "pattern": pattern,
        "reason": reason,
        "recommendation": recommendation,
    }


def scan_requirements_file(
    path: Path,
    relative_path: str,
    *,
    temporary_exception_allowed: bool,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    coverage = {
        "requirement_count": 0,
        "pinned_requirement_count": 0,
        "unhashed_requirement_count": 0,
        "hashed_requirement_count": 0,
        "invalid_hash_count": 0,
        "placeholder_hash_count": 0,
        "unpinned_requirement_count": 0,
    }
    findings: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        findings.append(
            finding("high", relative_path, 0, "dependency_file_unreadable", "Dependency file could not be audited.", "Fix its encoding or permissions.")
        )
        return coverage, findings

    for line_number, line in logical_lines(text):
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ", "--index-url", "--extra-index-url")):
            continue
        if line.startswith("-") and not line.startswith("-e "):
            continue
        coverage["requirement_count"] += 1
        pinned = bool(PIN_PATTERN.match(line))
        if pinned:
            coverage["pinned_requirement_count"] += 1
        else:
            coverage["unpinned_requirement_count"] += 1

        hashes = HASH_PATTERN.findall(line)
        valid_hashes = [value for value in hashes if hash_state(value) == "valid"]
        invalid_hashes = [value for value in hashes if hash_state(value) == "invalid"]
        placeholder_hashes = [value for value in hashes if hash_state(value) == "placeholder"]
        if valid_hashes:
            coverage["hashed_requirement_count"] += 1
        else:
            coverage["unhashed_requirement_count"] += 1
        coverage["invalid_hash_count"] += len(invalid_hashes)
        coverage["placeholder_hash_count"] += len(placeholder_hashes)

        if placeholder_hashes:
            findings.append(
                finding("critical", relative_path, line_number, "placeholder_sha256_hash", "A placeholder/fake hash is presented as dependency integrity evidence.", "Replace it only with hashes generated from artifacts in a controlled resolver environment.")
            )
        if invalid_hashes:
            findings.append(
                finding("high", relative_path, line_number, "invalid_sha256_hash", "A dependency hash is not 64 hexadecimal sha256 characters.", "Regenerate the hash from the controlled lock workflow.")
            )
        if URL_PATTERN.search(line) and not valid_hashes:
            findings.append(
                finding("critical", relative_path, line_number, "unhashed_remote_dependency", "A remote dependency is not protected by a valid sha256 hash.", "Pin the immutable source and include resolver-generated hashes before runtime use.")
            )
        if not pinned and not URL_PATTERN.search(line):
            severity = "high" if is_lockfile(relative_path) else "medium"
            findings.append(
                finding(severity, relative_path, line_number, "unpinned_dependency", "A dependency entry is not pinned with ==.", "Pin it in the lock/constraints source of truth.")
            )
        elif pinned and not hashes:
            severity = "medium" if temporary_exception_allowed else "high"
            findings.append(
                finding(severity, relative_path, line_number, "pinned_without_hash", "The version is pinned but artifact hashes are not hermetic.", f"Resolve complete hashes in {FOLLOW_UP_BRANCH} before enabling --require-hashes.")
            )
    return coverage, findings


def scan_dockerfile(
    path: Path,
    relative_path: str,
    *,
    temporary_exception_allowed: bool,
) -> list[dict[str, Any]]:
    try:
        lines = logical_lines(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return [finding("high", relative_path, 0, "dockerfile_unreadable", "Dockerfile could not be audited.", "Fix its encoding or permissions.")]
    findings: list[dict[str, Any]] = []
    for line_number, line in lines:
        if REMOTE_INSTALLER_PATTERN.search(line):
            findings.append(
                finding("critical", relative_path, line_number, "dynamic_remote_installer", "A remote installer may execute dynamically during image build.", "Vendor or pin the artifact and verify it with a real sha256 hash.")
            )
        if not PIP_INSTALL_PATTERN.search(line):
            continue
        if UPGRADE_PATTERN.search(line):
            severity = "medium" if temporary_exception_allowed else "high"
            findings.append(
                finding(severity, relative_path, line_number, "pip_install_upgrade_in_runtime_build", "The packaging toolchain is upgraded without version/hash constraints.", f"Pin and hash the build toolchain in {FOLLOW_UP_BRANCH}.")
            )
        requirement_files = REQUIREMENT_OPTION_PATTERN.findall(line)
        if not requirement_files or "--no-deps -e ." in line:
            continue
        protected = any(name.lower().endswith(".lock") for name in requirement_files) or "-c " in line or "--constraint " in line
        if not protected:
            severity = "medium" if temporary_exception_allowed else "high"
            findings.append(
                finding(severity, relative_path, line_number, "docker_install_without_lock_or_constraint", "Runtime build installs a requirements file without a lock or constraints source.", "Install from a locked/constraint-controlled source before runtime build.")
            )
    return findings


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    dependency_files = sorted(path for path in discovery.files if is_dependency_file(path) and (root / path).is_file())
    requirements_files = sorted(path for path in dependency_files if is_requirements_style(path))
    lockfiles = sorted(path for path in dependency_files if is_lockfile(path))
    dockerfiles = sorted(path for path in discovery.files if is_dockerfile(path) and (root / path).is_file())
    policy_documented, temporary_exception_allowed = policy_state(root)
    coverage = {
        "requirement_count": 0,
        "pinned_requirement_count": 0,
        "unhashed_requirement_count": 0,
        "hashed_requirement_count": 0,
        "invalid_hash_count": 0,
        "placeholder_hash_count": 0,
        "unpinned_requirement_count": 0,
    }
    findings: list[dict[str, Any]] = []
    for relative_path in requirements_files:
        file_coverage, file_findings = scan_requirements_file(
            root / relative_path,
            relative_path,
            temporary_exception_allowed=temporary_exception_allowed,
        )
        for key, value in file_coverage.items():
            coverage[key] += value
        findings.extend(file_findings)

    docker_findings: list[dict[str, Any]] = []
    for relative_path in dockerfiles:
        docker_findings.extend(
            scan_dockerfile(root / relative_path, relative_path, temporary_exception_allowed=temporary_exception_allowed)
        )
    findings.extend(docker_findings)
    findings.sort(key=lambda item: (-SEVERITY_RANK[item["severity"]], item["file"], item["line"], item["pattern"]))
    counts = {severity: sum(item["severity"] == severity for item in findings) for severity in SEVERITY_RANK}

    if counts["critical"] or counts["high"]:
        status, reason = "blocked", "high_or_critical_lockfile_integrity_finding"
    elif not policy_documented:
        status, reason = "blocked", "lockfile_hash_policy_missing_or_incomplete"
    elif findings:
        status, reason = "warning", "temporary_unhashed_lock_policy_documented"
    else:
        status, reason = "ok", "lockfile_hash_integrity_policy_satisfied"

    return {
        "status": status,
        "reason": reason,
        "scanned_files": len(dependency_files) + len(dockerfiles),
        "file_discovery_mode": discovery.mode,
        "file_discovery_source": discovery.source,
        "dependency_files": dependency_files,
        "lockfiles": lockfiles,
        "requirements_files": requirements_files,
        "hash_coverage": coverage,
        "docker_install_findings": docker_findings,
        "findings": findings,
        "finding_count": len(findings),
        "critical_count": counts["critical"],
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "low_count": counts["low"],
        "policy_documented": policy_documented,
        "temporary_exception_allowed": temporary_exception_allowed,
        "follow_up_branch": FOLLOW_UP_BRANCH,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit dependency lock and sha256 hash integrity without installing packages.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        coverage = report["hash_coverage"]
        print(f"{report['status']}: {report['reason']} ({coverage['hashed_requirement_count']} hashed requirements)")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
