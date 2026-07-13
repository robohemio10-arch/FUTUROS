from __future__ import annotations

import hashlib
import json
import sqlite3
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
ORDER_NAMESPACE = "freqtrade:paper:sqlite:trades.id:v1"


def profile_payload(*, namespace: str = ORDER_NAMESPACE) -> dict[str, Any]:
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
            "contract_size_source": "authoritative_sqlite.trades.contract_size",
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
            "leverage": "leverage",
        },
        "authoritative_sqlite": {
            "snapshot_path": "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
            "explicitly_non_authoritative_paths": [
                "freqtrade/user_data/tradesv3.paper.sqlite"
            ],
            "table": "trades",
            "closed_trade_filter": "is_open = 0",
            "join_key_semantics": "freqtrade-paper-{trades.id}",
            "access_mode": "temporary_copy_query_only",
            "required_columns": [
                "id",
                "exchange",
                "pair",
                "is_open",
                "is_short",
                "open_rate",
                "close_rate",
                "amount",
                "contract_size",
                "leverage",
                "fee_open_cost",
                "fee_close_cost",
                "fee_open_currency",
                "fee_close_currency",
                "funding_fees",
                "close_profit_abs",
                "realized_profit",
                "open_date",
                "close_date",
            ],
        },
        "financial_contract": {
            "gross_pnl_formula": "linear_price_delta_times_quantity_contract_size",
            "fee_source_sign": "positive_cost",
            "zero_fee_handling": "allow_authoritative_sqlite_zero",
            "funding_availability": "authoritative_sqlite_column",
            "funding_column": "funding_fees",
            "funding_sign": "source_positive_revenue_negative_cost",
            "fee_open_normalization": "fee_open_cost_times_leverage",
            "fee_close_normalization": "fee_close_cost",
            "funding_normalization": "negate_source_value_to_cost",
            "net_reference_column": "close_profit_abs",
            "epsilon_abs_fonte": "0.00000001",
            "pnl_semantics": "reported net pnl from close_profit_abs",
        },
    }


def sqlite_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 123,
        "exchange": "binance",
        "pair": "BTC/USDT:USDT",
        "is_open": 0,
        "is_short": 0,
        "open_rate": 100.0,
        "close_rate": 110.0,
        "amount": 1.0,
        "contract_size": 1.0,
        "leverage": 2.0,
        "fee_open_cost": 0.5,
        "fee_close_cost": 1.0,
        "fee_open_currency": "USDT",
        "fee_close_currency": "USDT",
        "funding_fees": -1.0,
        "close_profit_abs": 7.0,
        "realized_profit": 7.0,
        "open_date": "2026-06-01 10:00:00.000000",
        "close_date": "2026-06-01 10:05:00.000000",
    }
    row.update(overrides)
    return row


def csv_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "moeda": "BTCUSDT",
        "fechar_side": "long",
        "leverage": 2.0,
        "order_id": "freqtrade-paper-123",
        "horario_abertura": "2026-06-01 10:00:00.000000",
        "horario_fechamento": "2026-06-01 10:05:00.000000",
        "preco_abertura": 100.0,
        "preco_fechamento": 110.0,
        "volume_fechado": 1.0,
        "pnl_fechado": 7.0,
        "taxa_1": 0.5,
        "taxa_2": 1.0,
    }
    row.update(overrides)
    return row


def write_sqlite(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(sqlite_row())
    connection = sqlite3.connect(path)
    try:
        definitions = ", ".join(
            f'"{column}" {"INTEGER" if column in {"id", "is_open", "is_short"} else "REAL" if column not in {"exchange", "pair", "fee_open_currency", "fee_close_currency", "open_date", "close_date"} else "TEXT"}'
            for column in columns
        )
        connection.execute(f'CREATE TABLE trades ({definitions}, PRIMARY KEY ("id"))')
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(f'"{column}"' for column in columns)
        connection.executemany(
            f"INSERT INTO trades ({names}) VALUES ({placeholders})",
            [[row.get(column) for column in columns] for row in rows],
        )
        connection.commit()
    finally:
        connection.close()


def write_fixture(
    root: Path,
    *,
    csv_rows: list[dict[str, Any]] | None = None,
    sqlite_rows: list[dict[str, Any]] | None = None,
    namespace: str = ORDER_NAMESPACE,
    identical_replica: bool = True,
) -> tuple[Path, Path, Path, Path]:
    profile = root / "config" / "profile.json"
    primary = root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    replica = root / "data" / "trades" / "freqtrade_paper_closed_smartcrypto.csv"
    snapshot = root / "data" / "snapshots" / "freqtrade-paper" / "tradesv3.paper.snapshot.sqlite"
    profile.parent.mkdir(parents=True)
    primary.parent.mkdir(parents=True)
    replica.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps(profile_payload(namespace=namespace)), encoding="utf-8")
    pd.DataFrame(csv_rows or [csv_row()]).to_csv(primary, index=False)
    if identical_replica:
        replica.write_bytes(primary.read_bytes())
    else:
        pd.DataFrame([csv_row(order_id="freqtrade-paper-999")]).to_csv(replica, index=False)
    write_sqlite(snapshot, sqlite_rows or [sqlite_row()])
    return profile, primary, replica, snapshot


def build(root: Path, profile: Path, **kwargs: Any) -> dict[str, Any]:
    return build_freqtrade_paper_closed_trades_adapter_report(
        project_root=root,
        source_profile_path=profile,
        account_scope_hash=kwargs.pop("account_scope_hash", ACCOUNT_HASH),
        **kwargs,
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hash_identical_files_are_one_source_batch(tmp_path: Path) -> None:
    profile, primary, replica, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile)
    assert sha256(primary) == sha256(replica)
    assert report["source_replica_hash_identical"] is True
    assert report["source_replica_count"] == 1
    assert report["unique_source_batch_count"] == 1


@pytest.mark.parametrize("account_hash", [None, "not-a-sha256"])
def test_account_scope_hash_is_mandatory_and_validated(
    tmp_path: Path, account_hash: str | None
) -> None:
    profile, _, _, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile, account_scope_hash=account_hash)
    expected = "account_scope_hash_missing" if account_hash is None else "account_scope_hash_invalid"
    assert report["reason"] == expected
    assert report["accepted_row_count"] == 0


def test_missing_order_id_namespace_is_fail_closed(tmp_path: Path) -> None:
    profile, _, _, _ = write_fixture(tmp_path)
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["identity"]["order_id_namespace"] = ""
    profile.write_text(json.dumps(payload), encoding="utf-8")
    report = build(tmp_path, profile)
    assert report["reason"] == "source_profile_invalid"


def test_exact_one_to_one_join_and_native_order_id_preserved(tmp_path: Path) -> None:
    profile, _, _, _ = write_fixture(tmp_path)
    report = build(tmp_path, profile)
    assert report["exact_join_count"] == 1
    assert report["csv_only_trade_id_count"] == 0
    assert report["sqlite_only_trade_id_count"] == 0
    assert report["accepted_row_count"] == 1
    assert report["row_results"][0]["order_id"] == "freqtrade-paper-123"


def test_malformed_duplicate_and_unmatched_ids_block_join(tmp_path: Path) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(), csv_row(order_id="bad-id")],
        sqlite_rows=[sqlite_row(), sqlite_row(id=124)],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["reason"] == "authoritative_sqlite_join_contract_violation"
    assert report["malformed_order_id_count"] == 1
    assert report["sqlite_only_trade_id_count"] == 1


def test_duplicate_csv_trade_id_blocks_one_to_one_join(tmp_path: Path) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(), csv_row()],
        sqlite_rows=[sqlite_row()],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["reason"] == "authoritative_sqlite_join_contract_violation"
    assert report["duplicate_csv_trade_id_count"] == 1
    assert report["duplicate_csv_trade_ids"] == [123]


def test_gross_is_independent_from_reported_net(tmp_path: Path) -> None:
    profile_path, _, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons, metrics = adapt_record(
        csv_row(pnl_fechado=999.0),
        sqlite_row(close_profit_abs=999.0, realized_profit=999.0),
        profile=profile,
        account_scope_hash=ACCOUNT_HASH,
    )
    assert canonical is None
    assert "financial_accounting_identity_violation" in reasons
    assert metrics["gross_pnl"] == 10


def test_fee_and_funding_signs_follow_authoritative_contract(tmp_path: Path) -> None:
    profile_path, _, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons, metrics = adapt_record(
        csv_row(), sqlite_row(), profile=profile, account_scope_hash=ACCOUNT_HASH
    )
    assert reasons == []
    assert canonical is not None
    assert metrics["effective_open_fee"] == 1
    assert metrics["effective_close_fee"] == 1
    assert canonical["trading_fee"] == "2.00"
    assert canonical["funding_fee"] == "1.0"


def test_csv_fee_fields_do_not_override_authoritative_sqlite(tmp_path: Path) -> None:
    profile_path, _, _, _ = write_fixture(tmp_path)
    profile = load_source_profile(profile_path)
    canonical, reasons, _ = adapt_record(
        csv_row(taxa_1=999.0, taxa_2=999.0),
        sqlite_row(),
        profile=profile,
        account_scope_hash=ACCOUNT_HASH,
    )
    assert reasons == []
    assert canonical is not None
    assert canonical["trading_fee"] == "2.00"


def test_authoritative_zero_fee_is_demonstrated_not_defaulted(tmp_path: Path) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(taxa_1=0.0, taxa_2=0.0, pnl_fechado=9.0)],
        sqlite_rows=[
            sqlite_row(
                fee_open_cost=0.0,
                fee_close_cost=0.0,
                close_profit_abs=9.0,
                realized_profit=9.0,
            )
        ],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["accepted_row_count"] == 1


def test_missing_funding_is_fail_closed(tmp_path: Path) -> None:
    profile, _, _, _ = write_fixture(tmp_path, sqlite_rows=[sqlite_row(funding_fees=None)])
    report = build(tmp_path, profile)
    assert report["accepted_row_count"] == 0
    assert report["quarantined_reason_counts"]["funding_fees_unavailable"] == 1


def test_accounting_identity_valid_and_invalid_are_separated(tmp_path: Path) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(), csv_row(order_id="freqtrade-paper-124", pnl_fechado=8.0)],
        sqlite_rows=[
            sqlite_row(),
            sqlite_row(id=124, close_profit_abs=8.0, realized_profit=8.0),
        ],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["formula_match_count"] == 1
    assert report["formula_mismatch_count"] == 1
    assert report["accepted_row_count"] == 1
    assert report["quarantined_order_ids"] == ["freqtrade-paper-124"]


@pytest.mark.parametrize(
    ("csv_override", "sqlite_override", "reason"),
    [
        ({"moeda": "ETHUSDT"}, {}, "symbol_divergence"),
        ({"fechar_side": "short"}, {}, "side_divergence"),
        ({"horario_fechamento": "2026-06-01 10:06:00"}, {}, "close_time_divergence"),
        ({"pnl_fechado": 8.0}, {}, "net_pnl_divergence"),
    ],
)
def test_csv_sqlite_identity_divergence_is_quarantined(
    tmp_path: Path,
    csv_override: dict[str, Any],
    sqlite_override: dict[str, Any],
    reason: str,
) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(**csv_override)],
        sqlite_rows=[sqlite_row(**sqlite_override)],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["quarantined_reason_counts"][reason] == 1


def test_missing_close_rate_remains_quarantined(tmp_path: Path) -> None:
    profile, _, replica, _ = write_fixture(
        tmp_path,
        csv_rows=[csv_row(preco_fechamento=None)],
        sqlite_rows=[sqlite_row(close_rate=None)],
    )
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica.write_bytes(primary.read_bytes())
    report = build(tmp_path, profile)
    assert report["accepted_row_count"] == 0
    assert report["quarantined_reason_counts"]["exit_price_unavailable"] == 1


def test_snapshot_is_temp_copied_query_only_and_hashes_are_preserved(tmp_path: Path) -> None:
    profile, _, _, snapshot = write_fixture(tmp_path)
    before = sha256(snapshot)
    report = build(tmp_path, profile)
    assert report["snapshot_temp_copy_used"] is True
    assert report["snapshot_query_only"] is True
    assert report["snapshot_source_hashes_preserved"] is True
    assert sha256(snapshot) == before


def test_non_authoritative_runtime_sqlite_is_rejected(tmp_path: Path) -> None:
    profile, _, _, _ = write_fixture(tmp_path)
    runtime_db = tmp_path / "freqtrade/user_data/tradesv3.paper.sqlite"
    write_sqlite(runtime_db, [sqlite_row()])
    report = build(tmp_path, profile, authoritative_sqlite_path=runtime_db)
    assert report["reason"] == "explicitly_non_authoritative_sqlite_forbidden"
    assert report["snapshot_temp_copy_used"] is False


def test_divergent_replica_is_blocked(tmp_path: Path) -> None:
    profile, _, _, _ = write_fixture(tmp_path, identical_replica=False)
    report = build(tmp_path, profile)
    assert report["reason"] == "source_replica_hash_mismatch"


def test_adapter_no_write_does_not_create_or_modify_sources(tmp_path: Path) -> None:
    profile, primary, replica, snapshot = write_fixture(tmp_path)
    before = {path: path.read_bytes() for path in (profile, primary, replica, snapshot)}
    report = build(tmp_path, profile)
    after = {path: path.read_bytes() for path in (profile, primary, replica, snapshot)}
    assert report["write_performed"] is False
    assert report["write_to_master_performed"] is False
    assert before == after
    assert not (tmp_path / "data/reports").exists()


def test_real_batch_no_write_preserves_csv_master_db_wal_and_shm() -> None:
    profile = ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json"
    paths = [
        ROOT / "data/trades/inbox/freqtrade_paper_closed_trades.csv",
        ROOT / "data/trades/freqtrade_paper_closed_smartcrypto.csv",
        ROOT / "data/trades/trades_master.parquet",
        ROOT / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
        ROOT / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite-wal",
        ROOT / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite-shm",
    ]
    if not all(path.exists() for path in paths):
        pytest.skip("runtime paper evidence is not present in this checkout")
    before = {path: sha256(path) for path in paths}
    report = build(ROOT, profile)
    after = {path: sha256(path) for path in paths}
    assert report["exact_join_count"] == 558
    assert report["snapshot_source_hashes_preserved"] is True
    assert report["write_performed"] is False
    assert before == after


def test_cli_accepts_authoritative_sqlite_override(tmp_path: Path) -> None:
    profile, _, _, snapshot = write_fixture(tmp_path)
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
            "--authoritative-sqlite",
            str(snapshot),
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
    assert payload["exact_join_count"] == 1
    assert payload["research_pipeline_writes_runtime"] is False
    assert payload["sends_exchange_orders"] is False
    assert payload["exchange_private_access"] is False


def test_profile_contains_no_account_identity_or_secret_material() -> None:
    payload = json.loads(
        (ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(payload).casefold()
    assert "account_scope_hash" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
