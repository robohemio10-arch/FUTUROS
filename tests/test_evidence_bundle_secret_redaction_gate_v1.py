from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts.build_sanitized_evidence_bundle_v1 import main
from smartcrypto.security.evidence_bundle_redaction import (
    build_sanitized_evidence_bundle_v1,
    redact_text,
    scan_source,
)


def github_pat() -> str:
    return "gh" + "p_" + "A" * 36


def api_key() -> str:
    return "sk-" + "B" * 32


def telegram_token() -> str:
    return "123456789:" + "C" * 30


def jwt_token() -> str:
    return "eyJ" + "D" * 12 + "." + "E" * 14 + "." + "F" * 14


def pem_key() -> str:
    begin = "-----BEGIN " + "PRIVATE KEY-----"
    end = "-----END " + "PRIVATE KEY-----"
    return begin + "\n" + "R" * 64 + "\n" + end


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def scan_text_value(tmp_path: Path, text: str, name: str = "evidence.txt") -> dict[str, Any]:
    source = write_text(tmp_path / name, text)
    return build_sanitized_evidence_bundle_v1(project_root=tmp_path, source=source)


def test_detects_synthetic_github_pat(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "token=" + github_pat())
    assert report["findings"][0]["category"] == "github_token"


def test_detects_synthetic_api_key(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "value=" + api_key())
    assert report["findings"][0]["category"] == "api_key"


def test_detects_synthetic_telegram_token(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "bot=" + telegram_token())
    assert report["findings"][0]["category"] == "telegram_token"


def test_detects_synthetic_jwt(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "session=" + jwt_token())
    assert report["findings"][0]["category"] == "jwt"


def test_detects_bearer_token(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "Authorization: Bearer " + "G" * 32)
    assert any(item["matched_pattern_name"] == "authorization_header" for item in report["findings"])


def test_detects_authenticated_url(tmp_path: Path) -> None:
    secret = "H" * 24
    report = scan_text_value(tmp_path, "https://operator:" + secret + "@example.test/path")
    assert report["findings"][0]["category"] == "authenticated_url"


def test_detects_synthetic_pem_private_key(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, pem_key())
    assert report["findings"][0]["category"] == "private_key"


def test_detects_secret_in_json(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, json.dumps({"api_key": "J" * 28}), "evidence.json")
    assert report["secret_finding_count"] == 1


def test_detects_secret_in_yaml(tmp_path: Path) -> None:
    report = scan_text_value(tmp_path, "password: " + "K" * 28, "evidence.yaml")
    assert report["secret_finding_count"] == 1


def test_detects_secret_in_env_file(tmp_path: Path) -> None:
    source = write_text(tmp_path / ".env", "ACCESS_KEY=" + "L" * 28)
    report = build_sanitized_evidence_bundle_v1(project_root=tmp_path, source=source)
    assert report["secret_finding_count"] == 1
    assert report["decision"] == "BLOCKED_FORBIDDEN_FILE"


def test_dot_env_is_forbidden_without_secret_finding(tmp_path: Path) -> None:
    source = write_text(tmp_path / ".env", "SAFE_FLAG=false")
    report = build_sanitized_evidence_bundle_v1(project_root=tmp_path, source=source)
    assert report["decision"] == "BLOCKED_FORBIDDEN_FILE"
    assert report["forbidden_file_count"] == 1


def test_redaction_does_not_preserve_original_secret() -> None:
    secret = "M" * 30
    result = redact_text("API_KEY=" + secret, relative_path="safe.txt")
    assert secret not in result.redacted_text
    assert result.redacted_text.startswith("API_KEY=<REDACTED:")


def test_finding_fingerprint_is_deterministic() -> None:
    text = "TOKEN=" + "N" * 30
    first = redact_text(text, relative_path="safe.txt")
    second = redact_text(text, relative_path="safe.txt")
    assert first.findings[0].secret_fingerprint_sha256 == second.findings[0].secret_fingerprint_sha256
    assert first.findings[0].finding_id == second.findings[0].finding_id


def test_report_contains_no_secret_value(tmp_path: Path) -> None:
    secret = "P" * 30
    report = scan_text_value(tmp_path, "PASSWORD=" + secret)
    serialized = json.dumps(report, sort_keys=True)
    assert secret not in serialized
    assert set(report["findings"][0]) == {
        "finding_id",
        "category",
        "severity",
        "relative_path",
        "line_number",
        "column_number",
        "matched_pattern_name",
        "redacted_preview",
        "secret_fingerprint_sha256",
        "blocking",
        "remediation",
    }


def test_interpolated_compose_output_is_blocked(tmp_path: Path) -> None:
    source = write_text(tmp_path / "docker-compose-config.yml", "API_KEY: " + "Q" * 30)
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        compose_output_mode="interpolated",
    )
    assert report["decision"] == "BLOCKED_COMPOSE_INTERPOLATION"
    assert report["compose_interpolation_detected"] is True
    assert report["compose_output_allowed"] is False


def test_no_interpolate_compose_output_is_allowed_after_scan(tmp_path: Path) -> None:
    source = write_text(tmp_path / "docker-compose-config.yml", "API_KEY: ${API_KEY}\n")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        compose_output_mode="no-interpolate",
    )
    assert report["decision"] == "BUNDLE_SAFE_TO_CREATE"
    assert report["compose_output_allowed"] is True


def test_zip_path_traversal_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape.txt", "safe")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        allowed_files=["../escape.txt"],
    )
    assert report["decision"] == "BLOCKED_UNSAFE_ARCHIVE_ENTRY"


def test_zip_absolute_path_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("C:/absolute.txt", "safe")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        allowed_files=["C:/absolute.txt"],
    )
    assert report["decision"] == "BLOCKED_UNSAFE_ARCHIVE_ENTRY"


def test_zip_duplicate_entry_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("same.txt", "one")
            archive.writestr("same.txt", "two")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        allowed_files=["same.txt"],
    )
    assert report["decision"] == "BLOCKED_UNSAFE_ARCHIVE_ENTRY"


def test_file_outside_allowlist_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_text(source / "allowed.txt", "safe")
    write_text(source / "unexpected.txt", "safe")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        allowed_files=["allowed.txt"],
    )
    assert report["decision"] == "BLOCKED_ALLOWLIST_VIOLATION"
    assert report["allowlist_violation_count"] == 1


def test_default_without_source_writes_nothing(tmp_path: Path) -> None:
    report = build_sanitized_evidence_bundle_v1(project_root=tmp_path)
    assert report["decision"] == "BLOCKED_INPUT_NOT_FOUND"
    assert report["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_write_report_writes_only_data_reports(tmp_path: Path) -> None:
    source = write_text(tmp_path / "source.txt", "safe evidence")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        write_report=True,
    )
    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "evidence_bundle_secret_redaction_gate_v1.json").is_file()
    assert not (tmp_path / "data" / "runtime").exists()


def test_final_bundle_is_rescanned(tmp_path: Path) -> None:
    source = write_text(tmp_path / "source.txt", "safe evidence")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir="data/reports/evidence_bundles/run",
    )
    assert report["final_bundle_validation"]["status"] == "ok"
    assert report["final_bundle_validation"]["scanned_archive_entry_count"] == 1


def test_final_bundle_contains_no_synthetic_secret(tmp_path: Path) -> None:
    secret = "S" * 30
    source = write_text(tmp_path / "source.txt", "SECRET=" + secret)
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir="data/reports/evidence_bundles/run",
    )
    assert report["decision"] == "BUNDLE_SAFE_AFTER_REDACTION"
    with zipfile.ZipFile(tmp_path / report["bundle_path"]) as archive:
        assert secret.encode() not in archive.read("source.txt")


def test_bundle_hash_is_deterministic_for_identical_content(tmp_path: Path) -> None:
    source = write_text(tmp_path / "source.txt", "safe evidence")
    first = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir="data/reports/evidence_bundles/one",
    )
    second = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir="data/reports/evidence_bundles/two",
    )
    assert first["bundle_sha256"] == second["bundle_sha256"]


def test_staging_directory_is_removed(tmp_path: Path) -> None:
    source = write_text(tmp_path / "source.txt", "safe evidence")
    output = tmp_path / "data" / "reports" / "evidence_bundles" / "run"
    build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir=output,
    )
    assert not list(output.glob(".evidence_staging_*"))


def test_failed_build_leaves_no_partial_zip(tmp_path: Path) -> None:
    source = write_text(tmp_path / "source.txt", "X" * 100)
    output = tmp_path / "data" / "reports" / "evidence_bundles" / "run"
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source,
        build_sanitized_bundle=True,
        output_dir=output,
        max_file_bytes=10,
    )
    assert report["status"] == "blocked"
    assert not (output / "sanitized_evidence_bundle.zip").exists()
    assert not list(output.glob(".sanitized_bundle_*")) if output.exists() else True


def test_safety_flags_remain_false(tmp_path: Path) -> None:
    report = build_sanitized_evidence_bundle_v1(project_root=tmp_path)
    assert report["paper_only"] is True
    assert report["security_only"] is True
    assert report["read_only"] is True
    for field in (
        "live_trading_enabled",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "runs_training",
        "writes_runtime",
        "writes_feedback",
        "writes_sqlite",
        "writes_parquet",
        "writes_models",
        "writes_registries",
    ):
        assert report[field] is False


def test_relative_paths_use_posix_separators(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    write_text(source_dir / "nested" / "safe.txt", "safe")
    report = build_sanitized_evidence_bundle_v1(
        project_root=tmp_path,
        source=source_dir,
        allowed_files=["nested/safe.txt"],
    )
    serialized = json.dumps(report)
    assert "nested\\safe.txt" not in serialized
    assert all("\\" not in finding["relative_path"] for finding in report["findings"])


def test_stdout_json_never_contains_synthetic_secret(tmp_path: Path, capsys: Any) -> None:
    secret = "Z" * 30
    source = write_text(tmp_path / "source.txt", "PASSWORD=" + secret)
    exit_code = main(["--project-root", str(tmp_path), "--source", str(source), "--json"])
    stdout = capsys.readouterr().out
    assert exit_code == 1
    assert secret not in stdout
    assert json.loads(stdout)["decision"] == "BLOCKED_SECRET_FINDINGS"


def test_zip_scanner_does_not_extract_entries(tmp_path: Path) -> None:
    source = tmp_path / "safe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("nested/safe.txt", "safe")
    result = scan_source(source, allowed_files=["nested/safe.txt"])
    assert result.allowed_file_count == 1
    assert not (tmp_path / "nested").exists()
