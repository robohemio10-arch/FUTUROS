from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.bitradex_ocr_adapter import (
    SAFETY_FLAGS,
    build_bitradex_ocr_readonly_adapter_report,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_bitradex_ocr_batch_readonly_adapter_v2.py"
ACCOUNT_HASH = "c" * 64


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def source_row(index: int, *, financial_index: int | None = None) -> dict[str, str]:
    financial = financial_index or index
    entry = Decimal("100") + Decimal(financial) / Decimal("100")
    exit_price = entry + (Decimal("1") if financial % 2 else Decimal("-1"))
    side = "LONG" if financial % 3 else "SHORT"
    gross = exit_price - entry if side == "LONG" else entry - exit_price
    taxa_total = Decimal("-0.10")
    taxa_execucao = Decimal("-0.10")
    net = gross + taxa_total + taxa_execucao
    source_name = f"BITRADEX ({index}).jpg"
    source_sha = hashlib.sha256(source_name.encode()).hexdigest()
    return {
        "source_file_name": source_name,
        "source_image_path": f"E:\\fixture\\{source_name}",
        "source_sha256": source_sha,
        "order_id": f"ocr-{financial}",
        "symbol": "BTCUSDT" if financial % 2 else "ETHUSDT",
        "position_side": side,
        "entry_price": _decimal(entry),
        "exit_price": _decimal(exit_price),
        "closed_volume": "1",
        "open_time_utc": f"2026-06-01T{financial % 24:02d}:00:00Z",
        "close_time_utc": f"2026-06-01T{financial % 24:02d}:05:00Z",
        "pnl_fechado": _decimal(net),
        "taxa_1": _decimal(taxa_total),
        "taxa_2": _decimal(taxa_execucao),
        "funding_fee_source": "0",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _profile_payload(*, funding_approved: bool, fee_approved: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "bitradex_ocr_locked_candidates_source_profile_v2",
        "profile_id": "fixture_bitradex_v2",
        "batch_id": "fixture",
        "source_files": {
            "package_v4": "v4",
            "package_v5": "v5",
            "canonical_v4_csv": "canonical.csv",
            "excluded_duplicates_v5_csv": "excluded.csv",
            "duplicate_groups_v5_csv": "groups.csv",
            "synthetic_mapping_v5_csv": "mapping.csv",
        },
        "identity": {
            "venue": "bitradex",
            "market_type": "usdt_m_futures",
            "contract_type": "linear_perpetual",
            "settlement_currency": "USDT",
            "quantity_unit": "base_asset",
            "contract_size": "1",
            "source_namespace": "bitradex_fixture",
            "native_order_identity_policy": "raw_ocr_evidence_only_no_native_identity",
        },
        "financial_contract": {
            "gross_pnl_formula": (
                "long=(exit-entry)*quantity*contract_size;"
                "short=(entry-exit)*quantity*contract_size"
            ),
            "taxa_total_column": "taxa_1",
            "taxa_execucao_column": "taxa_2",
            "fee_relation": "distinct_additive_negative_cost_components",
            "fee_source_sign": "negative_cost",
            "trading_fee_formula": "abs(taxa_total)+abs(taxa_execucao)",
            "fee_contract_approved": fee_approved,
            "funding_source_column": "funding_fee_source" if funding_approved else None,
            "funding_source_rule": (
                "direct_source_cost_positive_revenue_negative"
                if funding_approved
                else "authoritative_funding_unavailable_block"
            ),
            "funding_contract_approved": funding_approved,
            "net_pnl_column": "pnl_fechado",
            "epsilon_abs_fonte": "0.000001",
        },
    }


def prepare_project(
    tmp_path: Path,
    *,
    funding_approved: bool = True,
    fee_approved: bool = True,
    master_unverifiable: bool = False,
) -> tuple[Path, Path]:
    rows = [source_row(index) for index in range(1, 507)]
    rows[111] = source_row(112, financial_index=111)
    rows[347] = source_row(348, financial_index=347)
    v4 = tmp_path / "v4"
    v5 = tmp_path / "v5"
    _write_csv(v4 / "canonical.csv", rows)

    excluded = []
    groups = []
    for group_index, (retained_index, excluded_index) in enumerate(
        ((111, 112), (347, 348)), start=1
    ):
        retained = rows[retained_index - 1]
        duplicate = rows[excluded_index - 1]
        fingerprint = hashlib.sha256(f"group-{group_index}".encode()).hexdigest()
        excluded.append(
            {
                "excluded_source_file_name": duplicate["source_file_name"],
                "excluded_source_sha256": duplicate["source_sha256"],
                "retained_source_file_name": retained["source_file_name"],
                "retained_source_sha256": retained["source_sha256"],
                "financial_trade_fingerprint": fingerprint,
                "exclusion_reason": "DUPLICATE_REAL_TRADE_EXCLUDED",
            }
        )
        for disposition, member in (("RETAINED", retained), ("EXCLUDED", duplicate)):
            groups.append(
                {
                    "duplicate_group_id": f"GROUP_{group_index}",
                    "financial_trade_fingerprint": fingerprint,
                    "disposition": disposition,
                    "source_file_name": member["source_file_name"],
                    "source_sha256": member["source_sha256"],
                }
            )
    _write_csv(v5 / "excluded.csv", excluded)
    _write_csv(v5 / "groups.csv", groups)

    excluded_keys = {
        (row["excluded_source_file_name"], row["excluded_source_sha256"])
        for row in excluded
    }
    retained_rows = [
        row
        for row in rows
        if (row["source_file_name"], row["source_sha256"]) not in excluded_keys
    ]
    mapping = []
    for row in retained_rows:
        mapping.append(
            {
                "raw_order_id": row["order_id"],
                "synthetic_order_id": hashlib.sha256(
                    row["source_sha256"].encode()
                ).hexdigest()[:24],
                "financial_trade_fingerprint": hashlib.sha256(
                    row["source_file_name"].encode()
                ).hexdigest(),
                "source_file_name": row["source_file_name"],
                "source_image_path": row["source_image_path"],
                "source_sha256": row["source_sha256"],
            }
        )
    _write_csv(v5 / "mapping.csv", mapping)

    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            _profile_payload(
                funding_approved=funding_approved,
                fee_approved=fee_approved,
            )
        ),
        encoding="utf-8",
    )
    master = tmp_path / "data" / "trades" / "trades_master.parquet"
    master.parent.mkdir(parents=True)
    if master_unverifiable:
        pd.DataFrame([{"order_id": "legacy-only", "moeda": "BTCUSDT"}]).to_parquet(
            master, index=False
        )
    else:
        pd.DataFrame(columns=["order_id", "moeda"]).to_parquet(master, index=False)
    return profile, master


def build(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    profile, master = prepare_project(
        tmp_path,
        funding_approved=kwargs.pop("funding_approved", True),
        fee_approved=kwargs.pop("fee_approved", True),
        master_unverifiable=kwargs.pop("master_unverifiable", False),
    )
    return build_bitradex_ocr_readonly_adapter_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=kwargs.pop("account_scope_hash", ACCOUNT_HASH),
        trader_master_path=master,
        generated_at_utc="2026-07-14T00:00:00+00:00",
        **kwargs,
    )


def test_506_inputs_and_two_exclusions_produce_504(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["input_rows"] == 506
    assert report["excluded_duplicate_rows"] == 2
    assert report["source_record_count"] == 504


def test_synthetic_ids_never_enter_canonical_identity(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["synthetic_identity_usage_count"] == 0
    assert report["order_id_non_null_count"] == 0
    assert report["source_trade_id_non_null_count"] == 0
    assert all(row["order_id"] is None for row in report["record_results"])
    assert all(row["source_trade_id"] is None for row in report["record_results"])


def test_raw_ocr_order_id_remains_lineage_only(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["raw_ocr_order_id_lineage_count"] == 504
    assert report["record_results"][0]["raw_ocr_order_id"] == "ocr-1"
    assert report["record_results"][0]["synthetic_id_used_as_native_identity"] is False


def test_account_scope_hash_missing_blocks(tmp_path: Path) -> None:
    report = build(tmp_path, account_scope_hash=None)
    assert report["status"] == "blocked"
    assert report["reason"] == "account_scope_hash_missing"


def test_unapproved_fee_contract_blocks_all_rows(tmp_path: Path) -> None:
    report = build(tmp_path, fee_approved=False)
    assert report["classification_counts"]["ACCOUNTING_CONTRACT_BLOCKED"] == 504
    assert "fee_contract_not_approved" in report["record_results"][0]["reasons"]


def test_missing_funding_without_approved_rule_blocks_all_rows(tmp_path: Path) -> None:
    report = build(tmp_path, funding_approved=False)
    assert report["classification_counts"]["ACCOUNTING_CONTRACT_BLOCKED"] == 504
    assert "funding_contract_not_approved" in report["record_results"][0]["reasons"]
    assert report["canonical_trade_id_count"] == 0


def test_fully_unverifiable_master_never_classifies_novel(tmp_path: Path) -> None:
    report = build(tmp_path, master_unverifiable=True)
    assert report["master_canonical_record_count"] == 0
    assert report["master_unverifiable_row_count"] == 1
    assert report["classification_counts"]["VERIFIED_NOVEL"] == 0
    assert report["classification_counts"]["LEGACY_OVERLAP_AMBIGUOUS"] == 504
    assert all(not row["import_eligible"] for row in report["record_results"])


def test_master_uses_temporary_copy_and_preserves_sha256(tmp_path: Path) -> None:
    profile, master = prepare_project(tmp_path)
    before = hashlib.sha256(master.read_bytes()).hexdigest()
    report = build_bitradex_ocr_readonly_adapter_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        generated_at_utc="2026-07-14T00:00:00+00:00",
    )
    assert report["trader_master_temp_copy_used"] is True
    assert report["trader_master_hash_preserved"] is True
    assert report["trader_master_sha256_before"] == before
    assert report["trader_master_sha256_after"] == before
    assert hashlib.sha256(master.read_bytes()).hexdigest() == before


def test_default_no_write_and_safety_flags(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()
    for flag, expected in SAFETY_FLAGS.items():
        assert report[flag] is expected
        assert report["safety"][flag] is expected


def test_write_report_only_writes_json_and_markdown(tmp_path: Path) -> None:
    report = build(tmp_path, write_report=True)
    reports = tmp_path / "data" / "reports"
    assert report["write_performed"] is True
    assert sorted(path.suffix for path in reports.iterdir()) == [".json", ".md"]
    assert not list(tmp_path.rglob("*.xlsx"))
    assert len(list(tmp_path.rglob("*.parquet"))) == 1


def test_adapter_reuses_protected_contracts_without_direct_parquet_read() -> None:
    source = (
        ROOT
        / "smartcrypto/data/trader_master_fingerprint_v2/bitradex_ocr_adapter.py"
    ).read_text(encoding="utf-8")
    assert "read_trader_master_readonly(" in source
    assert "validate_staging_records(" in source
    assert "reconcile_canonical_records(" in source
    assert "canonical_trade_id_for(" in source
    assert "pd.read_parquet" not in source
    assert "pandas.read_parquet" not in source


def test_legacy_master_policy_registers_new_readonly_consumers() -> None:
    policy = json.loads(
        (ROOT / "config/trader_master_legacy_research_only_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    registrations = {
        item["relative_path"]: item for item in policy["registered_consumers"]
    }
    expected = {
        "smartcrypto/data/trader_master_fingerprint_v2/bitradex_ocr_adapter.py",
        "smartcrypto/research/profit_research/paper_analysis.py",
    }

    assert expected <= registrations.keys()
    assert len(registrations) == 11
    for path in expected:
        registration = registrations[path]
        assert registration["consumer_classification"] == "registered_readonly_consumer"
        assert registration["allowed_access_mode"] == "read_only"
        assert registration["allowed_capabilities"] == [
            "read_rows",
            "read_schema",
            "compute_hash",
            "diagnostic_metrics",
        ]
        assert registration["operational_authority"] is False
    assert policy["prohibited_capabilities"] == [
        "write",
        "import",
        "fingerprint_generation",
        "deduplication",
        "operational_training",
        "paper_signal_selection",
        "live_signal_selection",
        "risk_decision",
        "order_execution",
        "model_promotion",
        "operational_release",
    ]


def test_cli_executes_without_writing(tmp_path: Path) -> None:
    profile, master = prepare_project(tmp_path, funding_approved=False)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--source-profile",
            str(profile),
            "--account-scope-hash",
            ACCOUNT_HASH,
            "--trader-master",
            str(master),
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["source_record_count"] == 504
    assert payload["reason"] == "accounting_contract_blocked"
    assert payload["write_performed"] is False
