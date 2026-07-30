from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter import (
    FreqtradePaperAdapterBundle,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_lineage_profile import (
    LEGACY_OBSERVATION_KEY_VERSION,
    SAFETY_FLAGS,
    build_field_lineage_profile,
    build_legacy_overlap_profile,
    build_source_cohort_profiles,
    build_trader_master_legacy_lineage_profile_report,
    classify_financial_lineage,
    legacy_observation_key_for,
    profile_legacy_master_row,
)
from smartcrypto.data.trader_master_fingerprint_v2 import (
    master_adapter as master_adapter_module,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "profile_trader_master_legacy_lineage_v2.py"
PROFILE = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"
ACCOUNT_HASH = "c" * 64


def complete_trade(order_id: str = "paper-1", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "venue": "binance",
        "market_type": "usdt-m_futures",
        "contract_type": "linear_perpetual",
        "settlement_currency": "usdt",
        "quantity_unit": "base_asset",
        "contract_size": "1.00000000",
        "account_scope_hash": ACCOUNT_HASH,
        "order_id_namespace": "paper:test:v1",
        "source_trade_id": None,
        "order_id": order_id,
        "source": "paper_fixture",
        "symbol": "btcusdt",
        "side": "long",
        "open_time": "2026-01-01T00:00:00.000000Z",
        "close_time": "2026-01-01T00:01:00.000000Z",
        "entry_price": "100.00000000",
        "exit_price": "110.00000000",
        "quantity": "1.00000000",
        "gross_pnl": "10.00000000",
        "trading_fee": "1.00000000",
        "funding_fee": "0.00000000",
        "net_pnl": "9.00000000",
        "epsilon_abs_fonte": "0.00000001",
    }
    row.update(overrides)
    return row


def observation_trade(**overrides: Any) -> dict[str, Any]:
    row = {field: complete_trade()[field] for field in (
        "symbol",
        "side",
        "open_time",
        "close_time",
        "entry_price",
        "exit_price",
        "quantity",
        "net_pnl",
    )}
    row.update(overrides)
    return row


def write_master(root: Path, rows: list[dict[str, Any]]) -> Path:
    path = root / "data" / "trades" / "trades_master.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_bundle(rows: list[dict[str, Any]] | None = None) -> FreqtradePaperAdapterBundle:
    accepted = rows or [complete_trade()]
    return FreqtradePaperAdapterBundle(
        report={
            "status": "blocked",
            "reason": "rows_quarantined_after_authoritative_reconciliation",
            "raw_row_count": len(accepted),
            "accepted_row_count": len(accepted),
            "quarantined_row_count": 0,
            "source_status": "ok",
            "snapshot_source_hashes_preserved": True,
            "structural_errors": [],
        },
        accepted_canonical_records=tuple(accepted),
        quarantined_row_summaries=(),
        batch_identity={"accepted_row_count": len(accepted)},
    )


def run_profile(
    root: Path,
    master_rows: list[dict[str, Any]],
    *,
    paper_rows: list[dict[str, Any]] | None = None,
    write_report: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    master = write_master(root, master_rows)

    def adapter_builder(**_: Any) -> FreqtradePaperAdapterBundle:
        return fake_bundle(paper_rows)

    return build_trader_master_legacy_lineage_profile_report(
        project_root=root,
        trader_master_path=master,
        source_profile_path=PROFILE,
        account_scope_hash=ACCOUNT_HASH,
        write_report=write_report,
        generated_at_utc="2026-07-13T00:00:00+00:00",
        adapter_builder=adapter_builder,
        **kwargs,
    )


def test_reuses_temporary_copy_reader(tmp_path: Path) -> None:
    path = write_master(tmp_path, [complete_trade()])
    bundle = read_trader_master_readonly(project_root=tmp_path, trader_master_path=path)
    assert bundle.report["trader_master_temp_copy_used"] is True
    assert len(bundle.source_rows) == 1


def test_master_hashes_remain_equal(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()])
    assert report["trader_master_sha256_before"] == report["trader_master_sha256_after"]
    assert report["trader_master_hash_preserved"] is True


def test_master_symlink_is_blocked(tmp_path: Path) -> None:
    target = write_master(tmp_path, [complete_trade()])
    link = tmp_path / "data" / "trades" / "linked.parquet"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    report = build_trader_master_legacy_lineage_profile_report(
        project_root=tmp_path,
        trader_master_path=link,
        source_profile_path=PROFILE,
        account_scope_hash=ACCOUNT_HASH,
    )
    assert report["reason"] == "trader_master_symlink_forbidden"


def test_master_outside_project_is_blocked(tmp_path: Path) -> None:
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside"
    outside = write_master(outside_root, [complete_trade()])
    report = build_trader_master_legacy_lineage_profile_report(
        project_root=tmp_path,
        trader_master_path=outside,
        source_profile_path=PROFILE,
        account_scope_hash=ACCOUNT_HASH,
    )
    assert report["reason"] == "trader_master_outside_project_root"


def test_no_xlsx_fallback_occurs() -> None:
    source = (ROOT / "smartcrypto/data/trader_master_fingerprint_v2/legacy_lineage_profile.py").read_text()
    assert "read_excel" not in source
    assert "trades_master.xlsx" not in source


def test_duplicate_schema_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_master(tmp_path, [complete_trade()])
    original_hash = file_hash(path)
    calls: dict[str, Any] = {}

    class FakeTable:
        def to_pandas(self, *, use_threads: bool) -> pd.DataFrame:
            calls["to_pandas_use_threads"] = use_threads
            return pd.DataFrame([["a", "b"]], columns=["symbol", "symbol"])

    class FakeParquetFile:
        def __init__(self, copied_path: Path) -> None:
            calls["copied_path"] = Path(copied_path)

        def read(self, *, use_threads: bool) -> FakeTable:
            calls["read_use_threads"] = use_threads
            return FakeTable()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(
        master_adapter_module.pq,
        "ParquetFile",
        FakeParquetFile,
    )

    bundle = read_trader_master_readonly(project_root=tmp_path, trader_master_path=path)

    assert bundle.report["reason"] == "duplicate_trader_master_columns"
    copied_path = calls["copied_path"]
    assert isinstance(copied_path, Path)
    assert copied_path.name == "trades_master.parquet"
    assert copied_path != path
    assert calls["read_use_threads"] is False
    assert calls["to_pandas_use_threads"] is False
    assert calls["closed"] is True
    assert bundle.report["write_performed"] is False
    assert bundle.report["writes_trader_master"] is False
    assert bundle.report["writes_runtime"] is False
    assert bundle.report["writes_sqlite"] is False
    assert bundle.report["writes_parquet"] is False
    assert file_hash(path) == original_hash


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("venue", "external_authoritative_evidence_required"),
        ("account_scope_hash", "external_authoritative_evidence_required"),
        ("contract_size", "external_authoritative_evidence_required"),
        ("trading_fee", "mathematically_underdetermined"),
        ("funding_fee", "mathematically_underdetermined"),
    ],
)
def test_missing_values_are_not_filled(field: str, expected: str) -> None:
    profile = {item["canonical_field"]: item for item in build_field_lineage_profile([{}])}
    assert profile[field]["lineage_classification"] == expected
    assert profile[field]["sample_sanitized_values"] == []


def test_venue_is_not_inferred_from_filename() -> None:
    row = {"source_file": "binance_futures_usdt.csv"}
    profile = {item["canonical_field"]: item for item in build_field_lineage_profile([row])}
    assert profile["venue"]["source_column"] is None
    assert profile["venue"]["lineage_classification"] == "external_authoritative_evidence_required"


def test_account_scope_is_not_inferred() -> None:
    row = profile_legacy_master_row(0, {"source_file": "account-a.csv", "order_id": "1"})
    assert "account_scope_hash" in row["external_evidence_required_fields"]


def test_contract_size_has_no_default() -> None:
    row = profile_legacy_master_row(0, observation_trade())
    assert row["financial_evidence"]["contract_size_available"] is False


def test_fee_and_funding_do_not_receive_zero() -> None:
    row = profile_legacy_master_row(0, observation_trade())
    assert row["financial_evidence"]["trading_fee_available"] is False
    assert row["financial_evidence"]["funding_fee_available"] is False


def test_gross_pnl_is_not_derived_from_net_pnl() -> None:
    values = {field: None for field in complete_trade()}
    values["net_pnl"] = "9.00000000"
    financial = classify_financial_lineage(values)
    assert financial["gross_pnl_reconstructable"] is False
    assert financial["financial_classification"] == "net_pnl_only_decomposition_underdetermined"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("side", "buy", "long"),
        ("open_time", "2026-01-01 00:00:00+00:00", "2026-01-01T00:00:00.000000Z"),
        ("entry_price", "10.5", "10.50000000"),
    ],
)
def test_safe_normalizations_are_deterministic(field: str, value: str, expected: str) -> None:
    key_row = observation_trade(**{field: value})
    key = legacy_observation_key_for(key_row)
    assert key["status"] == "valid"
    assert expected in str(key["payload"])


def test_gross_is_reconstructable_only_with_complete_inputs() -> None:
    values = {key: str(value) if value is not None else None for key, value in complete_trade().items()}
    assert classify_financial_lineage(values)["gross_pnl_reconstructable"] is True
    values["contract_size"] = None
    assert classify_financial_lineage(values)["gross_pnl_reconstructable"] is False


def test_missing_fee_and_funding_make_decomposition_underdetermined() -> None:
    values = {key: str(value) if value is not None else None for key, value in complete_trade().items()}
    values["trading_fee"] = None
    values["funding_fee"] = None
    financial = classify_financial_lineage(values)
    assert financial["financial_classification"] == "gross_pnl_reconstructable_but_costs_missing"


def test_directly_complete_row_is_v2_verifiable() -> None:
    row = profile_legacy_master_row(0, complete_trade())
    assert row["final_adaptability_classification"] == "v2_directly_verifiable"
    assert row["fingerprint_generation_allowed"] is True


def test_row_depending_only_on_versioned_contract_is_conditional() -> None:
    trade = complete_trade()
    trade.pop("order_id_namespace")
    row = profile_legacy_master_row(0, trade)
    assert row["final_adaptability_classification"] == (
        "conditionally_adaptable_with_versioned_source_contract"
    )


def test_external_evidence_row_is_not_adaptable() -> None:
    trade = complete_trade()
    trade.pop("account_scope_hash")
    row = profile_legacy_master_row(0, trade)
    assert row["fingerprint_generation_allowed"] is False
    assert row["final_adaptability_classification"] == "blocked_by_native_identity_lineage"


def test_cohorts_are_deterministic() -> None:
    rows = [complete_trade(source_file="a"), complete_trade("paper-2", source_file="b")]
    profiles = [profile_legacy_master_row(index, row) for index, row in enumerate(rows)]
    first = build_source_cohort_profiles(rows, profiles)
    second = build_source_cohort_profiles(list(reversed(rows)), list(reversed(profiles)))
    assert [item["cohort_id"] for item in first] == [item["cohort_id"] for item in second]


def test_null_source_forms_explicit_cohort() -> None:
    rows = [complete_trade(source_file=None)]
    profiles = [profile_legacy_master_row(0, rows[0])]
    cohort = build_source_cohort_profiles(rows, profiles)[0]
    assert cohort["cohort_values"]["source_file"] == "<NULL>"


def test_order_id_pattern_is_descriptive_only() -> None:
    rows = [complete_trade(order_id="freqtrade-paper-1", source_file="a")]
    profiles = [profile_legacy_master_row(0, rows[0])]
    cohort = build_source_cohort_profiles(rows, profiles)[0]
    assert cohort["order_id_format_distribution"] == {"freqtrade_paper_local_trade_id": 1}
    assert cohort["filename_is_not_identity_authority"] is True


def test_legacy_observation_key_requires_all_eight_fields() -> None:
    row = observation_trade()
    row.pop("quantity")
    key = legacy_observation_key_for(row)
    assert key["status"] == "unverifiable"
    assert "missing_quantity" in key["reasons"]


def test_unique_overlap_is_not_confirmed_duplicate() -> None:
    overlap = build_legacy_overlap_profile([observation_trade()], [observation_trade()])
    result = overlap["legacy_overlap_results"][0]
    assert result["classification"] == "unique_exact_legacy_overlap_candidate"
    assert result["duplicate_confirmed"] is False
    assert result["import_eligible"] is False


def test_multiple_overlap_is_ambiguous() -> None:
    overlap = build_legacy_overlap_profile(
        [observation_trade(), observation_trade()],
        [observation_trade()],
    )
    assert overlap["multiple_exact_legacy_overlap_candidate_count"] == 1


def test_no_overlap_does_not_mean_new_trade() -> None:
    overlap = build_legacy_overlap_profile(
        [observation_trade()],
        [observation_trade(net_pnl="8.00000000")],
    )
    result = overlap["legacy_overlap_results"][0]
    assert result["classification"] == "no_exact_legacy_overlap_observed"
    assert result["new_trade_confirmed"] is False


def test_no_overlap_is_unverifiable_when_master_key_coverage_is_incomplete() -> None:
    overlap = build_legacy_overlap_profile(
        [{"order_id": "legacy-without-observation-fields"}],
        [observation_trade()],
    )
    result = overlap["legacy_overlap_results"][0]
    assert result["classification"] == "legacy_overlap_unverifiable"
    assert result["reasons"] == ["master_observation_coverage_incomplete"]


def test_same_hash_with_different_payload_is_detected() -> None:
    overlap = build_legacy_overlap_profile(
        [observation_trade()],
        [observation_trade(net_pnl="8.00000000")],
        row_hasher=lambda _: "a" * 64,
    )
    assert overlap["legacy_observation_hash_collision_count"] == 1
    assert overlap["legacy_overlap_results"][0]["classification"] == (
        "legacy_overlap_unverifiable"
    )


def test_all_rows_remain_not_import_eligible(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade(), complete_trade("paper-2")])
    assert report["import_eligible_true_count"] == 0
    assert all(item["import_eligible"] is False for item in report["row_profiles"])


def test_incomplete_row_does_not_generate_fingerprint() -> None:
    row = profile_legacy_master_row(0, {"order_id": "legacy-1"})
    assert row["fingerprint_generation_allowed"] is False


def test_default_does_not_write_report(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()])
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports/trader_master_legacy_lineage_profile_v2.json").exists()


def test_write_report_is_limited_to_data_reports(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()], write_report=True)
    assert report["write_performed"] is True
    assert (tmp_path / "data/reports/trader_master_legacy_lineage_profile_v2.json").exists()
    assert (tmp_path / "data/reports/trader_master_legacy_lineage_profile_v2.md").exists()


def test_unsafe_write_report_path_is_blocked(tmp_path: Path) -> None:
    report = run_profile(
        tmp_path,
        [complete_trade()],
        write_report=True,
        output_json="outside.json",
    )
    assert report["reason"] == "unsafe_report_output_path"


def test_fingerprint_spec_is_unchanged_by_profile(tmp_path: Path) -> None:
    spec = ROOT / "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
    before = file_hash(spec)
    run_profile(tmp_path, [complete_trade()])
    assert file_hash(spec) == before


def test_no_legacy_writer_is_called() -> None:
    source = (ROOT / "smartcrypto/data/trader_master_fingerprint_v2/legacy_lineage_profile.py").read_text()
    for forbidden in ("to_parquet", "to_excel", "to_csv", "sqlite3", "trades_master.xlsx"):
        assert forbidden not in source


def test_cli_runs_without_pythonpath(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--source-profile",
            str(PROFILE),
            "--json",
        ],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["reason"] == "account_scope_hash_missing"


def test_output_is_deterministic_except_timestamp(tmp_path: Path) -> None:
    first = run_profile(tmp_path, [complete_trade()])
    second = run_profile(tmp_path, [complete_trade()])
    assert first == second


def test_data_trades_files_are_not_changed(tmp_path: Path) -> None:
    master = write_master(tmp_path, [complete_trade()])
    before = file_hash(master)

    def adapter_builder(**_: Any) -> FreqtradePaperAdapterBundle:
        return fake_bundle()

    build_trader_master_legacy_lineage_profile_report(
        project_root=tmp_path,
        trader_master_path=master,
        source_profile_path=PROFILE,
        account_scope_hash=ACCOUNT_HASH,
        adapter_builder=adapter_builder,
    )
    assert file_hash(master) == before


def test_contract_contains_required_global_fields(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()])
    required = {
        "schema_version",
        "decision",
        "field_lineage_profile",
        "source_cohort_profiles",
        "legacy_overlap_results",
        "recommended_next_action",
        "import_eligible_true_count",
    }
    assert required <= report.keys()
    assert report["legacy_observation_key_version"] == LEGACY_OBSERVATION_KEY_VERSION


def test_safety_flags_are_fail_closed(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()])
    for field, expected in SAFETY_FLAGS.items():
        assert report[field] is expected
    assert report["operational_authority"] is False


def test_account_hash_is_not_persisted(tmp_path: Path) -> None:
    report = run_profile(tmp_path, [complete_trade()])
    serialized = json.dumps(report, default=str)
    assert ACCOUNT_HASH not in serialized
    assert report["account_scope_original_identifier_persisted"] is False
