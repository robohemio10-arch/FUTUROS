"""Read-only consistency and vulnerability gate for a sealed research environment.

The input reports are evidence to validate; this script does not install packages,
run an auditor, contact a package index, or grant operational authority.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidName, canonicalize_name
from packaging.version import InvalidVersion, Version


class AuditResult(TypedDict):
    PIT_DEPENDENCY_AUDIT: Literal["CLEAN", "BLOCKED"]
    PIT_AUDIT_COVERAGE: str
    runtime_integration_allowed: Literal[False]
    paper_modified: Literal[False]
    operational_authority: Literal[False]
    error_codes: list[str]


@dataclass(frozen=True)
class LockedPackage:
    version: str
    hashes: frozenset[str]


class AuditBlocked(ValueError):
    """Contains only a static error code, never an input payload or local path."""


_HASH = re.compile(r"(?:^|\s+)--hash(?:=|\s+)sha256:([0-9a-fA-F]{64})(?=\s|$)")


def _read_text(path: Path, kind: str) -> str:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, ValueError) as from_error:
        raise AuditBlocked(f"{kind}_READ_ERROR") from from_error
    if not content.strip():
        raise AuditBlocked(f"{kind}_EMPTY")
    return content


def _without_comment(line: str) -> str:
    quote: str | None = None
    for index, character in enumerate(line):
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        elif character == "#" and quote is None:
            if index == 0 or line[index - 1].isspace():
                return line[:index].rstrip()
    return line.rstrip()


def _lock_lines(content: str) -> list[str]:
    requirements: list[str] = []
    pending: list[str] = []
    for physical_line in content.splitlines():
        line = _without_comment(physical_line).strip()
        if not line:
            continue
        continued = line.endswith("\\")
        pending.append(line[:-1].rstrip() if continued else line)
        if not continued:
            requirements.append(" ".join(pending))
            pending.clear()
    if pending:
        raise AuditBlocked("LOCK_UNTERMINATED_CONTINUATION")
    return requirements


def _identity(name: object, version: object, kind: str) -> tuple[str, str]:
    if not isinstance(name, str) or not isinstance(version, str):
        raise AuditBlocked(f"{kind}_IDENTITY_INVALID")
    if not name or not version or name != name.strip() or version != version.strip():
        raise AuditBlocked(f"{kind}_IDENTITY_INVALID")
    try:
        normalized_name = canonicalize_name(name, validate=True)
        Version(version)
    except (InvalidName, InvalidVersion) as error:
        raise AuditBlocked(f"{kind}_IDENTITY_INVALID") from error
    return str(normalized_name), version


def _read_lock(path: Path) -> dict[str, LockedPackage]:
    active: dict[str, LockedPackage] = {}
    environment = default_environment()
    for line in _lock_lines(_read_text(path, "LOCK")):
        if line.startswith("-"):
            raise AuditBlocked("LOCK_UNSAFE_OPTION")
        matches = list(_HASH.finditer(line))
        if not matches:
            code = "LOCK_HASH_INVALID" if "--hash" in line else "LOCK_HASH_REQUIRED"
            raise AuditBlocked(code)
        requirement_text = line[: matches[0].start()].strip()
        hash_text = line[matches[0].start() :]
        if _HASH.sub("", hash_text).strip():
            raise AuditBlocked("LOCK_HASH_INVALID")
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement as error:
            raise AuditBlocked("LOCK_REQUIREMENT_INVALID") from error
        if requirement.url is not None:
            raise AuditBlocked("LOCK_DIRECT_URL_FORBIDDEN")
        specifiers = list(requirement.specifier)
        if (
            len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise AuditBlocked("LOCK_EXACT_PIN_REQUIRED")
        name, version = _identity(requirement.name, specifiers[0].version, "LOCK")
        if requirement.marker is not None:
            try:
                selected = requirement.marker.evaluate(environment=environment)
            except (ValueError, KeyError) as error:
                raise AuditBlocked("LOCK_MARKER_INVALID") from error
            if not selected:
                continue
        if name in active:
            raise AuditBlocked("LOCK_DUPLICATE_ACTIVE_PACKAGE")
        active[name] = LockedPackage(
            version=version,
            hashes=frozenset(match.group(1).lower() for match in matches),
        )
    if not active:
        raise AuditBlocked("LOCK_NO_ACTIVE_PACKAGES")
    return active


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuditBlocked("REPORT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise AuditBlocked("REPORT_JSON_CONSTANT_INVALID")


def _object(value: object, kind: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AuditBlocked(f"{kind}_SCHEMA_INVALID")
    return cast(dict[str, object], value)


def _nonempty_list(value: object, kind: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise AuditBlocked(f"{kind}_SCHEMA_INVALID")
    return cast(list[object], value)


def _read_report(path: Path, kind: str) -> dict[str, object]:
    try:
        value: object = json.loads(
            _read_text(path, kind),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except AuditBlocked:
        raise
    except (ValueError, RecursionError) as error:
        raise AuditBlocked(f"{kind}_JSON_INVALID") from error
    return _object(value, kind)


def _insert_identity(
    identities: dict[str, str], metadata: dict[str, object], kind: str
) -> str:
    name, version = _identity(metadata.get("name"), metadata.get("version"), kind)
    if name in identities:
        raise AuditBlocked(f"{kind}_DUPLICATE_PACKAGE")
    identities[name] = version
    return name


def _require_same_packages(
    observed: dict[str, str], locked: dict[str, LockedPackage], kind: str
) -> None:
    if observed != {name: package.version for name, package in locked.items()}:
        raise AuditBlocked(f"{kind}_PACKAGE_SET_MISMATCH")


def _check_resolver(path: Path, locked: dict[str, LockedPackage]) -> None:
    report = _read_report(path, "RESOLVER")
    environment = _object(report.get("environment"), "RESOLVER")
    if environment != default_environment():
        raise AuditBlocked("RESOLVER_ENVIRONMENT_MISMATCH")
    observed: dict[str, str] = {}
    for item in _nonempty_list(report.get("install"), "RESOLVER"):
        entry = _object(item, "RESOLVER")
        name = _insert_identity(observed, _object(entry.get("metadata"), "RESOLVER"), "RESOLVER")
        download = _object(entry.get("download_info"), "RESOLVER")
        archive = _object(download.get("archive_info"), "RESOLVER")
        hashes = _object(archive.get("hashes"), "RESOLVER")
        sha256 = hashes.get("sha256")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
            raise AuditBlocked("RESOLVER_SHA256_REQUIRED")
        if name not in locked or sha256.lower() not in locked[name].hashes:
            raise AuditBlocked("RESOLVER_HASH_MISMATCH")
    _require_same_packages(observed, locked, "RESOLVER")


def _check_installed(path: Path, locked: dict[str, LockedPackage]) -> None:
    report = _read_report(path, "INSTALLED")
    observed: dict[str, str] = {}
    for item in _nonempty_list(report.get("installed"), "INSTALLED"):
        entry = _object(item, "INSTALLED")
        _insert_identity(observed, _object(entry.get("metadata"), "INSTALLED"), "INSTALLED")
    _require_same_packages(observed, locked, "INSTALLED")


def _check_audit(path: Path, locked: dict[str, LockedPackage]) -> None:
    report = _read_report(path, "AUDIT")
    if "skip_reason" in report or report.get("findings") or report.get("vulns"):
        raise AuditBlocked("AUDIT_UNCLEAN_REPORT")
    observed: dict[str, str] = {}
    for item in _nonempty_list(report.get("dependencies"), "AUDIT"):
        entry = _object(item, "AUDIT")
        if "skip_reason" in entry:
            raise AuditBlocked("AUDIT_SKIPPED_PACKAGE")
        vulnerabilities = entry.get("vulns")
        if not isinstance(vulnerabilities, list):
            raise AuditBlocked("AUDIT_SCHEMA_INVALID")
        if vulnerabilities or entry.get("findings"):
            raise AuditBlocked("AUDIT_VULNERABILITY_FOUND")
        _insert_identity(observed, entry, "AUDIT")
    _require_same_packages(observed, locked, "AUDIT")


def audit_dependencies(
    lock: Path, resolver_report: Path, installed_report: Path, audit_report: Path
) -> AuditResult:
    """Validate existing evidence files without changing the environment or files."""
    try:
        locked = _read_lock(lock)
        _check_resolver(resolver_report, locked)
        _check_installed(installed_report, locked)
        _check_audit(audit_report, locked)
    except AuditBlocked as error:
        return {
            "PIT_DEPENDENCY_AUDIT": "BLOCKED",
            "PIT_AUDIT_COVERAGE": "BLOCKED",
            "runtime_integration_allowed": False,
            "paper_modified": False,
            "operational_authority": False,
            "error_codes": [str(error)],
        }
    return {
        "PIT_DEPENDENCY_AUDIT": "CLEAN",
        "PIT_AUDIT_COVERAGE": f"{len(locked)}/{len(locked)}",
        "runtime_integration_allowed": False,
        "paper_modified": False,
        "operational_authority": False,
        "error_codes": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--resolver-report", type=Path, required=True)
    parser.add_argument("--installed-report", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    arguments = parser.parse_args(argv)
    result = audit_dependencies(
        arguments.lock,
        arguments.resolver_report,
        arguments.installed_report,
        arguments.audit_report,
    )
    if arguments.json_output:
        print(json.dumps(result, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}={json.dumps(value) if not isinstance(value, str) else value}")
    return 0 if result["PIT_DEPENDENCY_AUDIT"] == "CLEAN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
