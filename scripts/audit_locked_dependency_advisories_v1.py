"""Query PyPI and OSV for every exact Python pin in the three security locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import NamedTuple
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

LOCK_FILES = (
    "requirements-dev.lock",
    "requirements-runtime.lock",
    "requirements-qlib-security.lock",
)
PYPI_URL = "https://pypi.org/pypi"
OSV_URL = "https://api.osv.dev/v1"
OSV_BATCH_SIZE = 80
OSV_MAX_PAGES = 100
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SCHEMA_VERSION = "locked_dependency_advisories_v1"
SECURITY_PASS = "SECURITY_PASS"
SECURITY_BLOCKED = "SECURITY_BLOCKED"
SECURITY_COVERAGE_INCOMPLETE = "SECURITY_COVERAGE_INCOMPLETE"
EXIT_CODES = {SECURITY_PASS: 0, SECURITY_BLOCKED: 1, SECURITY_COVERAGE_INCOMPLETE: 2}
PIN_RE = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([0-9][A-Za-z0-9.!+_-]*)"
)
HASH_RE = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}")
ADVISORY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})")
SUBMICROSECOND_RE = re.compile(r"\.\d{7,9}")


class CoverageError(ValueError):
    """The inputs or source response do not prove complete coverage."""


class Pin(NamedTuple):
    name: str
    version: str


@dataclass(frozen=True)
class Advisory:
    id: str
    aliases: tuple[str, ...]
    withdrawn: str | None


@dataclass
class SourceAudit:
    checked: set[Pin] = field(default_factory=set)
    records: dict[Pin, list[Advisory]] = field(default_factory=dict)
    errors: dict[Pin, str] = field(default_factory=dict)


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(data: bytes) -> set[Pin]:
    pins: set[Pin] = set()
    names: set[str] = set()
    fragments: list[str] = []
    for line_number, raw in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        line = raw.partition("#")[0].strip()
        if not line:
            if fragments:
                raise CoverageError(f"interrupted_pin_at_line_{line_number}")
            continue
        fragments.append(line.removesuffix("\\").rstrip())
        if line.endswith("\\"):
            continue
        spec, *hashes = " ".join(fragments).split()
        fragments.clear()
        match = PIN_RE.fullmatch(spec)
        if match is None or not hashes or any(HASH_RE.fullmatch(h) is None for h in hashes):
            raise CoverageError(f"invalid_hashed_pin_at_line_{line_number}")
        pin = Pin(normalize_name(match[1]), match[2])
        if pin.name in names:
            raise CoverageError(f"duplicate_normalized_pin_at_line_{line_number}")
        names.add(pin.name)
        pins.add(pin)
    if fragments or not pins:
        raise CoverageError("empty_lock_or_unterminated_pin")
    return pins


def json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CoverageError(f"{label}_must_be_object")
    return value


def json_array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CoverageError(f"{label}_must_be_array")
    return value


def unique_json_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(items)
    if len(result) != len(items):
        raise CoverageError("duplicate_json_key")
    return result


def reject_json_constant(_value: str) -> None:
    raise CoverageError("invalid_json_constant")


def request_json(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    request = Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store",
            "User-Agent": "SmartCrypto-locked-advisory-audit/1",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200 or response.geturl() != url:
                raise CoverageError("unexpected_http_status_or_redirect")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise CoverageError(f"http_{exc.code}") from exc
    except (OSError, HTTPException) as exc:
        raise CoverageError(f"transport_{type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CoverageError("response_size_limit_exceeded")
    try:
        value = json.loads(
            raw, object_pairs_hook=unique_json_keys, parse_constant=reject_json_constant
        )
    except (ValueError, RecursionError) as exc:
        raise CoverageError("invalid_json_response") from exc
    return json_object(value, "response")


def advisory_id(value: object) -> str:
    if not isinstance(value, str) or not ADVISORY_ID_RE.fullmatch(value):
        raise CoverageError("invalid_advisory_id")
    return value


def timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise CoverageError(f"invalid_{label}_timestamp")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CoverageError(f"invalid_{label}_timestamp") from exc
    if moment.utcoffset() is None:
        raise CoverageError(f"invalid_{label}_timestamp")
    return value


def same_osv_revision(reference: str, detail: str) -> bool:
    # querybatch may truncate to microseconds while the detail retains nanoseconds.
    return reference == detail or (
        SUBMICROSECOND_RE.search(reference) is None
        and datetime.fromisoformat(reference) == datetime.fromisoformat(detail)
    )


def read_advisory(data: dict[str, object]) -> Advisory:
    aliases = tuple(advisory_id(alias) for alias in json_array(data.get("aliases", []), "aliases"))
    withdrawn = data.get("withdrawn")
    return Advisory(
        advisory_id(data.get("id")),
        aliases,
        None if withdrawn is None else timestamp(withdrawn, "withdrawn"),
    )


def pypi_advisories(pin: Pin) -> list[Advisory]:
    data = request_json(f"{PYPI_URL}/{quote(pin.name, safe='')}/{quote(pin.version, safe='')}/json")
    identity = json_object(data.get("info"), "pypi_info")
    name = identity.get("name")
    if (
        not isinstance(name, str)
        or normalize_name(name) != pin.name
        or identity.get("version") != pin.version
    ):
        raise CoverageError("pypi_release_identity_mismatch")
    return [
        read_advisory(json_object(v, "pypi_advisory"))
        for v in json_array(data.get("vulnerabilities"), "pypi_vulnerabilities")
    ]


def audit_pypi(pins: list[Pin]) -> SourceAudit:
    result = SourceAudit()
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pin: pool.submit(pypi_advisories, pin) for pin in pins}
        for pin, future in futures.items():
            try:
                result.records[pin] = future.result()
                result.checked.add(pin)
            except CoverageError as exc:
                result.errors[pin] = str(exc)
    return result


def osv_detail(
    reference: object, pin: Pin, memo: dict[tuple[str, str], dict[str, object]]
) -> Advisory:
    entry = json_object(reference, "osv_advisory_reference")
    identifier = advisory_id(entry.get("id"))
    modified = timestamp(entry.get("modified"), "osv_modified")
    key = (identifier, modified)
    if key not in memo:
        data = request_json(f"{OSV_URL}/vulns/{quote(identifier, safe='')}")
        detail_modified = timestamp(data.get("modified"), "osv_modified")
        if data.get("id") != identifier or not same_osv_revision(modified, detail_modified):
            raise CoverageError("osv_advisory_identity_mismatch")
        memo[key] = data
    data = memo[key]
    packages = [
        json_object(json_object(row, "osv_affected_entry").get("package"), "osv_affected_package")
        for row in json_array(data.get("affected"), "osv_affected")
    ]
    if not any(
        pkg.get("ecosystem") == "PyPI"
        and isinstance(name := pkg.get("name"), str)
        and normalize_name(name) == pin.name
        for pkg in packages
    ):
        raise CoverageError("osv_advisory_package_mismatch")
    return read_advisory(data)


def osv_query(pin: Pin, token: str | None) -> dict[str, object]:
    query: dict[str, object] = {
        "package": {"ecosystem": "PyPI", "name": pin.name},
        "version": pin.version,
    }
    if token is not None:
        query["page_token"] = token
    return query


def audit_osv(pins: list[Pin]) -> SourceAudit:
    result = SourceAudit()
    memo: dict[tuple[str, str], dict[str, object]] = {}
    for start in range(0, len(pins), OSV_BATCH_SIZE):
        pending: dict[Pin, str | None] = dict.fromkeys(pins[start : start + OSV_BATCH_SIZE])
        tokens: dict[Pin, set[str]] = {pin: set() for pin in pending}
        for _ in range(OSV_MAX_PAGES):
            if not pending:
                break
            try:
                payload = request_json(
                    f"{OSV_URL}/querybatch",
                    {
                        "queries": [osv_query(pin, token) for pin, token in pending.items()],
                    },
                )
                rows = json_array(payload.get("results"), "osv_results")
                # OSV guarantees positional responses. A truncated result cannot prove any pairing.
                if set(payload) != {"results"} or len(rows) != len(pending):
                    raise CoverageError("osv_batch_response_coverage_mismatch")
            except CoverageError as exc:
                result.errors.update(dict.fromkeys(pending, str(exc)))
                pending.clear()
                break
            next_page: dict[Pin, str | None] = {}
            for pin, row in zip(pending, rows, strict=True):
                try:
                    entry = json_object(row, "osv_result")
                    if set(entry) - {"vulns", "next_page_token"}:
                        raise CoverageError("unexpected_osv_result_fields")
                    records = result.records.setdefault(pin, [])
                    for reference in json_array(entry.get("vulns", []), "osv_vulns"):
                        advisory = osv_detail(reference, pin, memo)
                        if advisory not in records:
                            records.append(advisory)
                    if "next_page_token" not in entry:
                        result.checked.add(pin)
                        continue
                    token = entry["next_page_token"]
                    if (
                        not isinstance(token, str)
                        or not token
                        or len(token) > 4096
                        or token in tokens[pin]
                    ):
                        raise CoverageError("invalid_or_repeated_osv_page_token")
                    tokens[pin].add(token)
                    next_page[pin] = token
                except CoverageError as exc:
                    result.errors[pin] = str(exc)
            pending = next_page
        result.errors.update(dict.fromkeys(pending, "osv_page_limit_exceeded"))
    return result


def active_groups(
    records: list[tuple[str, Advisory]], now: datetime
) -> list[tuple[set[str], set[str]]]:
    """Merge overlapping aliases transitively per pin and preserve all active sources."""
    groups: list[tuple[set[str], set[str]]] = []
    for source, advisory in records:
        if advisory.withdrawn is not None and datetime.fromisoformat(advisory.withdrawn) <= now:
            continue
        ids = {canonical_id(value) for value in (advisory.id, *advisory.aliases)}
        sources = {source}
        disjoint: list[tuple[set[str], set[str]]] = []
        for other_ids, other_sources in groups:
            if ids.intersection(other_ids):
                ids.update(other_ids)
                sources.update(other_sources)
            else:
                disjoint.append((other_ids, other_sources))
        groups = [*disjoint, (ids, sources)]
    return groups


def canonical_id(value: str) -> str:
    normalized = value.upper()
    return f"GHSA-{normalized[5:].lower()}" if normalized.startswith("GHSA-") else normalized


def audit_project(root: Path) -> dict[str, object]:
    started = datetime.now(timezone.utc)
    locks: dict[str, set[Pin]] = {}
    hashes: dict[str, str] = {}
    lock_errors: dict[str, str] = {}
    for filename in LOCK_FILES:
        try:
            path = root / filename
            if path.is_symlink() or not path.is_file():
                raise CoverageError("lock_not_a_regular_file")
            data = path.read_bytes()
            hashes[filename] = hashlib.sha256(data).hexdigest()
            locks[filename] = parse_lock(data)
        except (CoverageError, OSError, UnicodeError) as exc:
            lock_errors[filename] = (
                str(exc) if isinstance(exc, CoverageError) else type(exc).__name__
            )
    expected_pins = {pin for inventory in locks.values() for pin in inventory}
    pins = sorted(expected_pins)
    sources = {"pypi": audit_pypi(pins), "osv": audit_osv(pins)}
    findings = []
    coverage = []
    for pin in pins:
        files = [filename for filename, inventory in locks.items() if pin in inventory]
        records = [
            (source, item)
            for source, audit in sources.items()
            for item in audit.records.get(pin, [])
        ]
        for ids, seen_by in active_groups(records, started):
            identifiers = sorted(ids, key=lambda value: (not value.startswith("CVE-"), value))
            findings.append(
                {
                    "package": pin.name,
                    "version": pin.version,
                    "id": identifiers[0],
                    "identifiers": identifiers,
                    "sources": sorted(seen_by),
                    "locks": files,
                }
            )
        coverage.append(
            {
                "package": pin.name,
                "version": pin.version,
                "locks": files,
                "sources": {
                    source: {
                        "checked": pin in audit.checked,
                        "error": audit.errors.get(pin),
                        "advisories": [
                            {
                                "id": item.id,
                                "aliases": sorted(item.aliases),
                                "withdrawn": item.withdrawn,
                            }
                            for item in sorted(
                                audit.records.get(pin, []), key=lambda advisory: advisory.id
                            )
                        ],
                    }
                    for source, audit in sources.items()
                },
            }
        )
    findings.sort(key=lambda finding: (finding["package"], finding["version"], finding["id"]))
    complete = {
        source: not lock_errors and not audit.errors and audit.checked == expected_pins
        for source, audit in sources.items()
    }
    if not all(complete.values()):
        status = SECURITY_COVERAGE_INCOMPLETE
    else:
        status = SECURITY_BLOCKED if findings else SECURITY_PASS
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "head_sha": os.environ.get("ADVISORY_AUDIT_HEAD_SHA"),
        "checkout_sha": os.environ.get("ADVISORY_AUDIT_CHECKOUT_SHA"),
        "workflow_sha": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "lock_errors": lock_errors,
        "per_lock": {
            filename: {
                "sha256": hashes.get(filename),
                "package_count": len(locks.get(filename, set())),
                "sources": {
                    source: {
                        "checked_pairs": len(
                            audit.checked.intersection(locks.get(filename, set()))
                        ),
                        "active_findings": sum(
                            filename in f["locks"] and source in f["sources"] for f in findings
                        ),
                    }
                    for source, audit in sources.items()
                },
            }
            for filename in LOCK_FILES
        },
        "sources": {
            source: {
                "endpoint": PYPI_URL if source == "pypi" else OSV_URL,
                "requested_pairs": len(pins),
                "checked_pairs": len(audit.checked),
                "complete": complete[source],
                "active_findings": sum(source in f["sources"] for f in findings),
            }
            for source, audit in sources.items()
        },
        "coverage": coverage,
        "findings": findings,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".json" or args.output.is_symlink():
        parser.error("--output must be a JSON file and cannot be a symlink")
    report = audit_project(args.project_root)
    try:
        write_report(args.output, report)
    except OSError as exc:
        report["status"] = SECURITY_COVERAGE_INCOMPLETE
        report["report_error"] = type(exc).__name__
        print(json.dumps(report, sort_keys=True))
        return EXIT_CODES[SECURITY_COVERAGE_INCOMPLETE]
    print(
        json.dumps(
            {"status": report["status"], "sources": report["sources"], "report": str(args.output)}
        )
    )
    return EXIT_CODES[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
