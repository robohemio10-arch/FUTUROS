from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.authoritative_evidence_inventory import (
    PRIORITY_FIELDS,
    SAFETY_FLAGS,
    build_trader_master_authoritative_evidence_inventory_report,
    discover_evidence_candidates,
    inspect_evidence_artifact,
)
from smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite import (
    inspect_sqlite_schema_readonly,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inventory_trader_master_authoritative_evidence_v2.py"
SOURCE_PROFILE = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"
ACCOUNT_HASH = "c" * 64
FIXED_TIME = "2026-07-13T00:00:00+00:00"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_project(root: Path) -> tuple[Path, Path, Path]:
    master = root / "data" / "trades" / "trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"source_file": "full_ocr_3141", "symbol": "BTCUSDT", "side": "long"},
            {
                "source_file": "manual_queue_resolved",
                "symbol": "ETHUSDT",
                "side": "short",
            },
        ]
    ).to_parquet(master, index=False)
    profile = root / "config" / SOURCE_PROFILE.name
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_bytes(SOURCE_PROFILE.read_bytes())
    evidence = root / "evidence"
    evidence.mkdir()
    return master, profile, evidence


def declaration(
    cohort: str,
    fields: tuple[str, ...],
    *,
    join_classification: str = "exact_native_id",
    digest_seed: str = "same",
    producer: str = "fixture.authoritative_export",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "trader_master_authoritative_evidence_v2",
        "provenance_classification": "authoritative",
        "producer": producer,
        "source_cohort": cohort,
        "field_semantics": {field: f"versioned semantics for {field}" for field in fields},
        "join_contract": {
            "classification": join_classification,
            "fields": ["source_trade_id"] if join_classification != "cohort_level_only" else [],
            "deterministic_per_row": True,
            "uniqueness_verified": True,
            "fuzzy_matching": False,
        },
        "field_value_digests": {
            field: hashlib.sha256(f"{digest_seed}:{field}".encode()).hexdigest()
            for field in fields
        },
    }
    if set(fields) & {
        "market_type",
        "contract_type",
        "settlement_currency",
        "quantity_unit",
        "contract_size",
    }:
        payload["instrument_scope"] = {
            "schema_version": "instrument_scope_v1",
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "market_type": "usdt-m_futures",
            "valid_from_utc": "2026-01-01T00:00:00Z",
            "valid_to_utc": "2026-12-31T23:59:59Z",
        }
    if "account_scope_hash" in fields:
        payload["account_scope_attestation"] = {
            "account_scope_hash": ACCOUNT_HASH,
            "provenance": "sanitized dual-control fixture",
            "original_identifier_included": False,
        }
    financial = set(fields) & {"gross_pnl", "trading_fee", "funding_fee", "epsilon_abs_fonte"}
    if financial:
        payload["financial_provenance"] = {
            "source_columns": {field: f"authoritative.{field}" for field in financial},
            "formulas": {field: f"versioned_formula:{field}" for field in financial},
        }
    return payload


def write_declaration(
    evidence: Path,
    name: str,
    cohort: str,
    fields: tuple[str, ...],
    **kwargs: Any,
) -> Path:
    path = evidence / name
    path.write_text(
        json.dumps(declaration(cohort, fields, **kwargs), sort_keys=True),
        encoding="utf-8",
    )
    return path


def run_inventory(root: Path, **kwargs: Any) -> dict[str, Any]:
    master, profile, evidence = setup_project(root)
    return build_trader_master_authoritative_evidence_inventory_report(
        project_root=root,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
        **kwargs,
    )


def test_scan_remains_inside_project_root(tmp_path: Path) -> None:
    master, profile, _ = setup_project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[outside],
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_evidence_root"


def test_symlinks_are_blocked(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    target = write_declaration(evidence, "ocr_target.json", "full_ocr_3141", ("source_trade_id",))
    link = evidence / "ocr_link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    discovery = discover_evidence_candidates(
        project_root=tmp_path,
        evidence_roots=[evidence],
    )
    assert link in discovery.blocked_symlinks


def test_env_is_never_discovered(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    (evidence / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    discovery = discover_evidence_candidates(project_root=tmp_path, evidence_roots=[evidence])
    assert not discovery.candidate_paths
    assert discovery.ignored_forbidden_file_count == 1


def test_secret_content_is_blocked_and_not_exposed(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    token = "gh" + "p_" + "A" * 40
    (evidence / "ocr_secret_evidence.json").write_text(
        json.dumps({"source_cohort": "full_ocr_3141", "token": token}),
        encoding="utf-8",
    )
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    serialized = json.dumps(report)
    assert token not in serialized
    assert report["artifact_blocked_count"] == 1


def test_artifact_hash_and_size_are_preserved(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    path = write_declaration(evidence, "ocr_evidence.json", "full_ocr_3141", ("source_trade_id",))
    before = sha256(path)
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert artifact.sha256 == before == sha256(path)
    assert artifact.size_bytes == path.stat().st_size
    assert artifact.integrity_preserved is True


def test_regular_artifact_uses_temporary_copy(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    path = write_declaration(evidence, "ocr_evidence.json", "full_ocr_3141", ("source_trade_id",))
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert artifact.temp_copy_used is True


def test_sqlite_uses_readonly_query_only_copy(tmp_path: Path) -> None:
    database = tmp_path / "data" / "evidence" / "ocr_orders.sqlite"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE fills (source_trade_id TEXT UNIQUE, trading_fee REAL)")
    connection.execute("INSERT INTO fills VALUES ('fixture-1', 0.1)")
    connection.commit()
    connection.close()
    before = sha256(database)
    inspected = inspect_sqlite_schema_readonly(project_root=tmp_path, snapshot_path=database)
    assert inspected["status"] == "ok"
    assert inspected["snapshot_temp_copy_used"] is True
    assert inspected["snapshot_query_only"] is True
    assert inspected["snapshot_source_hashes_preserved"] is True
    assert sha256(database) == before


def test_zip_is_inventory_only_and_not_extracted(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    archive = evidence / "ocr_batch.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("inside.json", "{}")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=archive)
    assert artifact.artifact_type == "archive"
    assert artifact.archive_extracted is False


def test_image_is_inventory_only_and_no_ocr_runs(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    image = evidence / "ocr_batch_image.png"
    image.write_bytes(b"not-a-real-image")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=image)
    assert artifact.artifact_type == "image"
    assert artifact.ocr_executed is False


def test_filename_never_defines_authority(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    path = evidence / "authoritative_full_ocr_3141_contract_size.csv"
    path.write_text("source_trade_id,contract_size\n1,1\n", encoding="utf-8")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert artifact.authority_classification == "informational_only"


def test_order_id_pattern_does_not_define_namespace(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    (evidence / "full_ocr_3141_orders.csv").write_text(
        "order_id\nfreqtrade-paper-1\n", encoding="utf-8"
    )
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    item = _field(report, "full_ocr_3141", "order_id_namespace")
    assert item["selected_classification"] == "missing"


@pytest.mark.parametrize(
    "field",
    [
        "account_scope_hash",
        "contract_size",
        "quantity_unit",
        "settlement_currency",
        "gross_pnl",
        "trading_fee",
        "funding_fee",
        "epsilon_abs_fonte",
    ],
)
def test_missing_values_receive_no_defaults(tmp_path: Path, field: str) -> None:
    report = run_inventory(tmp_path)
    assert _field(report, "full_ocr_3141", field)["selected_classification"] == "missing"


def test_net_pnl_is_not_decomposed(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    (evidence / "full_ocr_3141_net.csv").write_text("net_pnl\n1.0\n", encoding="utf-8")
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    assert _field(report, "full_ocr_3141", "gross_pnl")["selected_classification"] == "missing"


def test_joinable_evidence_requires_exact_verified_key(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    path = write_declaration(
        evidence,
        "ocr_cohort_only.json",
        "full_ocr_3141",
        ("source_trade_id",),
        join_classification="cohort_level_only",
    )
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert artifact.authority_classification == "authoritative_but_not_joinable"


def test_fuzzy_join_contract_is_rejected(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    payload = declaration("full_ocr_3141", ("source_trade_id",))
    payload["join_contract"]["fuzzy_matching"] = True
    path = evidence / "ocr_fuzzy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert "deterministic_exact_join_not_verified" in artifact.blockers
    assert artifact.authority_classification != "authoritative_and_joinable"


def test_authoritative_nonjoinable_decision(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    write_declaration(
        evidence,
        "ocr_cohort_only.json",
        "full_ocr_3141",
        ("source_trade_id",),
        join_classification="cohort_level_only",
    )
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    assert report["decision"] == "AUTHORITATIVE_EVIDENCE_NOT_JOINABLE"


def test_conflicting_authorities_are_blocking(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    for seed in ("first", "second"):
        write_declaration(
            evidence,
            f"full_ocr_3141_{seed}.json",
            "full_ocr_3141",
            ("source_trade_id",),
            digest_seed=seed,
        )
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    assert report["decision"] == "CONFLICTING_AUTHORITATIVE_EVIDENCE"
    assert report["conflicting_count"] == 1


def test_partial_evidence_never_releases_bridge(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    write_declaration(
        evidence,
        "full_ocr_3141_partial.json",
        "full_ocr_3141",
        ("source_trade_id",),
    )
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    assert report["decision"] == "PARTIAL_AUTHORITATIVE_EVIDENCE_FOUND"
    assert report["bridge_design_preconditions_satisfied"] is False
    assert report["bridge_applied"] is False


def test_complete_synthetic_coverage_reaches_complete_decision(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    for cohort in ("full_ocr_3141", "manual_queue_resolved"):
        write_declaration(evidence, f"{cohort}_complete.json", cohort, PRIORITY_FIELDS)
    report = build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    assert report["decision"] == "AUTHORITATIVE_EVIDENCE_COMPLETE_AND_JOINABLE"
    assert report["authoritative_and_joinable_count"] == 24
    assert report["bridge_design_preconditions_satisfied"] is True
    assert report["bridge_applied"] is False


def test_no_authoritative_evidence_decision(tmp_path: Path) -> None:
    report = run_inventory(tmp_path)
    assert report["decision"] == "NO_AUTHORITATIVE_EVIDENCE_FOUND"


def test_account_attestation_must_match_explicit_hash(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    payload = declaration("full_ocr_3141", ("account_scope_hash",))
    payload["account_scope_attestation"]["account_scope_hash"] = "d" * 64
    path = evidence / "full_ocr_3141_account.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    artifact = inspect_evidence_artifact(
        project_root=tmp_path,
        path=path,
        expected_account_scope_hash=ACCOUNT_HASH,
    )
    assert "sanitized_account_scope_attestation_missing" in artifact.blockers


def test_instrument_fields_require_versioned_temporal_scope(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    payload = declaration("full_ocr_3141", ("contract_size",))
    payload.pop("instrument_scope")
    path = evidence / "full_ocr_3141_contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert "versioned_instrument_scope_missing" in artifact.blockers


def test_financial_fields_require_source_columns_and_formulas(tmp_path: Path) -> None:
    _, _, evidence = setup_project(tmp_path)
    payload = declaration("full_ocr_3141", ("funding_fee",))
    payload.pop("financial_provenance")
    path = evidence / "full_ocr_3141_funding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    artifact = inspect_evidence_artifact(project_root=tmp_path, path=path)
    assert "financial_source_columns_incomplete" in artifact.blockers


def test_safety_flags_and_import_eligibility_remain_closed(tmp_path: Path) -> None:
    report = run_inventory(tmp_path)
    assert report["import_eligible_true_count"] == 0
    assert report["fingerprint_generation_allowed"] is False
    assert all(report[key] is value for key, value in SAFETY_FLAGS.items())


def test_default_mode_does_not_write(tmp_path: Path) -> None:
    report = run_inventory(tmp_path)
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()


def test_write_report_is_limited_to_data_reports(tmp_path: Path) -> None:
    report = run_inventory(tmp_path, write_report=True)
    assert report["write_performed"] is True
    assert (tmp_path / report["output_paths"]["json"]).is_file()
    assert (tmp_path / report["output_paths"]["markdown"]).is_file()


def test_unsafe_write_paths_are_blocked(tmp_path: Path) -> None:
    report = run_inventory(
        tmp_path,
        write_report=True,
        output_json="outside.json",
        output_markdown="outside.md",
    )
    assert report["reason"] == "unsafe_report_output_path"
    assert report["write_performed"] is False


def test_master_and_data_trades_remain_hash_identical(tmp_path: Path) -> None:
    master, profile, evidence = setup_project(tmp_path)
    before = {path.name: sha256(path) for path in master.parent.iterdir() if path.is_file()}
    build_trader_master_authoritative_evidence_inventory_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        evidence_roots=[evidence],
        generated_at_utc=FIXED_TIME,
    )
    after = {path.name: sha256(path) for path in master.parent.iterdir() if path.is_file()}
    assert before == after


def test_fingerprint_spec_is_not_mutated(tmp_path: Path) -> None:
    fingerprint_spec = ROOT / "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
    before = sha256(fingerprint_spec)
    run_inventory(tmp_path)
    assert sha256(fingerprint_spec) == before


def test_output_is_deterministic_except_generated_time(tmp_path: Path) -> None:
    report_one = run_inventory(tmp_path / "one")
    report_two = run_inventory(tmp_path / "two")
    for report in (report_one, report_two):
        report.pop("generated_at_utc")
        report["trader_master_path"] = "normalized"
        report["source_profile_path"] = "normalized"
    assert report_one == report_two


def test_cli_runs_without_pythonpath(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["reason"] == "account_scope_hash_missing"


def test_no_legacy_writer_or_operational_dependency_is_imported() -> None:
    source = (
        ROOT
        / "smartcrypto/data/trader_master_fingerprint_v2/authoritative_evidence_inventory.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("ccxt", "freqtrade", "subprocess", "run_ocr", "write_master"):
        assert forbidden not in source


def _field(report: dict[str, Any], cohort: str, field: str) -> dict[str, Any]:
    return next(
        item
        for item in report["evidence_by_cohort_and_field"]
        if item["source_cohort"] == cohort and item["canonical_field"] == field
    )
