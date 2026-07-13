from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter import (
    adapt_record,
    build_freqtrade_paper_closed_trades_adapter_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_trader_master_staging_v2.py"
ACCOUNT_HASH = "c" * 64


def profile_payload(*, funding: str = "column", namespace: str = "freqtrade:paper:sqlite:trades.id:v1") -> dict[str, Any]:
    return {
        "schema_version": "freqtrade_paper_closed_trades_source_profile_v2",
        "profile_id": "test_freqtrade_paper_source_v2",
        "producer_module": "smartcrypto.data.paper_trade_lifecycle",
        "producer_function": "collect_closed_feedback.normalize_closed_trades",
        "source_files": {
            "primary_source_path": "data/trades/inbox/freqtrade_paper_closed_trades.csv",
            "replica_source_paths": ["data/trades/freqtrade_paper_closed_smartcrypto.csv"],
        },
        "identity": {
            "venue": "binance",
            "market_type": "usdt-m_futures",
            "contract_type": "linear_perpetual",
            "settlement_currency": "USDT",
            "quantity_unit": "base_asset",
            "contract_size": "1.00000000",
            "source_namespace": "phase14_freqtrade_paper_closed_trades",
            "order_id_namespace": namespace,
            "order_id_semantics": "freqtrade-paper-{local_trades_table_id}",
        },
        "column_map": {
            "symbol": "moeda",
            "side": "fechar_side",
            "order_id": "order_id",
            "open_time": "horario_abertura",
            "close_time": "horario_fechamento",
            "entry_price": "preco_abertura",
            "exit_price": "preco_fechamento",
            "quantity": "volume_fechado",
            "net_pnl": "pnl_fechado",
            "fee_open": "taxa_1",
            "fee_close": "taxa_2",
        },
        "financial_contract": {
            "gross_pnl_formula": "linear_price_delta_times_quantity_contract_size",
            "fee_source_sign": "positive_cost",
            "zero_fee_handling": "quarantine_as_unverifiable",
            "funding_availability": funding,
            "funding_column": "funding_fee" if funding == "column" else None,
            "funding_sign": "positive_cost_negative_revenue",
            "epsilon_abs_fonte": "0.00000001",
            "pnl_semantics": "reported net pnl from close_profit_abs",
        },
    }


def valid_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "moeda": "BTCUSDT",
        "fechar_side": "long",
        "order_id": "freqtrade-paper-123",
        "horario_abertura": "2026-06-01T10:00:00Z",
        "horario_fechamento": "2026-06-01T10:05:00Z",
        "preco_abertura": "100",
        "preco_fechamento": "110",
        "volume_fechado": "1",
        "pnl_fechado": "7",
        "taxa_1": "1",
        "taxa_2": "1",
        "funding_fee": "1",
    }
    row.update(overrides)
    return row


def write_fixture(
    root: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    funding: str = "column",
    namespace: str = "freqtrade:paper:sqlite:trades.id:v1",
    identical_replica: bool = True,
) -> tuple[Path, Path, Path]:
    profile = root / "config" / "profile.json"
    primary = root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    replica = root / "data" / "trades" / "freqtrade_paper_closed_smartcrypto.csv"
    profile.parent.mkdir(parents=True)
    primary.parent.mkdir(parents=True)
    replica.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(profile_payload(funding=funding, namespace=namespace)), encoding="utf-8"
    )
    pd.DataFrame(rows or [valid_row()]).to_csv(primary, index=False)
    if identical_replica:
        replica.write_bytes(primary.read_bytes())
    else:
        pd.DataFrame([valid_row(order_id="freqtrade-paper-other")]).to_csv(replica, index=False)
    return profile, primary, replica


def build(root: Path, profile: Path, **kwargs: Any) -> dict[str, Any]:
    return build_freqtrade_paper_closed_trades_adapter_report(
        project_root=root,
        source_profile_path=profile,
        account_scope_hash=kwargs.pop("account_scope_hash", ACCOUNT_HASH),
        **kwargs,
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hash_identical_files_are_classified_as_source_replicas(tmp_path: Path) -> None:
    profile, primary, replica = write_fixture(tmp_path)
    report = build(tmp_path, profile)
    assert sha256(primary) == sha256(replica)
    assert report["source_replica_hash_identical"] is True
    assert report["source_replica_count"] == 1
    assert report["unique_source_batch_count"] == 1
    assert report["raw_row_count"] == 1


def test_missing_account_scope_hash_is_blocked(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile, account_scope_hash=None)
    assert report["reason"] == "account_scope_hash_missing"
    assert report["accepted_row_count"] == 0


def test_invalid_account_scope_hash_is_blocked(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile, account_scope_hash="not-a-sha256")
    assert report["reason"] == "account_scope_hash_invalid"


def test_missing_order_id_namespace_is_fail_closed(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path, namespace="missing")
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["identity"]["order_id_namespace"] = ""
    profile.write_text(json.dumps(payload), encoding="utf-8")
    report = build(tmp_path, profile)
    assert report["status"] == "blocked"
    assert report["reason"] == "source_profile_invalid"
    assert any("order_id_namespace" in error for error in report["validation_errors"])


def test_order_id_is_preserved_without_source_trade_id_fabrication(tmp_path: Path) -> None:
    profile_path, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons = adapt_record(valid_row(), profile=profile, account_scope_hash=ACCOUNT_HASH)
    assert reasons == []
    assert canonical is not None
    assert canonical["order_id"] == "freqtrade-paper-123"
    assert canonical["source_trade_id"] is None


def test_gross_pnl_is_reconstructed_independently_from_reported_net(tmp_path: Path) -> None:
    profile_path, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons = adapt_record(
        valid_row(pnl_fechado="999"), profile=profile, account_scope_hash=ACCOUNT_HASH
    )
    assert reasons == []
    assert canonical is not None
    assert canonical["gross_pnl"] == "10.00000000"
    assert canonical["net_pnl"] == "999"


def test_fee_cost_sign_is_normalized_by_positive_cost_contract(tmp_path: Path) -> None:
    profile_path, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons = adapt_record(valid_row(), profile=profile, account_scope_hash=ACCOUNT_HASH)
    assert reasons == []
    assert canonical is not None
    assert canonical["trading_fee"] == "2"


def test_negative_fee_is_quarantined_not_silently_absed(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path, rows=[valid_row(taxa_1="-1")])
    report = build(tmp_path, profile)
    assert report["status"] == "blocked"
    assert report["quarantined_reason_counts"]["trading_fee_sign_invalid"] == 1


def test_zero_fee_from_producer_default_is_not_treated_as_demonstrated(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path, rows=[valid_row(taxa_1="0")])
    report = build(tmp_path, profile)
    assert report["status"] == "blocked"
    assert (
        report["quarantined_reason_counts"][
            "trading_fee_unverifiable_zero_from_producer_default"
        ]
        == 1
    )


def test_absent_funding_is_fail_closed_for_every_row(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(
        tmp_path,
        rows=[valid_row(), valid_row(order_id="freqtrade-paper-124")],
        funding="absent",
    )
    report = build(tmp_path, profile)
    assert report["accepted_row_count"] == 0
    assert report["quarantined_row_count"] == 2
    assert report["quarantined_reason_counts"]["funding_fee_unavailable"] == 2


def test_valid_financial_identity_is_accepted_by_v2_validator(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile)
    assert report["status"] == "ok"
    assert report["accepted_row_count"] == 1
    assert report["quarantined_row_count"] == 0
    assert report["canonical_records_delivered_to_validator_count"] == 1


def test_invalid_financial_identity_is_quarantined_by_v2_validator(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path, rows=[valid_row(pnl_fechado="8")])
    report = build(tmp_path, profile)
    assert report["status"] == "blocked"
    assert report["accepted_row_count"] == 0
    assert report["quarantined_reason_counts"]["financial_accounting_identity_violation"] == 1


def test_divergent_replica_is_blocked(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path, identical_replica=False)
    report = build(tmp_path, profile)
    assert report["status"] == "blocked"
    assert report["reason"] == "source_replica_hash_mismatch"


def test_adapter_no_write_does_not_create_or_modify_files(tmp_path: Path) -> None:
    profile, primary, replica = write_fixture(tmp_path)
    before = {path: path.read_bytes() for path in (profile, primary, replica)}
    report = build(tmp_path, profile)
    after = {path: path.read_bytes() for path in (profile, primary, replica)}
    assert report["write_performed"] is False
    assert before == after
    assert not (tmp_path / "data" / "reports").exists()


def test_real_batch_no_write_remains_unmodified() -> None:
    profile = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"
    primary = ROOT / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    replica = ROOT / "data" / "trades" / "freqtrade_paper_closed_smartcrypto.csv"
    master = ROOT / "data" / "trades" / "trades_master.parquet"
    if not all(path.exists() for path in (primary, replica, master)):
        pytest.skip("runtime paper sources are not present in this checkout")
    before = {path: sha256(path) for path in (primary, replica, master)}
    report = build(ROOT, profile)
    after = {path: sha256(path) for path in (primary, replica, master)}
    assert report["write_performed"] is False
    assert report["write_to_master_performed"] is False
    assert before == after


def test_cli_accepts_source_profile_and_account_scope_hash(tmp_path: Path) -> None:
    profile, _, _ = write_fixture(tmp_path)
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
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["account_scope_original_identifier_persisted"] is False
    assert payload["research_pipeline_writes_runtime"] is False
    assert payload["write_to_master_performed"] is False
    assert payload["sends_exchange_orders"] is False
    assert payload["exchange_private_access"] is False


def test_profile_does_not_contain_account_identity_or_secret_material() -> None:
    payload = json.loads(
        (ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(payload).casefold()
    assert "account_scope_hash" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
