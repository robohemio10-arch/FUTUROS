from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    canonical_json,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
)
from smartcrypto.data.trader_master_fingerprint_v2.staging_runner import (
    build_trader_master_staging_validation_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.staging_validator import (
    KillSwitchMonitor,
    validate_staging_records,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_trader_master_staging_v2.py"
ACCOUNT_HASH = "a" * 64


def valid_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "venue": " Binance ",
        "market_type": " USDT-M FUTURES ",
        "contract_type": " PERPETUAL ",
        "settlement_currency": " USDT ",
        "quantity_unit": " BASE_ASSET ",
        "contract_size": "1.00000000",
        "account_scope_hash": ACCOUNT_HASH.upper(),
        "order_id_namespace": " Binance:Futures:Paper ",
        "source_trade_id": "trade-native-1",
        "order_id": "order-native-1",
        "source": " Trader Export ",
        "symbol": " BTCUSDT ",
        "side": " LONG ",
        "open_time": "2026-06-01T10:00:00-03:00",
        "close_time": "2026-06-01T13:05:00Z",
        "entry_price": "100000.00000000",
        "exit_price": "100100.00000000",
        "quantity": "0.01000000",
        "gross_pnl": "10.00000000",
        "trading_fee": "1.00000000",
        "funding_fee": "2.00000000",
        "net_pnl": "7.00000000",
        "epsilon_abs_fonte": "0.00000000",
    }
    row.update(overrides)
    return row


def validate(rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return validate_staging_records(
        rows,
        source_file="data/staging/sample.csv",
        source_sha256="b" * 64,
        ingestion_run_id="run-test",
        **kwargs,
    )


def fingerprint_in_process(row: dict[str, Any], *, seed: str, timezone: str) -> str:
    code = (
        "import json; "
        "from smartcrypto.data.trader_master_fingerprint_v2.fingerprint_spec "
        "import normalize_trade_row,row_fingerprint_for; "
        f"row=json.loads({json.dumps(json.dumps(row))}); "
        "print(row_fingerprint_for(normalize_trade_row(row)))"
    )
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = seed
    env["TZ"] = timezone
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return completed.stdout.strip()


def test_same_row_is_deterministic_in_three_independent_processes() -> None:
    fingerprints = {
        fingerprint_in_process(valid_row(), seed=seed, timezone=timezone)
        for seed, timezone in (("1", "UTC"), ("7", "America/Sao_Paulo"), ("99", "Asia/Tokyo"))
    }
    assert len(fingerprints) == 1


def test_pythonhashseed_and_local_timezone_do_not_change_fingerprint() -> None:
    first = fingerprint_in_process(valid_row(), seed="2", timezone="UTC")
    second = fingerprint_in_process(valid_row(), seed="123", timezone="Pacific/Honolulu")
    assert first == second


def test_dict_order_does_not_change_fingerprint() -> None:
    row = valid_row()
    reversed_row = dict(reversed(list(row.items())))
    assert row_fingerprint_for(normalize_trade_row(row)) == row_fingerprint_for(
        normalize_trade_row(reversed_row)
    )


@pytest.mark.parametrize("equivalent", ["1", "1.0", "1.000000000", Decimal("1.000")])
def test_equivalent_decimal_representations_have_same_fingerprint(equivalent: object) -> None:
    row = valid_row(contract_size=equivalent, trading_fee=equivalent)
    assert row_fingerprint_for(normalize_trade_row(row)) == row_fingerprint_for(
        normalize_trade_row(valid_row())
    )


def test_optional_null_is_explicit_in_canonical_json() -> None:
    normalized = normalize_trade_row(
        valid_row(order_id=None, source_trade_id=None, order_id_namespace=None)
    )
    rendered = canonical_json(normalized)
    assert '"order_id":null' in rendered
    assert '"source_trade_id":null' in rendered
    assert FINGERPRINT_SPEC_VERSION in rendered


def test_distinct_rows_have_distinct_fingerprints() -> None:
    first = normalize_trade_row(valid_row())
    second = normalize_trade_row(valid_row(symbol="ETHUSDT"))
    assert row_fingerprint_for(first) != row_fingerprint_for(second)


def test_exact_duplicate_is_duplicate_not_collision() -> None:
    report = validate([valid_row(), deepcopy(valid_row())])
    assert report["status"] == "ok"
    assert report["staging_duplicate_count"] == 1
    assert report["duplicate_canonical_trade_id_count"] == 1
    assert report["duplicate_fingerprint_count"] == 1
    assert report["observed_fingerprint_collision_count"] == 0


def test_source_without_order_id_uses_row_fingerprint_fallback() -> None:
    normalized = normalize_trade_row(
        valid_row(order_id=None, source_trade_id=None, order_id_namespace=None)
    )
    fingerprint = row_fingerprint_for(normalized)
    canonical_id = canonical_trade_id_for(normalized, row_fingerprint=fingerprint)
    assert primary_identity_for(normalized) is None
    assert canonical_id.startswith("ctid:v2:")


def test_attempt_to_invent_order_id_is_quarantined() -> None:
    report = validate([valid_row(order_id_generated=True)])
    assert report["status"] == "blocked"
    assert report["quarantined_row_count"] == 1
    assert "invented_native_identifier_forbidden:order_id" in report["validation_errors"]


def test_simulated_sha256_collision_is_fail_closed() -> None:
    report = validate(
        [valid_row(), valid_row(order_id="order-2", source_trade_id="trade-2", symbol="ETHUSDT")],
        hasher=lambda _payload: "0" * 64,
    )
    assert report["status"] == "blocked"
    assert report["observed_fingerprint_collision_count"] == 1
    assert report["quarantined_row_count"] == 2


def test_negative_trading_fee_is_quarantined() -> None:
    report = validate([valid_row(trading_fee="-1", net_pnl="11")])
    assert report["status"] == "blocked"
    assert "trading_fee_negative" in report["validation_errors"]


def test_accounting_identity_outside_tolerance_is_quarantined() -> None:
    report = validate([valid_row(net_pnl="7.1")])
    assert report["status"] == "blocked"
    assert "financial_accounting_identity_violation" in report["validation_errors"]


def test_negative_funding_fee_is_received_revenue() -> None:
    report = validate([valid_row(funding_fee="-2", net_pnl="11")])
    assert report["status"] == "ok"
    assert report["accepted_row_count"] == 1
    assert report["row_results"][0]["accounting_delta"] == "0.00000000"


def test_quarantined_row_is_never_eligible_for_promotion() -> None:
    report = validate([valid_row(net_pnl="999")])
    assert report["accepted_row_count"] == 0
    assert report["quarantined_rows_promoted_to_master"] == 0
    assert report["write_to_master_performed"] is False


def test_canonical_trade_id_includes_namespaced_native_identity() -> None:
    first = normalize_trade_row(valid_row(order_id_namespace="venue:paper:a"))
    second = normalize_trade_row(valid_row(order_id_namespace="venue:paper:b"))
    first_id = canonical_trade_id_for(first, row_fingerprint=row_fingerprint_for(first))
    second_id = canonical_trade_id_for(second, row_fingerprint=row_fingerprint_for(second))
    assert primary_identity_for(first) == {
        "venue": "binance",
        "account_scope_hash": ACCOUNT_HASH,
        "order_id_namespace": "venue:paper:a",
        "native_id_type": "source_trade_id",
        "native_id": "trade-native-1",
    }
    assert first_id != second_id


def test_report_lists_and_rows_are_deterministically_ordered() -> None:
    report = validate([valid_row(net_pnl="999"), valid_row(order_id_generated=True)])
    assert report["validation_errors"] == sorted(report["validation_errors"])
    assert [row["source_row_index"] for row in report["row_results"]] == [0, 1]
    assert all(row["reasons"] == sorted(row["reasons"]) for row in report["row_results"])


def test_kill_switch_before_boot_aborts(tmp_path: Path) -> None:
    kill_switch = tmp_path / "data" / "KILL_SWITCH"
    kill_switch.parent.mkdir(parents=True)
    kill_switch.write_text("stop", encoding="utf-8")
    report = validate([valid_row()], kill_switch=KillSwitchMonitor(kill_switch))
    assert report["status"] == "blocked"
    assert report["reason"] == "kill_switch_active_at_boot"
    assert report["partial_artifact_status"] == "aborted"


def test_kill_switch_during_batch_aborts_gracefully(tmp_path: Path) -> None:
    kill_switch = tmp_path / "data" / "KILL_SWITCH"
    now = [0.0]
    monitor = KillSwitchMonitor(kill_switch, clock=lambda: now[0])

    def activate_after_first_batch(batch_number: int) -> None:
        if batch_number == 0:
            kill_switch.parent.mkdir(parents=True)
            kill_switch.write_text("stop", encoding="utf-8")
            now[0] = 61.0

    report = validate(
        [valid_row(), valid_row(order_id="order-2", source_trade_id="trade-2")],
        batch_size=1,
        kill_switch=monitor,
        batch_hook=activate_after_first_batch,
    )
    assert report["reason"] == "kill_switch_activated_during_processing"
    assert report["processed_row_count"] == 1
    assert report["partial_artifact_status"] == "aborted"


def test_attempted_master_write_is_blocked_without_touching_master(tmp_path: Path) -> None:
    project = tmp_path / "project"
    staging = project / "data" / "staging" / "sample.csv"
    master = project / "data" / "trades" / "trades_master.xlsx"
    staging.parent.mkdir(parents=True)
    master.parent.mkdir(parents=True)
    pd.DataFrame([valid_row()]).to_csv(staging, index=False)
    master.write_bytes(b"master-before")
    report = build_trader_master_staging_validation_report(
        project_root=project,
        staging_file=staging,
        write_to_master_requested=True,
    )
    assert report["reason"] == "write_to_master_forbidden"
    assert report["write_to_master_performed"] is False
    assert master.read_bytes() == b"master-before"


def test_default_runner_is_read_only_and_validates_csv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    staging = project / "data" / "staging" / "sample.csv"
    staging.parent.mkdir(parents=True)
    pd.DataFrame([valid_row()]).to_csv(staging, index=False)
    before = staging.read_bytes()
    report = build_trader_master_staging_validation_report(
        project_root=project,
        staging_file=staging,
    )
    assert report["status"] == "ok"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert staging.read_bytes() == before
    assert not (project / "data" / "reports").exists()


def test_write_report_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    project = tmp_path / "project"
    staging = project / "data" / "staging" / "sample.csv"
    staging.parent.mkdir(parents=True)
    pd.DataFrame([valid_row()]).to_csv(staging, index=False)
    report = build_trader_master_staging_validation_report(
        project_root=project,
        staging_file=staging,
        write_report=True,
    )
    assert report["write_performed"] is True
    assert (project / "data" / "reports" / "trader_master_staging_validator_v2.json").exists()
    assert (project / "data" / "reports" / "trader_master_staging_validator_v2.md").exists()
    assert not (project / "data" / "trades").exists()


def test_missing_source_is_structured_blocked(tmp_path: Path) -> None:
    report = build_trader_master_staging_validation_report(
        project_root=tmp_path,
        staging_file="missing.csv",
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "staging_source_missing"


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--staging-file",
            "missing.csv",
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
    assert payload["reason"] == "staging_source_missing"
    assert payload["write_to_master_performed"] is False


def test_r01_domain_safety_flags_are_preserved() -> None:
    report = validate([valid_row()])
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["sends_exchange_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["research_pipeline_writes_runtime"] is False
    assert report["writes_active_model_runtime"] is False
    assert report["writes_operational_sqlite_outside_freqtrade"] is False
    assert report["changes_risk"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert "writes_runtime" not in report
    assert "order_submission_enabled" not in report
