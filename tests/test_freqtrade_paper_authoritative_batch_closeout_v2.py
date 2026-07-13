from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import scripts.validate_trader_master_staging_v2 as cli_module
import smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter as adapter_module
from smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter import (
    build_freqtrade_paper_closed_trades_adapter_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.quarantine_recovery import (
    RECOVERY_METADATA_KEY,
    RecoveryValidationError,
    apply_authoritative_recoveries,
    build_authoritative_recovery_map,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_trader_master_staging_v2.py"
PROFILE = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"
ACCOUNT_HASH = "c" * 64
EPSILON = Decimal("0.00000001")
HASHES = {"snapshot.sqlite": {"exists": True, "sha256": "d" * 64, "size_bytes": 1}}
TARGET_IDS = {141, 221, 234, 258, 561}
RECOVERED_IDS = {221, 234}


def _forensic_row(trade_id: int, *, recovered: bool, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_id": trade_id,
        "order_id": f"freqtrade-paper-{trade_id}",
        "recovery_decision": (
            "recovered_authoritatively"
            if recovered
            else "remains_quarantined_accounting_unexplained"
        ),
        "recovery_applied": False,
        "weighted_entry_price": "100",
        "weighted_exit_price": "110",
        "verified_open_rate": "100",
        "filled_entry_quantity": "1",
        "filled_exit_quantity": "1" if recovered else "2",
        "amount_inventory": {"amount": "1"},
        "recovered_residual": "0.000000004" if recovered else None,
        "weighted_average_fill_validated": recovered,
        "formula_version": "filled_orders_weighted_average_v1" if recovered else None,
        "evidence_table": ["trades", "orders"],
        "evidence_row_ids": {
            "trades": [trade_id],
            "orders": [trade_id * 10, trade_id * 10 + 1],
            "trade_custom_data": [],
        },
        "source_columns": [
            "orders.id",
            "orders.average",
            "orders.filled",
            "trades.amount",
            "trades.open_rate",
        ],
        "reported_profit": {
            "realized_profit": "10",
            "close_profit_abs": "10",
            "values_match": True,
        },
        "remaining_blockers": [] if recovered else ["filled_order_quantity_mismatch"],
        "close_rate_requested_used": False,
    }
    row.update(overrides)
    return row


def _forensic_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "ok",
        "reason": "targeted_quarantine_forensics_completed",
        "recovery_applied": False,
        "snapshot_source_hashes_before": copy.deepcopy(HASHES),
        "trade_results": [
            _forensic_row(trade_id, recovered=trade_id in RECOVERED_IDS)
            for trade_id in sorted(TARGET_IDS)
        ],
    }
    report.update(overrides)
    return report


def _profile_payload() -> dict[str, Any]:
    payload = json.loads(PROFILE.read_text(encoding="utf-8"))
    payload["profile_id"] = "test_authoritative_batch_closeout_v2"
    return payload


def _csv_row(trade_id: int) -> dict[str, Any]:
    blocked = trade_id in {141, 258, 561}
    missing_exit = trade_id in RECOVERED_IDS
    return {
        "moeda": "BTCUSDT",
        "fechar_side": "long",
        "leverage": 1,
        "order_id": f"freqtrade-paper-{trade_id}",
        "horario_abertura": "2026-06-01 10:00:00.000000",
        "horario_fechamento": "2026-06-01 10:05:00.000000",
        "preco_abertura": 100,
        "preco_fechamento": None if missing_exit else 110,
        "volume_fechado": 1,
        "pnl_fechado": 20 if blocked else 10,
        "taxa_1": 0,
        "taxa_2": 0,
    }


def _sqlite_row(trade_id: int) -> dict[str, Any]:
    blocked = trade_id in {141, 258, 561}
    missing_exit = trade_id in RECOVERED_IDS
    return {
        "id": trade_id,
        "exchange": "binance",
        "pair": "BTC/USDT:USDT",
        "is_open": 0,
        "is_short": 0,
        "open_rate": 100,
        "close_rate": None if missing_exit else 110,
        "amount": 1,
        "contract_size": 1,
        "leverage": 1,
        "fee_open_cost": 0,
        "fee_close_cost": 0,
        "fee_open_currency": "USDT",
        "fee_close_currency": "USDT",
        "funding_fees": 0,
        "close_profit_abs": 20 if blocked else 10,
        "realized_profit": 20 if blocked else 10,
        "open_date": "2026-06-01 10:00:00.000000",
        "close_date": "2026-06-01 10:05:00.000000",
    }


def _frozen_558_ids() -> list[int]:
    ids = list(range(1, 562))
    ids.remove(557)
    ids.remove(558)
    ids.remove(559)
    assert len(ids) == 558
    return ids


def _install_adapter_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    profile = tmp_path / "config" / "profile.json"
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica = tmp_path / "data/trades/freqtrade_paper_closed_smartcrypto.csv"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    profile.parent.mkdir(parents=True)
    primary.parent.mkdir(parents=True)
    replica.parent.mkdir(parents=True, exist_ok=True)
    snapshot.parent.mkdir(parents=True)
    profile.write_text(json.dumps(_profile_payload()), encoding="utf-8")
    primary.write_text("frozen-fixture\n", encoding="utf-8")
    replica.write_bytes(primary.read_bytes())
    snapshot.write_bytes(b"fixture")
    ids = _frozen_558_ids()
    frame = pd.DataFrame([_csv_row(trade_id) for trade_id in ids])
    sqlite_rows = [_sqlite_row(trade_id) for trade_id in ids]

    monkeypatch.setattr(adapter_module, "read_trade_file", lambda _: frame.copy(deep=True))

    def fake_reader(**_: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "reason": "authoritative_sqlite_read_ok",
            "rows": copy.deepcopy(sqlite_rows),
            "snapshot_source_hashes_before": copy.deepcopy(HASHES),
            "snapshot_source_hashes_after": copy.deepcopy(HASHES),
            "snapshot_source_hashes_preserved": True,
            "snapshot_temp_copy_used": True,
            "snapshot_query_only": True,
        }

    monkeypatch.setattr(adapter_module, "read_authoritative_closed_trades", fake_reader)
    monkeypatch.setattr(
        adapter_module,
        "build_targeted_quarantine_forensics_report",
        lambda **_: copy.deepcopy(_forensic_report()),
    )
    return profile


def _build(tmp_path: Path, profile: Path, *, recovery: bool) -> dict[str, Any]:
    return build_freqtrade_paper_closed_trades_adapter_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        apply_authoritative_forensic_recovery=recovery,
    )


def test_default_false_keeps_frozen_batch_553_accepted_5_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=False)
    assert report["accepted_row_count"] == 553
    assert report["quarantined_row_count"] == 5
    assert report["authoritative_forensic_recovery_executed"] is False


def test_default_false_never_calls_forensics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)

    def forbidden_forensics(**_: Any) -> dict[str, Any]:
        raise AssertionError("forensics must be opt-in")

    monkeypatch.setattr(
        adapter_module,
        "build_targeted_quarantine_forensics_report",
        forbidden_forensics,
    )
    report = _build(tmp_path, profile, recovery=False)
    assert report["accepted_row_count"] == 553


def test_recovery_true_closes_frozen_batch_555_accepted_3_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    assert report["pre_forensic_accepted_row_count"] == 553
    assert report["pre_forensic_quarantined_row_count"] == 5
    assert report["accepted_row_count"] == 555
    assert report["quarantined_row_count"] == 3
    assert report["batch_closeout_status"] == "completed_with_quarantine"


def test_only_221_and_234_receive_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    applied = {
        row["sqlite_trade_id"]
        for row in report["adapter_row_results"]
        if row["forensic_recovery_applied"]
    }
    assert applied == RECOVERED_IDS


def test_141_258_and_561_remain_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    assert report["remaining_quarantined_order_ids"] == [
        "freqtrade-paper-141",
        "freqtrade-paper-258",
        "freqtrade-paper-561",
    ]


def test_recovery_map_and_application_do_not_mutate_inputs() -> None:
    report = _forensic_report()
    report_before = copy.deepcopy(report)
    recovery_map = build_authoritative_recovery_map(report, epsilon=EPSILON)
    sqlite_rows = [_sqlite_row(221), _sqlite_row(234)]
    rows_before = copy.deepcopy(sqlite_rows)
    output, application = apply_authoritative_recoveries(sqlite_rows, recovery_map)
    assert report == report_before
    assert sqlite_rows == rows_before
    assert output[0]["close_rate"] == "110"
    assert output[0][RECOVERY_METADATA_KEY]["original_close_rate"] is None
    assert application["source_records_mutated"] is False


def test_recovery_map_is_immutable() -> None:
    recovery_map = build_authoritative_recovery_map(_forensic_report(), epsilon=EPSILON)
    with pytest.raises(TypeError):
        recovery_map[221] = recovery_map[221]  # type: ignore[index]


def test_id_outside_fixed_allowlist_is_rejected() -> None:
    report = _forensic_report()
    report["trade_results"].append(_forensic_row(999, recovered=True))
    with pytest.raises(RecoveryValidationError, match="not_allowed"):
        build_authoritative_recovery_map(report, epsilon=EPSILON)


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"weighted_exit_price": None}, "weighted_exit_price_invalid"),
        ({"weighted_entry_price": None}, "weighted_entry_price_invalid"),
        ({"recovered_residual": "0.01"}, "recovered_residual_above_epsilon"),
        ({"verified_open_rate": "101"}, "weighted_entry_open_rate_mismatch"),
        ({"filled_exit_quantity": "0.5"}, "filled_entry_exit_quantity_mismatch"),
        ({"close_rate_requested_used": True}, "close_rate_requested_used"),
        ({"source_columns": ["orders.id"]}, "required_source_columns_missing"),
        ({"evidence_table": ["trades"]}, "required_evidence_tables_missing"),
        ({"remaining_blockers": ["still_blocked"]}, "remaining_blockers_present"),
    ],
)
def test_numeric_and_lineage_gates_reject_invalid_candidate(
    overrides: dict[str, Any], expected_error: str
) -> None:
    report = _forensic_report()
    report["trade_results"] = [
        _forensic_row(221, recovered=True, **overrides),
        _forensic_row(234, recovered=True),
    ]
    assessment = adapter_module.assess_authoritative_recovery_map(report, epsilon=EPSILON)
    assert 221 not in assessment.recoveries
    assert expected_error in assessment.rejected_reasons[221]


def test_recovered_text_without_numeric_gates_is_rejected() -> None:
    report = _forensic_report(
        trade_results=[
            {
                "trade_id": 221,
                "order_id": "freqtrade-paper-221",
                "recovery_decision": "recovered_authoritatively",
            },
            _forensic_row(234, recovered=True),
        ]
    )
    recovery_map = build_authoritative_recovery_map(report, epsilon=EPSILON)
    assert set(recovery_map) == {234}


def test_realized_profit_must_match_close_profit_abs() -> None:
    report = _forensic_report()
    report["trade_results"][1]["reported_profit"] = {
        "realized_profit": "9",
        "close_profit_abs": "10",
        "values_match": True,
    }
    recovery_map = build_authoritative_recovery_map(report, epsilon=EPSILON)
    assert 221 not in recovery_map


def test_hash_mismatch_blocks_all_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _forensic_report(snapshot_source_hashes_before={"different": True})
    monkeypatch.setattr(
        adapter_module,
        "build_targeted_quarantine_forensics_report",
        lambda **_: report,
    )
    result = _build(tmp_path, profile, recovery=True)
    assert result["forensic_snapshot_hash_match"] is False
    assert result["forensic_recovery_applied_count"] == 0
    assert result["accepted_row_count"] == 553


def test_source_row_changed_since_forensics_rejects_application() -> None:
    recovery_map = build_authoritative_recovery_map(_forensic_report(), epsilon=EPSILON)
    rows = [_sqlite_row(221) | {"open_rate": 101}]
    output, application = apply_authoritative_recoveries(rows, {221: recovery_map[221]})
    assert RECOVERY_METADATA_KEY not in output[0]
    assert application["rejected_trade_ids"] == [221]


def test_recovery_uses_only_recovered_close_rate() -> None:
    recovery_map = build_authoritative_recovery_map(_forensic_report(), epsilon=EPSILON)
    source = _sqlite_row(221)
    output, _ = apply_authoritative_recoveries([source], {221: recovery_map[221]})
    changed = {key for key in source if source[key] != output[0][key]}
    assert changed == {"close_rate"}


def test_recovered_rows_are_accepted_by_validator_and_have_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    rows = {
        row["order_id"]: row
        for row in report["row_results"]
        if row["order_id"] in {"freqtrade-paper-221", "freqtrade-paper-234"}
    }
    assert {row["status"] for row in rows.values()} == {"accepted"}
    assert all(row["row_fingerprint"] for row in rows.values())


def test_fingerprints_are_deterministic_after_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    first = _build(tmp_path, profile, recovery=True)
    second = _build(tmp_path, profile, recovery=True)
    first_fp = {row["order_id"]: row["row_fingerprint"] for row in first["row_results"]}
    second_fp = {row["order_id"]: row["row_fingerprint"] for row in second["row_results"]}
    assert first_fp == second_fp


def test_report_contains_complete_recovery_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    rows = {
        row["order_id"]: row
        for row in report["adapter_row_results"]
        if row["order_id"] in {"freqtrade-paper-221", "freqtrade-paper-234"}
    }
    for row in rows.values():
        assert row["forensic_recovery_candidate"] is True
        assert row["forensic_recovery_applied"] is True
        assert row["recovery_source"] == "authoritative_orders_average_filled_v1"
        assert row["recovery_formula_version"] == "filled_orders_weighted_average_v1"
        assert row["forensic_evidence_tables"] == ["orders", "trades"]
        assert row["close_rate_requested_used"] is False


def test_three_unexplained_rows_include_forensic_blockers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    rows = {
        row["sqlite_trade_id"]: row
        for row in report["adapter_row_results"]
        if row["sqlite_trade_id"] in {141, 258, 561}
    }
    assert all(row["remains_quarantined_after_forensics"] for row in rows.values())
    assert all(row["forensic_recovery_candidate"] is False for row in rows.values())
    assert all(row["forensic_remaining_blockers"] for row in rows.values())


def test_default_is_no_write_and_recovery_never_writes_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = _build(tmp_path, profile, recovery=True)
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert report["write_performed"] is False
    assert report["recovery_writes_performed"] is False
    assert before == after


def test_write_report_writes_only_allowed_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = build_freqtrade_paper_closed_trades_adapter_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        apply_authoritative_forensic_recovery=True,
        write_report=True,
        output_json="data/reports/closeout.json",
        output_markdown="data/reports/closeout.md",
    )
    assert report["write_performed"] is True
    assert (tmp_path / "data/reports/closeout.json").exists()
    assert (tmp_path / "data/reports/closeout.md").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.xlsx"))


def test_write_to_master_remains_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = build_freqtrade_paper_closed_trades_adapter_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        apply_authoritative_forensic_recovery=True,
        write_to_master_requested=True,
    )
    assert report["reason"] == "write_to_master_forbidden"
    assert report["write_to_master_performed"] is False


def test_cli_exposes_opt_in_flag_without_pythonpath() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "--apply-authoritative-forensic-recovery" in completed.stdout


def test_cli_flag_is_wired_to_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    received: dict[str, Any] = {}

    def fake_builder(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {"status": "blocked", "accepted_row_count": 555, "quarantined_row_count": 3}

    monkeypatch.setattr(cli_module, "build_freqtrade_paper_closed_trades_adapter_report", fake_builder)
    exit_code = cli_module.main(
        [
            "--project-root",
            ".",
            "--source-profile",
            "profile.json",
            "--account-scope-hash",
            ACCOUNT_HASH,
            "--apply-authoritative-forensic-recovery",
            "--no-write",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert received["apply_authoritative_forensic_recovery"] is True
    assert payload["accepted_row_count"] == 555
    assert payload["quarantined_row_count"] == 3


def test_real_no_write_probe_closes_only_fixed_recoveries_when_sources_exist() -> None:
    required = [
        ROOT / "data/trades/inbox/freqtrade_paper_closed_trades.csv",
        ROOT / "data/trades/freqtrade_paper_closed_smartcrypto.csv",
        ROOT / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
    ]
    if not all(path.exists() for path in required):
        pytest.skip("authoritative paper evidence is not available")
    before = {path: path.read_bytes() for path in required}
    report = build_freqtrade_paper_closed_trades_adapter_report(
        project_root=ROOT,
        source_profile_path=PROFILE,
        account_scope_hash=ACCOUNT_HASH,
        apply_authoritative_forensic_recovery=True,
    )
    after = {path: path.read_bytes() for path in required}
    assert report["accepted_row_count"] == report["pre_forensic_accepted_row_count"] + 2
    assert report["quarantined_row_count"] == 3
    assert report["forensic_recovered_order_ids"] == [
        "freqtrade-paper-221",
        "freqtrade-paper-234",
    ]
    assert report["observed_fingerprint_collision_count"] == 0
    assert report["snapshot_source_hashes_preserved"] is True
    assert before == after


def test_safety_flags_remain_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _install_adapter_fixture(monkeypatch, tmp_path)
    report = _build(tmp_path, profile, recovery=True)
    assert report["write_to_master_performed"] is False
    assert report["research_pipeline_writes_runtime"] is False
    assert report["sends_exchange_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["recovery_changes_fingerprint_spec"] is False
    assert report["recovery_changes_epsilon"] is False
