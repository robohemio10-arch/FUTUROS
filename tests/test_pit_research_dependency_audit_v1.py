"""Offline evidence fixtures for the read-only dependency gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.markers import default_environment

from scripts.audit_pit_research_dependencies_v1 import AuditResult, audit_dependencies, main


HASH_A = "a" * 64
HASH_B = "b" * 64


@dataclass(frozen=True)
class Evidence:
    lock: Path
    resolver: Path
    installed: Path
    audit: Path

    def run(self) -> AuditResult:
        return audit_dependencies(self.lock, self.resolver, self.installed, self.audit)

    def arguments(self) -> list[str]:
        return [
            "--lock", str(self.lock),
            "--resolver-report", str(self.resolver),
            "--installed-report", str(self.installed),
            "--audit-report", str(self.audit),
        ]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def evidence(tmp_path: Path) -> Evidence:
    files = Evidence(
        tmp_path / "research.lock",
        tmp_path / "resolver.json",
        tmp_path / "installed.json",
        tmp_path / "audit.json",
    )
    files.lock.write_text(
        "# generated lock\n"
        f"pip==25.2 --hash=sha256:{HASH_A}\n"
        'demo_pkg==1.0; python_version >= "0" \\\n'
        f"    --hash=sha256:{HASH_A} \\\n"
        f"    --hash=sha256:{HASH_B}\n"
        "    # via research\n"
        f'inactive==9.0; python_version < "0" --hash=sha256:{HASH_B}\n',
        encoding="utf-8",
    )
    packages = [{"name": "pip", "version": "25.2"}, {"name": "demo-pkg", "version": "1.0"}]
    write_json(files.resolver, {
        "version": "1",
        "environment": default_environment(),
        "install": [
            {"metadata": package, "download_info": {"archive_info": {"hashes": {"sha256": HASH_A}}}}
            for package in packages
        ],
    })
    write_json(files.installed, {"installed": [{"metadata": package} for package in packages]})
    write_json(files.audit, {"dependencies": [{**package, "vulns": []} for package in packages]})
    return files


def assert_blocked(files: Evidence, code: str | None = None) -> None:
    result = files.run()
    assert result["PIT_DEPENDENCY_AUDIT"] == "BLOCKED"
    assert result["PIT_AUDIT_COVERAGE"] == "BLOCKED"
    assert result["runtime_integration_allowed"] is False
    assert result["paper_modified"] is False
    assert result["operational_authority"] is False
    assert result["error_codes"]
    if code is not None:
        assert result["error_codes"] == [code]


def test_complete_consistent_environment_is_clean(evidence: Evidence) -> None:
    assert evidence.run() == {
        "PIT_DEPENDENCY_AUDIT": "CLEAN",
        "PIT_AUDIT_COVERAGE": "2/2",
        "runtime_integration_allowed": False,
        "paper_modified": False,
        "operational_authority": False,
        "error_codes": [],
    }


@pytest.mark.parametrize("field", ["resolver", "installed", "audit"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "version"])
def test_all_reports_require_exact_package_coverage(
    evidence: Evidence, field: str, mutation: str
) -> None:
    path = getattr(evidence, field)
    report = json.loads(path.read_text(encoding="utf-8"))
    key = {"resolver": "install", "installed": "installed", "audit": "dependencies"}[field]
    entries = report[key]
    if mutation == "missing":
        entries.pop()
    elif mutation == "extra":
        extra = json.loads(json.dumps(entries[0]))
        metadata = extra if field == "audit" else extra["metadata"]
        metadata["name"] = "unlocked-package"
        entries.append(extra)
    elif mutation == "duplicate":
        duplicate = json.loads(json.dumps(entries[-1]))
        metadata = duplicate if field == "audit" else duplicate["metadata"]
        metadata["name"] = "DEMO_pkg"
        entries.append(duplicate)
    else:
        metadata = entries[-1] if field == "audit" else entries[-1]["metadata"]
        metadata["version"] = "1.0.1"
    write_json(path, report)
    assert_blocked(evidence)


@pytest.mark.parametrize("line", [
    f"demo-pkg>=1.0 --hash=sha256:{HASH_A}",
    f"demo-pkg==1.* --hash=sha256:{HASH_A}",
    f"demo-pkg===1.0 --hash=sha256:{HASH_A}",
    f"demo-pkg==1.0,!=1.1 --hash=sha256:{HASH_A}",
    "demo-pkg==1.0",
    f"demo-pkg==1.0 --hash=sha512:{HASH_A}",
    "demo-pkg==1.0 --hash=sha256:abc",
    f"demo-pkg @ https://example.invalid/pkg.whl --hash=sha256:{HASH_A}",
    "--index-url https://example.invalid/simple",
    "--extra-index-url https://example.invalid/simple",
    "--trusted-host example.invalid",
    "-r other.lock",
    "-c constraints.txt",
    "-e local-package",
    f"demo-pkg==1.0 --hash=sha256:{HASH_A} --no-deps",
    f'demo-pkg==1.0; unknown_marker == "x" --hash=sha256:{HASH_A}',
    f'demo-pkg>=1.0; python_version < "0" --hash=sha256:{HASH_A}',
    "demo-pkg==1.0 \\",
])
def test_lock_rejects_unsafe_or_incomplete_requirements(evidence: Evidence, line: str) -> None:
    evidence.lock.write_text(line + "\n", encoding="utf-8")
    assert_blocked(evidence)


def test_lock_rejects_duplicate_active_canonical_name(evidence: Evidence) -> None:
    with evidence.lock.open("a", encoding="utf-8") as stream:
        stream.write(f"DEMO-pkg==1.0 --hash=sha256:{HASH_A}\n")
    assert_blocked(evidence, "LOCK_DUPLICATE_ACTIVE_PACKAGE")


def test_disjoint_marker_variants_are_supported(evidence: Evidence) -> None:
    with evidence.lock.open("a", encoding="utf-8") as stream:
        stream.write(f'demo-pkg==9.0; python_version < "0" --hash=sha256:{HASH_B}\n')
    assert evidence.run()["PIT_DEPENDENCY_AUDIT"] == "CLEAN"


def test_lock_without_active_packages_blocks(evidence: Evidence) -> None:
    evidence.lock.write_text(
        f'inactive==9.0; python_version < "0" --hash=sha256:{HASH_B}\n', encoding="utf-8"
    )
    assert_blocked(evidence, "LOCK_NO_ACTIVE_PACKAGES")


def test_second_allowed_artifact_hash_is_accepted(evidence: Evidence) -> None:
    report = json.loads(evidence.resolver.read_text(encoding="utf-8"))
    report["install"][1]["download_info"]["archive_info"]["hashes"]["sha256"] = HASH_B
    write_json(evidence.resolver, report)
    assert evidence.run()["PIT_DEPENDENCY_AUDIT"] == "CLEAN"


@pytest.mark.parametrize("hash_value", [None, "bad", "c" * 64, 1])
def test_resolver_hash_is_mandatory_and_must_belong_to_lock(
    evidence: Evidence, hash_value: object
) -> None:
    report = json.loads(evidence.resolver.read_text(encoding="utf-8"))
    report["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = hash_value
    write_json(evidence.resolver, report)
    assert_blocked(evidence)


@pytest.mark.parametrize("key", list(default_environment()))
def test_resolver_cannot_reuse_other_environment(evidence: Evidence, key: str) -> None:
    report = json.loads(evidence.resolver.read_text(encoding="utf-8"))
    report["environment"][key] = "other-environment"
    write_json(evidence.resolver, report)
    assert_blocked(evidence, "RESOLVER_ENVIRONMENT_MISMATCH")


def test_resolver_environment_is_mandatory(evidence: Evidence) -> None:
    report = json.loads(evidence.resolver.read_text(encoding="utf-8"))
    del report["environment"]
    write_json(evidence.resolver, report)
    assert_blocked(evidence, "RESOLVER_SCHEMA_INVALID")


@pytest.mark.parametrize("key", list(default_environment()))
def test_resolver_environment_requires_every_marker_field(evidence: Evidence, key: str) -> None:
    report = json.loads(evidence.resolver.read_text(encoding="utf-8"))
    del report["environment"][key]
    write_json(evidence.resolver, report)
    assert_blocked(evidence, "RESOLVER_ENVIRONMENT_MISMATCH")


@pytest.mark.parametrize("vulnerability_key", ["vulns", "findings"])
def test_audit_findings_block(evidence: Evidence, vulnerability_key: str) -> None:
    report = json.loads(evidence.audit.read_text(encoding="utf-8"))
    report["dependencies"][0][vulnerability_key] = [{"id": "TEST-ONLY-0001"}]
    write_json(evidence.audit, report)
    assert_blocked(evidence, "AUDIT_VULNERABILITY_FOUND")


@pytest.mark.parametrize("skip_reason", ["not supported", "", None])
def test_audit_skip_blocks_even_with_empty_vulnerabilities(
    evidence: Evidence, skip_reason: object
) -> None:
    report = json.loads(evidence.audit.read_text(encoding="utf-8"))
    report["dependencies"][0]["skip_reason"] = skip_reason
    write_json(evidence.audit, report)
    assert_blocked(evidence, "AUDIT_SKIPPED_PACKAGE")


@pytest.mark.parametrize("field", ["resolver", "installed", "audit"])
@pytest.mark.parametrize("payload", ["", "[]", "{}", "null", "{", '{"x":1,"x":2}', '{"x":NaN}'])
def test_invalid_or_empty_report_schema_blocks(
    evidence: Evidence, field: str, payload: str
) -> None:
    getattr(evidence, field).write_text(payload, encoding="utf-8")
    assert_blocked(evidence)


@pytest.mark.parametrize("field", ["lock", "resolver", "installed", "audit"])
def test_missing_file_blocks(evidence: Evidence, field: str) -> None:
    getattr(evidence, field).unlink()
    assert_blocked(evidence)


@pytest.mark.parametrize("field", ["lock", "resolver", "installed", "audit"])
def test_io_failure_blocks(evidence: Evidence, field: str) -> None:
    path = getattr(evidence, field)
    path.unlink()
    path.mkdir()
    assert_blocked(evidence)


def test_audit_requires_explicit_vulnerability_array(evidence: Evidence) -> None:
    report = json.loads(evidence.audit.read_text(encoding="utf-8"))
    del report["dependencies"][0]["vulns"]
    write_json(evidence.audit, report)
    assert_blocked(evidence, "AUDIT_SCHEMA_INVALID")


@pytest.mark.parametrize("field", ["resolver", "installed", "audit"])
@pytest.mark.parametrize("entries", [[], {}, None, [None]])
def test_report_package_list_schema_must_be_nonempty(
    evidence: Evidence, field: str, entries: object
) -> None:
    key = {"resolver": "install", "installed": "installed", "audit": "dependencies"}[field]
    write_json(getattr(evidence, field), {key: entries})
    assert_blocked(evidence)


@pytest.mark.parametrize("field", ["resolver", "installed", "audit"])
@pytest.mark.parametrize("invalid", ["", 1, None, "not a version!"])
def test_report_metadata_identity_must_be_valid(
    evidence: Evidence, field: str, invalid: object
) -> None:
    path = getattr(evidence, field)
    report = json.loads(path.read_text(encoding="utf-8"))
    key = {"resolver": "install", "installed": "installed", "audit": "dependencies"}[field]
    entry = report[key][0]
    metadata = entry if field == "audit" else entry["metadata"]
    metadata["version"] = invalid
    write_json(path, report)
    assert_blocked(evidence)


def test_invalid_utf8_input_blocks(evidence: Evidence) -> None:
    evidence.audit.write_bytes(b"\xff")
    assert_blocked(evidence, "AUDIT_READ_ERROR")


def test_json_cli_is_read_only_and_exits_zero(
    evidence: Evidence, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = [evidence.lock, evidence.resolver, evidence.installed, evidence.audit]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    assert main([*evidence.arguments(), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["PIT_AUDIT_COVERAGE"] == "2/2"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before


def test_blocked_cli_exits_two_without_echoing_payload(
    evidence: Evidence, capsys: pytest.CaptureFixture[str]
) -> None:
    sensitive_payload = "SENSITIVE_PAYLOAD_MUST_NOT_BE_PRINTED"
    evidence.audit.write_text(sensitive_payload, encoding="utf-8")
    assert main([*evidence.arguments(), "--json"]) == 2
    output = capsys.readouterr().out
    assert sensitive_payload not in output
    assert str(evidence.audit) not in output
    assert json.loads(output)["error_codes"] == ["AUDIT_JSON_INVALID"]


def test_text_cli_shows_gate_status(evidence: Evidence, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(evidence.arguments()) == 0
    output = capsys.readouterr().out
    assert "PIT_DEPENDENCY_AUDIT=CLEAN" in output
    assert "PIT_AUDIT_COVERAGE=2/2" in output
    assert "operational_authority=false" in output
