from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from openpyxl import Workbook

from smartcrypto.data.bitradex_ocr_legacy_authorized_append.contract import (
    AUTHORIZATION_PHRASE,
    IMPORTED_AT_UTC,
    TransitionContractError,
    file_sha256,
    load_transition_contract,
)
from smartcrypto.data.bitradex_ocr_legacy_authorized_append.executor import (
    apply_authorized_append,
)
from smartcrypto.data.bitradex_ocr_legacy_authorized_append.planner import (
    build_authorized_append_plan,
    materialize_candidate_imported_at,
    semantic_rows_sha256,
)
from smartcrypto.data.bitradex_ocr_legacy_authorized_append.transaction import (
    _read_parquet_rows,
    build_verified_candidates,
    create_verified_backups,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    FindingClassification,
    GUARDED_TRANSITION_BASELINE_HIGH_PATHS,
    TrackedFileInventory,
    analyze_python_source,
    audit_legacy_master_consumers,
    load_legacy_master_policy,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_bitradex_ocr_legacy_authorized_append_v1.py"
SOURCE_CONTRACT = ROOT / "config/bitradex_ocr_legacy_contract_v1.json"
TRANSITION_CONFIG = ROOT / "config/bitradex_ocr_legacy_append_transition_v1.json"
POLICY = ROOT / "config/trader_master_legacy_research_only_policy_v1.json"
FINGERPRINT_SHA = "7efee2c2ac682242796ac9954ddea525cd34c4a69ab985cdefcdb4e5fe223147"
COMPATIBILITY_HASHES = {
    "__init__.py": "2ded7ecdf20b257c91c16a6fd495503ca3d522ec6d998fca2e82198d4853078d",
    "contract.py": "ae61ffe97595273dd26f28865f04ace81f86a454116a4e6c0fc396d2a8fc2685",
    "compatibility_audit.py": "49c488f2f3eebf09a65e878e303f4f830a5f48dcaccb206779f1bbef458667d7",
}
PACKAGE_FILES = (
    "__init__.py",
    "contract.py",
    "planner.py",
    "transaction.py",
    "executor.py",
)


def valid_preview() -> dict[str, Any]:
    return {
        "status": "ok",
        "package_token": "20260714_151816",
        "pipeline_stage": "PREVIEW_V4_RECONCILED_IMPORT_NOT_CONFIRMED",
        "incoming_rows": 504,
        "master_reconciled_rows": 3058,
        "sidecar_auto_matched_rows": 3057,
        "sidecar_residual_equivalence_rows": 1,
        "sidecars_reconciled": True,
        "identity_contract_valid": True,
        "incoming_required_missing_total": 0,
        "incoming_internal_duplicate_excess_count": 0,
        "incoming_fallback_collision_count": 0,
        "exists_strict_count": 0,
        "exists_fallback_count": 0,
        "exists_strict_duplicate_group_count": 0,
        "novel_to_reconciled_master_count": 504,
        "ambiguous_master_match_count": 0,
        "invalid_identity_count": 0,
        "source_image_conflict_count": 0,
        "guarded_apply_allowed": False,
        "import_executed": False,
        "master_preserved": True,
        "stash_preserved": True,
        "worktree_clean": True,
    }


def make_row(index: int, *, candidate: bool) -> dict[str, str]:
    prefix = "candidate" if candidate else "master"
    return {
        "moeda": "BTCUSDT" if index % 2 else "ETHUSDT",
        "fechar_side": "long" if index % 3 else "short",
        "leverage": "2",
        "order_id": f"synthetic-{index}" if candidate else f"master-{index}",
        "pnl_fechado": str(index / 100),
        "taxa_lucros_perdas_fechados_pct": str(index / 10000),
        "preco_abertura": str(60000 + index),
        "preco_fechamento": str(60001 + index),
        "volume_posicao": "0.1",
        "volume_fechado": "0.1",
        "horario_abertura": f"2026-06-{(index % 28) + 1:02d}T00:00:00Z",
        "horario_fechamento": f"2026-06-{(index % 28) + 1:02d}T00:05:00Z",
        "taxa_1": "-0.01",
        "preco_transacao": str(60001 + index),
        "volume_transacao": "0.1",
        "direcao_liquidez": "TAKER",
        "taxa_2": "-0.01",
        "horario_transacao": f"2026-06-{(index % 28) + 1:02d}T00:05:00Z",
        "source_file": "fixture_candidate_package" if candidate else f"master-{index}.jpg",
        "imported_at": "" if candidate else "2026-07-14T12:00:00Z",
        "_dedup_key": f"{prefix}-dedup-{index}",
        "_relaxed_dedup_key": f"{prefix}-relaxed-{index}",
        "exchange_source": "BITRADEX",
        "market_data_source": "BINANCE_FUTURES",
        "ocr_source": "fixture_ocr",
    }


def write_xlsx(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("trades_master_candidate")
    sheet.append(columns)
    for row in rows:
        sheet.append([row[column] for column in columns])
    workbook.save(path)


def copy_authorized_sources(root: Path) -> dict[str, str]:
    destination = root / "smartcrypto/data/bitradex_ocr_legacy_authorized_append"
    destination.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for name in PACKAGE_FILES:
        relative = f"smartcrypto/data/bitradex_ocr_legacy_authorized_append/{name}"
        shutil.copy2(ROOT / relative, root / relative)
        hashes[relative] = file_sha256(root / relative)
    cli_relative = "scripts/build_bitradex_ocr_legacy_authorized_append_v1.py"
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / cli_relative, root / cli_relative)
    hashes[cli_relative] = file_sha256(root / cli_relative)
    return hashes


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("authorized-append-template")
    package = root / "data/staging/bitradex_ocr/package_20260714_151816"
    trades = root / "data/trades"
    config = root / "config"
    package.mkdir(parents=True)
    trades.mkdir(parents=True)
    config.mkdir(parents=True)
    columns = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))[
        "historical_master_schema"
    ]["columns"]
    master_rows = [make_row(index, candidate=False) for index in range(3058)]
    candidate_rows = [make_row(index, candidate=True) for index in range(504)]
    pd.DataFrame(master_rows, columns=columns).to_parquet(trades / "trades_master.parquet", index=False)
    write_xlsx(trades / "trades_master.xlsx", master_rows, columns)
    (package / "BITRADEX_OCR_IMPORT_PREVIEW_V4_SUMMARY.json").write_text(
        json.dumps(valid_preview()), encoding="utf-8"
    )
    imported_at_source = package / "ORDERID_SYNTHETIC_V5_SUMMARY.json"
    imported_at_source.write_text(
        json.dumps({"finalized_at_utc": IMPORTED_AT_UTC}),
        encoding="utf-8",
    )
    with (package / "BITRADEX_OCR_IMPORT_PREVIEW_V4.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=[*columns, "preview_v4_classification"])
        writer.writeheader()
        for row in candidate_rows:
            writer.writerow({**row, "preview_v4_classification": "NOVEL_TO_RECONCILED_MASTER"})
    source_contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    source_contract["expected_master_hashes"] = {
        "xlsx_sha256": file_sha256(trades / "trades_master.xlsx"),
        "parquet_sha256": file_sha256(trades / "trades_master.parquet"),
    }
    (config / "bitradex_ocr_legacy_contract_v1.json").write_text(
        json.dumps(source_contract), encoding="utf-8"
    )
    hashes = copy_authorized_sources(root)
    transition = json.loads(TRANSITION_CONFIG.read_text(encoding="utf-8"))
    transition["pre_state"]["master_xlsx_sha256"] = file_sha256(
        trades / "trades_master.xlsx"
    )
    transition["pre_state"]["master_parquet_sha256"] = file_sha256(
        trades / "trades_master.parquet"
    )
    transition["imported_at_policy"]["source_file_sha256"] = file_sha256(
        imported_at_source
    )
    transition["authorized_source_sha256"] = hashes
    (config / "bitradex_ocr_legacy_append_transition_v1.json").write_text(
        json.dumps(transition), encoding="utf-8"
    )
    return root


def project(tmp_path: Path, template: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(template, root)
    return root


def transition_path(root: Path) -> Path:
    return root / "config/bitradex_ocr_legacy_append_transition_v1.json"


def mutate_json(path: Path, keys: tuple[str, ...], value: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    current = payload
    for key in keys[:-1]:
        current = current[key]
    current[keys[-1]] = value
    path.write_text(json.dumps(payload), encoding="utf-8")


def plan(root: Path) -> dict[str, Any]:
    return build_authorized_append_plan(project_root=root)


def csv_path(root: Path) -> Path:
    return root / "data/staging/bitradex_ocr/package_20260714_151816/BITRADEX_OCR_IMPORT_PREVIEW_V4.csv"


def imported_at_source_path(root: Path) -> Path:
    return (
        root
        / "data/staging/bitradex_ocr/package_20260714_151816"
        / "ORDERID_SYNTHETIC_V5_SUMMARY.json"
    )


def write_imported_at_source(
    root: Path,
    payload: Any,
    *,
    update_hash: bool = True,
    policy_value: str | None = None,
) -> None:
    source = imported_at_source_path(root)
    source.write_text(json.dumps(payload), encoding="utf-8")
    if update_hash:
        transition = json.loads(transition_path(root).read_text(encoding="utf-8"))
        transition["imported_at_policy"]["source_file_sha256"] = file_sha256(source)
        if policy_value is not None:
            transition["imported_at_policy"]["value_utc"] = policy_value
        transition_path(root).write_text(json.dumps(transition), encoding="utf-8")


def modify_csv(root: Path, callback: Any) -> None:
    path = csv_path(root)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    fieldnames, rows = callback(fieldnames, rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def master_hashes(root: Path) -> tuple[str, str]:
    return (
        file_sha256(root / "data/trades/trades_master.xlsx"),
        file_sha256(root / "data/trades/trades_master.parquet"),
    )


def test_transition_contract_valid(template: Path) -> None:
    contract = load_transition_contract(transition_path(template))
    assert contract.transition_state == "planned_not_executed"
    assert contract.append_state.expected_post_row_count == 3562
    assert contract.imported_at_policy.value_utc == IMPORTED_AT_UTC


def test_imported_at_source_missing_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    imported_at_source_path(root).unlink()
    report = plan(root)
    assert report["status"] == "blocked"
    assert "imported_at_source_missing" in report["validation_errors"]


def test_imported_at_source_hash_divergence_blocks(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    write_imported_at_source(
        root,
        {"finalized_at_utc": IMPORTED_AT_UTC, "drift": True},
        update_hash=False,
    )
    assert "imported_at_source_hash_mismatch" in plan(root)["validation_errors"]


def test_imported_at_source_field_missing_blocks(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    write_imported_at_source(root, {"status": "ok"})
    assert "imported_at_source_field_missing" in plan(root)["validation_errors"]


def test_imported_at_without_timezone_blocks(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    value = "2026-07-14T19:49:13.500939"
    write_imported_at_source(
        root,
        {"finalized_at_utc": value},
        policy_value=value,
    )
    assert "imported_at_source_timezone_must_be_utc" in plan(root)["validation_errors"]


def test_imported_at_non_utc_offset_blocks(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    value = "2026-07-14T16:49:13.500939-03:00"
    write_imported_at_source(
        root,
        {"finalized_at_utc": value},
        policy_value=value,
    )
    assert "imported_at_source_timezone_must_be_utc" in plan(root)["validation_errors"]


def test_imported_at_value_different_from_pin_blocks(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    value = "2026-07-14T19:49:14.500939+00:00"
    write_imported_at_source(
        root,
        {"finalized_at_utc": value},
        policy_value=value,
    )
    assert "transition_imported_at_value_invalid" in plan(root)["validation_errors"]


def test_imported_at_source_symlink_is_rejected(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    source = imported_at_source_path(root)
    target = source.with_name("authoritative-target.json")
    source.replace(target)
    source.symlink_to(target)
    assert "imported_at_source_symlink_rejected" in plan(root)["validation_errors"]


def test_transition_schema_invalid_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    mutate_json(transition_path(root), ("schema_version",), "wrong")
    with pytest.raises(TransitionContractError, match="transition_schema_version_invalid"):
        load_transition_contract(transition_path(root))


def test_source_code_hash_divergence_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    source = root / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/planner.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    report = plan(root)
    assert report["status"] == "blocked"
    assert any("authorized_transition_source_hash_mismatch" in item for item in report["validation_errors"])


def test_cli_default_is_plan_no_write(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(root), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["decision"] == "REQUIRES_EXPLICIT_APPLY_CONFIRMATION"
    assert report["write_performed"] is False


def test_plan_creates_no_directories(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    before = {path.relative_to(root) for path in root.rglob("*")}
    assert plan(root)["status"] == "ok"
    assert {path.relative_to(root) for path in root.rglob("*")} == before


def test_plan_hash_is_deterministic(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    assert plan(root)["plan_sha256"] == plan(root)["plan_sha256"]


def test_imported_at_source_hash_is_in_canonical_plan(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    report = plan(root)
    assert report["source_hashes"]["imported_at_source_sha256"] == file_sha256(
        imported_at_source_path(root)
    )
    assert (
        report["plan"]["imported_at_policy"]["source_file_sha256"]
        == report["source_hashes"]["imported_at_source_sha256"]
    )


def test_imported_at_materialization_is_uniform_for_all_candidates(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    report = plan(root)
    assert report["materialization_ready"] is True
    assert report["materialization_blockers"] == []
    assert report["imported_at_source_verified"] is True
    assert report["imported_at_missing_count_before_materialization"] == 504
    assert report["imported_at_missing_count_after_materialization"] == 0
    assert report["imported_at_unique_count_after_materialization"] == 1
    assert report["imported_at_utc"] == IMPORTED_AT_UTC
    assert report["apply_allowed"] is False


def test_source_json_change_blocks_plan(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    first = plan(root)["plan_sha256"]
    write_imported_at_source(
        root,
        {"finalized_at_utc": IMPORTED_AT_UTC, "changed": True},
        update_hash=False,
    )
    report = plan(root)
    assert first is not None
    assert report["status"] == "blocked"
    assert "imported_at_source_hash_mismatch" in report["validation_errors"]


def test_filesystem_mtime_is_not_used_for_imported_at(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    source = imported_at_source_path(root)
    first = plan(root)
    os.utime(source, (1, 1))
    second = plan(root)
    assert second["plan_sha256"] == first["plan_sha256"]
    assert second["imported_at_utc"] == IMPORTED_AT_UTC


def test_batch_token_is_not_used_for_imported_at(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    report = plan(root)
    assert report["imported_at_utc"] != report["plan"]["batch_id"]
    assert report["plan"]["imported_at_policy"]["batch_token_timestamp_allowed"] is False


def test_runtime_clock_is_not_used_for_imported_at() -> None:
    planner_source = (
        ROOT / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/planner.py"
    ).read_text(encoding="utf-8")
    contract_source = (
        ROOT / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/contract.py"
    ).read_text(encoding="utf-8")
    assert "datetime.now(" not in planner_source
    assert "datetime.now(" not in contract_source
    assert "getmtime(" not in planner_source
    assert "st_mtime" not in planner_source


def test_external_timestamp_does_not_change_plan_hash(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    first = plan(root)["plan_sha256"]
    (root / "unrelated.txt").write_text("later", encoding="utf-8")
    assert plan(root)["plan_sha256"] == first


def test_preview_change_changes_plan_hash(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    first = plan(root)["plan_sha256"]
    modify_csv(root, lambda fields, rows: (fields, [{**rows[0], "pnl_fechado": "99"}, *rows[1:]]))
    assert plan(root)["plan_sha256"] != first


def test_master_change_changes_plan_hash(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    first = plan(root)["plan_sha256"]
    path = root / "data/trades/trades_master.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "pnl_fechado"] = "999"
    frame.to_parquet(path, index=False)
    assert plan(root)["plan_sha256"] != first


def test_header_absent_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    csv_path(root).write_text("", encoding="utf-8")
    assert plan(root)["status"] == "blocked"


def test_historical_column_absent_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)

    def remove(fields: list[str], rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
        return [field for field in fields if field != "taxa_2"], rows

    modify_csv(root, remove)
    assert any("preview_csv_missing_columns:taxa_2" in item for item in plan(root)["validation_errors"])


@pytest.mark.parametrize("count", [503, 505])
def test_candidate_count_mismatch_blocks(tmp_path: Path, template: Path, count: int) -> None:
    root = project(tmp_path, template)

    def resize(fields: list[str], rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
        return fields, rows[:count] if count < len(rows) else [*rows, dict(rows[-1])]

    modify_csv(root, resize)
    assert plan(root)["status"] == "blocked"


@pytest.mark.parametrize("field", ["_dedup_key", "_relaxed_dedup_key"])
def test_internal_collision_blocks(tmp_path: Path, template: Path, field: str) -> None:
    root = project(tmp_path, template)

    def collide(fields: list[str], rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
        rows[1][field] = rows[0][field]
        return fields, rows

    modify_csv(root, collide)
    assert any(f"collision_guard_internal_duplicate:{field}" == item for item in plan(root)["validation_errors"])


def test_master_collision_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)

    def collide(fields: list[str], rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
        rows[0]["_dedup_key"] = "master-dedup-0"
        return fields, rows

    modify_csv(root, collide)
    assert "collision_guard_master_overlap:_dedup_key" in plan(root)["validation_errors"]


def test_synthetic_id_never_becomes_v2_identity(tmp_path: Path, template: Path) -> None:
    report = plan(project(tmp_path, template))
    assert report["synthetic_order_id_authoritative"] is False
    assert report["synthetic_order_id_used_as_v2_identity"] is False


def test_funding_is_never_zero(tmp_path: Path, template: Path) -> None:
    assert plan(project(tmp_path, template))["plan"]["funding_fee_value"] is None


def test_funding_is_never_residual(tmp_path: Path, template: Path) -> None:
    assert plan(project(tmp_path, template))["plan"]["funding_derived_as_residual"] is False


def test_apply_without_phrase_blocks_before_lock(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    report = apply_authorized_append(
        project_root=root, expected_plan_sha256="a" * 64, authorization_phrase=None
    )
    assert report["reason"] == "authorization_phrase_invalid"
    assert not (root / "data/locks").exists()


def test_apply_without_plan_hash_blocks_before_lock(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    report = apply_authorized_append(
        project_root=root, expected_plan_sha256=None, authorization_phrase=AUTHORIZATION_PHRASE
    )
    assert report["reason"] == "expected_plan_sha256_invalid"
    assert not (root / "data/locks").exists()


def test_plan_hash_mismatch_blocks_before_backup(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    report = apply_authorized_append(
        project_root=root,
        expected_plan_sha256="a" * 64,
        authorization_phrase=AUTHORIZATION_PHRASE,
    )
    assert report["reason"] == "plan_sha256_mismatch"
    assert not (root / "data/backups").exists()


def test_existing_lock_blocks(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    plan_report = plan(root)
    lock = root / "data/locks/bitradex_ocr_legacy_append_v1.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("existing", encoding="utf-8")
    report = apply_authorized_append(
        project_root=root,
        expected_plan_sha256=plan_report["plan_sha256"],
        authorization_phrase=AUTHORIZATION_PHRASE,
    )
    assert report["reason"] == "transition_lock_exists"
    assert lock.read_text(encoding="utf-8") == "existing"


def test_backups_are_byte_exact(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    contract = load_transition_contract(transition_path(root))
    backup = create_verified_backups(root=root, contract=contract, run_id="fixture")
    assert backup.xlsx_sha256 == contract.pre_state.master_xlsx_sha256
    assert backup.parquet_sha256 == contract.pre_state.master_parquet_sha256


def candidate_evidence(root: Path) -> tuple[Any, list[dict[str, str]], list[str]]:
    contract = load_transition_contract(transition_path(root))
    columns = json.loads((root / contract.source_contract).read_text(encoding="utf-8"))[
        "historical_master_schema"
    ]["columns"]
    with csv_path(root).open(encoding="utf-8", newline="") as handle:
        rows = [{column: row[column] for column in columns} for row in csv.DictReader(handle)]
    rows, evidence = materialize_candidate_imported_at(
        rows,
        columns,
        contract.imported_at_policy.value_utc,
    )
    assert evidence["missing_count_after_materialization"] == 0
    assert evidence["unique_count_after_materialization"] == 1
    return build_verified_candidates(root=root, contract=contract, candidates=rows, columns=columns), rows, columns


def test_parquet_candidate_has_3562_rows(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    evidence, _, _ = candidate_evidence(root)
    assert evidence.parquet_row_count == 3562
    evidence.parquet_path.unlink()
    evidence.xlsx_path.unlink()


def test_xlsx_candidate_has_3562_rows(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    evidence, _, _ = candidate_evidence(root)
    assert evidence.xlsx_row_count == 3562
    evidence.parquet_path.unlink()
    evidence.xlsx_path.unlink()


def test_candidate_prefix_is_preserved(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    evidence, _, columns = candidate_evidence(root)
    original = _read_parquet_rows(root / "data/trades/trades_master.parquet", columns)
    candidate = _read_parquet_rows(evidence.parquet_path, columns)
    assert semantic_rows_sha256(candidate[:3058], columns) == semantic_rows_sha256(original, columns)
    evidence.parquet_path.unlink()
    evidence.xlsx_path.unlink()


def test_candidate_append_order_is_preserved(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    evidence, rows, columns = candidate_evidence(root)
    candidate = _read_parquet_rows(evidence.parquet_path, columns)
    assert semantic_rows_sha256(candidate[-504:], columns) == semantic_rows_sha256(rows, columns)
    evidence.parquet_path.unlink()
    evidence.xlsx_path.unlink()


def test_candidate_cross_format_semantics_match(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    evidence, _, _ = candidate_evidence(root)
    assert evidence.xlsx_semantic_sha256 == evidence.parquet_semantic_sha256
    evidence.parquet_path.unlink()
    evidence.xlsx_path.unlink()


def fail_on(stage: str) -> Any:
    def hook(observed: str) -> None:
        if observed == stage:
            raise RuntimeError(f"fixture_fault:{stage}")

    return hook


def execute_with_fault(root: Path, stage: str) -> dict[str, Any]:
    plan_report = plan(root)
    return apply_authorized_append(
        project_root=root,
        expected_plan_sha256=plan_report["plan_sha256"],
        authorization_phrase=AUTHORIZATION_PHRASE,
        fault_hook=fail_on(stage),
    )


def test_failure_before_first_replace_preserves_masters(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    before = master_hashes(root)
    report = execute_with_fault(root, "candidates_verified")
    assert report["rollback_attempted"] is False
    assert master_hashes(root) == before


def test_failure_between_replaces_runs_rollback(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    report = execute_with_fault(root, "parquet_replaced")
    assert report["rollback_attempted"] is True
    assert report["rollback_succeeded"] is True


def test_post_write_failure_runs_rollback(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    report = execute_with_fault(root, "post_apply_verified")
    assert report["rollback_attempted"] is True
    assert report["rollback_succeeded"] is True


def test_rollback_restores_original_hashes(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    before = master_hashes(root)
    execute_with_fault(root, "xlsx_replaced")
    assert master_hashes(root) == before


def test_success_writes_only_allowed_roots(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    before = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    plan_report = plan(root)
    report = apply_authorized_append(
        project_root=root,
        expected_plan_sha256=plan_report["plan_sha256"],
        authorization_phrase=AUTHORIZATION_PHRASE,
    )
    after = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    assert report["status"] == "ok"
    assert report["transaction_committed"] is True
    assert report["source_hashes"] == plan_report["source_hashes"]
    assert report["before_hashes"] == {
        "xlsx": plan_report["source_hashes"]["master_xlsx_sha256"],
        "parquet": plan_report["source_hashes"]["master_parquet_sha256"],
    }
    assert set(report["backup_hashes"]) == {"xlsx", "parquet"}
    assert set(report["backup_sizes"]) == {"xlsx", "parquet"}
    assert report["candidate_row_counts"] == {"xlsx": 3562, "parquet": 3562}
    assert report["candidate_hashes"]["xlsx_semantic_sha256"] == report["candidate_hashes"][
        "parquet_semantic_sha256"
    ]
    assert report["post_apply_verification"]["cross_format_semantic_equality"] is True
    assert report["post_apply_verification"]["tail_semantics_equal"] is True
    assert report["post_apply_verification"]["prefix_semantics_equal"] is True
    assert report["schema_column_count"] == 25
    additions = after - before
    assert all(str(path).replace("\\", "/").startswith(("data/backups/", "data/reports/")) for path in additions)


def test_reapply_is_blocked(tmp_path: Path, template: Path) -> None:
    root = project(tmp_path, template)
    first_plan = plan(root)
    first = apply_authorized_append(
        project_root=root,
        expected_plan_sha256=first_plan["plan_sha256"],
        authorization_phrase=AUTHORIZATION_PHRASE,
    )
    assert first["status"] == "ok"
    second = apply_authorized_append(
        project_root=root,
        expected_plan_sha256=first_plan["plan_sha256"],
        authorization_phrase=AUTHORIZATION_PHRASE,
    )
    assert second["status"] == "blocked"


def test_executor_does_not_import_retired_importer() -> None:
    source = (ROOT / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/executor.py").read_text(encoding="utf-8")
    assert "trades_importer" not in source


def test_executor_has_no_network_exchange_risk_or_order_calls() -> None:
    source = (ROOT / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/executor.py").read_text(encoding="utf-8").casefold()
    for token in ("ccxt", "requests", "urllib", "socket", "riskmanager", "send_order", "subprocess"):
        assert token not in source


def test_retired_official_apply_script_remains_blocked() -> None:
    source = (ROOT / "scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py").read_text(encoding="utf-8")
    assert '"reason": "legacy_official_dataset_apply_disabled"' in source
    assert '"decision": "LEGACY_DATASET_APPLY_FORBIDDEN"' in source
    assert '"write_performed": False' in source


def test_retired_sidecar_sync_remains_writer_callsite() -> None:
    source = (ROOT / "scripts/sync_ocr_master_v11_phase5_sidecars.py").read_text(encoding="utf-8")
    assert "legacy_master_sidecar_write_forbidden" in source
    assert '"would_write": False' in source
    assert '"write_performed": False' in source


def test_boundary_allows_only_hash_pinned_transition_paths(template: Path) -> None:
    policy = load_legacy_master_policy(project_root=ROOT, policy_path=POLICY)
    paths = tuple(
        sorted(
            str(path.relative_to(template)).replace("\\", "/")
            for path in template.rglob("*")
            if path.is_file()
        )
    )
    findings, metadata = audit_legacy_master_consumers(
        project_root=template,
        policy=policy,
        tracked_inventory=TrackedFileInventory(paths=paths, discovery_mode="fixture", complete=True),
    )
    assert metadata["guarded_transition_source_hashes_valid"] is True
    assert sum(
        item.classification == FindingClassification.AUTHORIZED_GUARDED_TRANSITION_IMPLEMENTATION
        for item in findings
    ) == 6


def test_boundary_high_baseline_is_exactly_pinned() -> None:
    policy = load_legacy_master_policy(
        project_root=ROOT,
        policy_path=POLICY,
    )

    findings, metadata = audit_legacy_master_consumers(
        project_root=ROOT,
        policy=policy,
    )

    high_finding_paths = sorted(
        {
            finding.relative_path
            for finding in findings
            if finding.severity.value == "high"
        }
    )

    critical_finding_paths = sorted(
        {
            finding.relative_path
            for finding in findings
            if finding.severity.value == "critical"
        }
    )

    authorized_transition_paths = sorted(
        {
            finding.relative_path
            for finding in findings
            if finding.classification
            == FindingClassification.AUTHORIZED_GUARDED_TRANSITION_IMPLEMENTATION
        }
    )

    expected_high_paths = list(
        GUARDED_TRANSITION_BASELINE_HIGH_PATHS
    )

    new_high_finding_paths = sorted(
        set(high_finding_paths)
        - set(GUARDED_TRANSITION_BASELINE_HIGH_PATHS)
    )

    assert metadata["tracked_file_discovery_complete"] is True
    assert metadata["guarded_transition_present"] is True
    assert metadata["guarded_transition_source_hashes_valid"] is True
    assert metadata["guarded_transition_default_no_write"] is True
    assert metadata["guarded_transition_apply_executed"] is False

    assert critical_finding_paths == []
    assert high_finding_paths == expected_high_paths
    assert high_finding_paths == [
        "config/bitradex_ocr_legacy_contract_v1.json"
    ]
    assert new_high_finding_paths == []
    assert len(authorized_transition_paths) == 6


def test_new_high_finding_is_not_absorbed_by_baseline(
    tmp_path: Path, template: Path
) -> None:
    root = project(tmp_path, template)
    rogue = root / "config/new_legacy_master_reference.json"
    rogue.write_text(
        json.dumps({"master": "data/trades/trades_master.parquet"}),
        encoding="utf-8",
    )
    policy = load_legacy_master_policy(project_root=ROOT, policy_path=POLICY)
    paths = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )
    findings, _ = audit_legacy_master_consumers(
        project_root=root,
        policy=policy,
        tracked_inventory=TrackedFileInventory(
            paths=paths,
            discovery_mode="fixture",
            complete=True,
        ),
    )
    new_high_paths = {
        item.relative_path
        for item in findings
        if item.severity.value == "high"
    } - set(GUARDED_TRANSITION_BASELINE_HIGH_PATHS)
    assert "config/new_legacy_master_reference.json" in new_high_paths


def test_writer_outside_allowlist_remains_blocking() -> None:
    findings = analyze_python_source(
        "smartcrypto/data/rogue_writer.py",
        "from pathlib import Path\nPath('data/trades/trades_master.parquet').write_bytes(b'x')\n",
    )
    assert any(item.severity.value in {"high", "critical"} for item in findings)


def test_fingerprint_spec_remains_unchanged() -> None:
    assert file_sha256(ROOT / "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py") == FINGERPRINT_SHA


def test_legacy_compatibility_package_remains_unchanged() -> None:
    base = ROOT / "smartcrypto/data/bitradex_ocr_legacy_compatibility"
    assert {name: file_sha256(base / name) for name in COMPATIBILITY_HASHES} == COMPATIBILITY_HASHES