from __future__ import annotations

import hashlib
import json
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import audit_locked_dependency_advisories_v1 as gate

HASH = "a" * 64
MODIFIED = "2026-10-22T16:30:41.002101482Z"
BATCH_MODIFIED = "2026-10-22T16:30:41.002101Z"
GHSA = "GHSA-pjvx-prjx-h659"
CVE = "CVE-2026-114140"
OSV_BATCH = f"{gate.OSV_URL}/querybatch"


def lock_row(name: str, version: str) -> str:
    return f"{name}=={version} \\\n    --hash=sha256:{HASH}\n"


@pytest.fixture
def locks(tmp_path: Path) -> Path:
    requirements = (
        lock_row("Shared_Package", "1.0"),
        lock_row("shared.package", "1.0") + lock_row("shared-new", "2.0"),
        lock_row("shared-package", "2.0") + lock_row("nbconvert", "7.17.1"),
    )
    for filename, content in zip(gate.LOCK_FILES, requirements, strict=True):
        (tmp_path / filename).write_bytes(content.encode())
    return tmp_path


def pypi_response(url: str, advisories: list[dict] | None = None) -> dict:
    name, version, suffix = url.removeprefix(gate.PYPI_URL + "/").split("/")
    assert suffix == "json"
    return {"info": {"name": name, "version": version}, "vulnerabilities": advisories or []}


def reference(identifier: str = GHSA, *, modified: str = MODIFIED) -> dict:
    return {"id": identifier, "modified": modified}


def detail(identifier: str = GHSA, name: str = "nbconvert", **fields: object) -> dict:
    return {
        **reference(identifier),
        "aliases": [CVE],
        "affected": [{"package": {"ecosystem": "PyPI", "name": name}}],
        **fields,
    }


def clean_source(url: str, payload: dict | None = None) -> dict:
    if url == OSV_BATCH:
        assert payload is not None
        return {"results": [{} for _ in payload["queries"]]}
    assert url.startswith(gate.PYPI_URL + "/") and payload is None
    return pypi_response(url)


def test_complete_coverage_queries_unique_normalized_pairs_and_seals_locks(
    locks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict | None]] = []

    def source(url: str, payload: dict | None = None) -> dict:
        calls.append((url, payload))
        return clean_source(url, payload)

    monkeypatch.setattr(gate, "request_json", source)
    output = locks / "evidence" / "advisories.json"
    assert gate.main(["--project-root", str(locks), "--output", str(output)]) == 0
    report = json.loads(output.read_bytes())
    assert report["status"] == gate.SECURITY_PASS
    assert report["findings"] == []
    assert report["started_at"] and report["finished_at"]
    assert {source: row["checked_pairs"] for source, row in report["sources"].items()} == {
        "pypi": 4,
        "osv": 4,
    }
    assert Counter(url for url, _ in calls) == {
        f"{gate.PYPI_URL}/shared-package/1.0/json": 1,
        f"{gate.PYPI_URL}/shared-package/2.0/json": 1,
        f"{gate.PYPI_URL}/shared-new/2.0/json": 1,
        f"{gate.PYPI_URL}/nbconvert/7.17.1/json": 1,
        OSV_BATCH: 1,
    }
    query = next(payload for url, payload in calls if url == OSV_BATCH)
    assert query is not None
    assert {(q["package"]["name"], q["version"]) for q in query["queries"]} == {
        ("shared-package", "1.0"),
        ("shared-package", "2.0"),
        ("shared-new", "2.0"),
        ("nbconvert", "7.17.1"),
    }
    for filename, count in zip(gate.LOCK_FILES, (1, 2, 2), strict=True):
        row = report["per_lock"][filename]
        assert row["sha256"] == hashlib.sha256((locks / filename).read_bytes()).hexdigest()
        assert row["package_count"] == count
        assert all(s["checked_pairs"] == count for s in row["sources"].values())


@pytest.mark.parametrize("also_reported_by_pypi", [False, True])
def test_osv_active_finding_blocks_even_if_pypi_is_clean_and_deduplicates_aliases(
    locks: Path, monkeypatch: pytest.MonkeyPatch, also_reported_by_pypi: bool
) -> None:
    def source(url: str, payload: dict | None = None) -> dict:
        if url == OSV_BATCH:
            assert payload is not None
            return {
                "results": [
                    {"vulns": [reference(modified=BATCH_MODIFIED)]}
                    if q["package"]["name"] == "nbconvert"
                    else {}
                    for q in payload["queries"]
                ]
            }
        if url == f"{gate.OSV_URL}/vulns/{GHSA}":
            return detail()
        if also_reported_by_pypi and "/nbconvert/" in url:
            return pypi_response(url, [{"id": CVE}])
        return clean_source(url, payload)

    monkeypatch.setattr(gate, "request_json", source)
    output = locks / "advisories.json"
    assert gate.main(["--project-root", str(locks), "--output", str(output)]) == 1
    report = json.loads(output.read_bytes())
    assert report["status"] == gate.SECURITY_BLOCKED
    assert report["findings"] == [
        {
            "package": "nbconvert",
            "version": "7.17.1",
            "id": CVE,
            "identifiers": [CVE, GHSA],
            "sources": ["osv", "pypi"] if also_reported_by_pypi else ["osv"],
            "locks": [gate.LOCK_FILES[2]],
        }
    ]


def test_osv_pagination_keeps_the_exact_pin_and_records_withdrawn_advisories(
    locks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_queries = []
    withdrawn = "2020-10-01T00:00:00Z"

    def source(url: str, payload: dict | None = None) -> dict:
        if url == OSV_BATCH:
            assert payload is not None
            page_queries.append(payload["queries"])
            if len(page_queries) == 1:
                return {
                    "results": [
                        {"next_page_token": "nb-page-2"}
                        if q["package"]["name"] == "nbconvert"
                        else {}
                        for q in payload["queries"]
                    ]
                }
            return {"results": [{"vulns": [reference()]}]}
        if url == f"{gate.OSV_URL}/vulns/{GHSA}":
            return detail(withdrawn=withdrawn)
        return clean_source(url, payload)

    monkeypatch.setattr(gate, "request_json", source)
    report = gate.audit_project(locks)
    assert report["status"] == gate.SECURITY_PASS
    assert len(page_queries) == 2
    assert page_queries[1] == [
        {
            "package": {"ecosystem": "PyPI", "name": "nbconvert"},
            "version": "7.17.1",
            "page_token": "nb-page-2",
        }
    ]
    assert report["coverage"][0]["sources"]["osv"]["advisories"] == [
        {"id": GHSA, "aliases": [CVE], "withdrawn": withdrawn}
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "truncated",
        "unexpected",
        "repeated_token",
        "wrong_package",
        "wrong_id",
        "wrong_revision",
        "wrong_nanosecond_revision",
        "bad_withdrawn",
        "http_503",
        "timeout",
    ],
)
def test_osv_missing_or_inconsistent_evidence_fails_closed(
    locks: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def source(url: str, payload: dict | None = None) -> dict:
        if url == OSV_BATCH:
            assert payload is not None
            queries = payload["queries"]
            if failure in {"http_503", "timeout"}:
                raise gate.CoverageError(failure)
            if failure == "truncated":
                return {"results": [{} for _ in queries[:-1]]}
            bad_result = (
                {"unexpected": True} if failure == "unexpected" else {"vulns": [reference()]}
            )
            if failure == "repeated_token":
                bad_result = {"next_page_token": "repeat"}
            return {
                "results": [
                    bad_result if q["package"]["name"] == "nbconvert" else {} for q in queries
                ]
            }
        if url == f"{gate.OSV_URL}/vulns/{GHSA}":
            if failure == "wrong_package":
                return detail(name="different")
            if failure == "wrong_id":
                return detail("GHSA-different")
            if failure == "wrong_revision":
                return detail(modified="2026-11-01T00:00:00Z")
            if failure == "wrong_nanosecond_revision":
                return detail(modified="2026-10-22T16:30:41.002101483Z")
            return detail(withdrawn=False)
        return clean_source(url, payload)

    monkeypatch.setattr(gate, "request_json", source)
    output = locks / "advisories.json"
    assert gate.main(["--project-root", str(locks), "--output", str(output)]) == 2
    report = json.loads(output.read_bytes())
    assert report["status"] == gate.SECURITY_COVERAGE_INCOMPLETE
    assert report["sources"]["pypi"]["complete"] is True
    assert report["sources"]["osv"]["complete"] is False
    assert report["coverage"][0]["sources"]["osv"]["error"]


@pytest.mark.parametrize(
    "failure", ["wrong_name", "wrong_version", "missing_vulnerabilities", "invalid_advisory"]
)
def test_pypi_missing_or_inconsistent_release_fails_closed(
    locks: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def source(url: str, payload: dict | None = None) -> dict:
        if "/nbconvert/" not in url:
            return clean_source(url, payload)
        result = pypi_response(url)
        if failure == "wrong_name":
            result["info"]["name"] = "mismatch"
        elif failure == "wrong_version":
            result["info"]["version"] = "7.17.2"
        elif failure == "missing_vulnerabilities":
            result.pop("vulnerabilities")
        else:
            result["vulnerabilities"] = [{"aliases": [CVE]}]
        return result

    monkeypatch.setattr(gate, "request_json", source)
    report = gate.audit_project(locks)
    assert report["status"] == gate.SECURITY_COVERAGE_INCOMPLETE
    assert report["sources"]["pypi"]["checked_pairs"] == 3
    assert report["sources"]["osv"]["complete"] is True


def test_invalid_lock_does_not_allow_vacuous_pass(
    locks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "request_json", clean_source)
    (locks / gate.LOCK_FILES[0]).write_text(lock_row("Foo_Bar", "1") + lock_row("foo-bar", "2"))
    (locks / gate.LOCK_FILES[1]).write_text("bar>=1.2\n")
    (locks / gate.LOCK_FILES[2]).unlink()
    report = gate.audit_project(locks)
    assert report["status"] == gate.SECURITY_COVERAGE_INCOMPLETE
    assert set(report["lock_errors"]) == set(gate.LOCK_FILES)
    assert all(
        row["checked_pairs"] == 0 and not row["complete"] for row in report["sources"].values()
    )


@pytest.mark.parametrize(
    "body", [b"not JSON", b'{"results":[],"results":[]}', b'{"value":NaN}', b"[]"]
)
def test_malformed_http_payload_is_rejected(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    class Response:
        status = 200

        def geturl(self) -> str:
            return OSV_BATCH

        def read(self, _size: int) -> bytes:
            return body

    monkeypatch.setattr(gate, "urlopen", lambda *_args, **_kwargs: nullcontext(Response()))
    with pytest.raises(gate.CoverageError):
        gate.request_json(OSV_BATCH)


@pytest.mark.parametrize(
    "error", [TimeoutError(), HTTPError(OSV_BATCH, 503, "unavailable", None, None)]
)
def test_http_failures_are_structured_coverage_errors(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(gate, "urlopen", fail)
    with pytest.raises(gate.CoverageError, match="transport_TimeoutError|http_503"):
        gate.request_json(OSV_BATCH)
