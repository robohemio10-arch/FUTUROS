from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite as reader
import smartcrypto.data.trader_master_fingerprint_v2.population_evidence as population
from tests.test_freqtrade_paper_closed_trades_adapter_v2 import (
    ACCOUNT_HASH,
    profile_payload,
    sqlite_row,
    write_sqlite,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_paper_financial_population_f04.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(snapshot: Path) -> dict[str, str | None]:
    return {
        role: sha(Path(f"{snapshot}{suffix}")) if Path(f"{snapshot}{suffix}").exists() else None
        for role, suffix in population.ARTIFACT_SUFFIXES.items()
    }


def fixture_source(
    tmp_path: Path, *, mixed: bool = False, opened: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    profile = tmp_path / "profile.json"
    snapshot = tmp_path / "explicit-paper.sqlite"
    authorization = tmp_path / "authorization.json"
    profile.write_text(json.dumps(profile_payload()), encoding="utf-8")
    rows = [sqlite_row(id=676), sqlite_row(id=677, is_short=1, close_rate=90)]
    if mixed:
        rows.append(sqlite_row(id=141, close_profit_abs=1234, realized_profit=1234))
    closed_ids = sorted(row["id"] for row in rows)
    if opened:
        rows.append(sqlite_row(id=900, is_open=1, close_rate=None, close_date=None))
    write_sqlite(snapshot, rows)
    if mixed:
        with sqlite3.connect(snapshot) as connection:
            connection.execute("UPDATE orders SET filled=2, amount=2 WHERE ft_trade_id=141 AND side='sell'")
    payload = {
        "schema_version": population.AUTHORIZATION_SCHEMA,
        "read_authorized": True,
        "sqlite_path": str(snapshot.resolve()),
        "source_profile_sha256": sha(profile),
        "source_id": "isolated-fixture-source-v1",
        "account_scope_hash": ACCOUNT_HASH,
        "paper_scope": "CONTROL_A",
        "epoch_id": "fixture-epoch-1",
        "cohort_id": "fixture-natural-cohort",
        "closed_trade_ids": closed_ids,
        "artifact_sha256": artifacts(snapshot),
    }
    authorization.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return profile, snapshot, authorization, payload


def audit(tmp_path: Path, fixture: tuple[Path, Path, Path, dict[str, Any]]) -> dict[str, Any]:
    profile, snapshot, authorization, _ = fixture
    return population.audit_paper_financial_population(
        project_root=tmp_path, sqlite_path=snapshot,
        source_profile_path=profile, authorization_path=authorization,
    )


def reseal(fixture: tuple[Path, Path, Path, dict[str, Any]]) -> None:
    profile, snapshot, authorization, payload = fixture
    payload.update(artifact_sha256=artifacts(snapshot), source_profile_sha256=sha(profile))
    authorization.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_complete_closed_population_and_deterministic_evidence(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.iterdir()}
    result = audit(tmp_path, fixture)
    assert result == audit(tmp_path, fixture)
    assert result["status"] == "OK"
    analysis = result["analysis"]
    assert analysis["partition_counts"] == {"accepted": 2, "quarantined": 0, "open_not_audited": 0}
    assert analysis["source_total_trade_count"] == 2
    assert analysis["analyzed_closed_trade_count"] == 2
    assert analysis["closed_reconciliation_coverage_pct"] == "100"
    assert analysis["whole_source_fully_reconciled"] is True
    assert Decimal(analysis["reconciled_subset_net_pnl_usdt"]) == Decimal(14)
    assert analysis["quarantined_reported_known_net_pnl_usdt_NOT_CERTIFIED"] is None
    assert analysis["economic_readiness"] == "NOT_ASSESSED"
    assert analysis["profitability_inferred"] is False
    assert result["write_performed"] is False
    assert result["runtime_parity_certified"] is False
    assert {str(path): path.read_bytes() for path in tmp_path.iterdir()} == before
    assert result["source"]["snapshot_query_only"] is True
    assert result["source"]["snapshot_copy_hashes_verified"] is True
    assert result["source"]["artifact_hashes_before"] == result["source"]["artifact_hashes_after"]
    evidence_hash = result.pop("evidence_sha256")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(encoded.encode()).hexdigest() == evidence_hash


def test_quarantine_never_contributes_to_audited_pnl_and_groups_conserve_total(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path, mixed=True, opened=True)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "FINANCIAL_QUARANTINES_PRESENT"
    analysis = result["analysis"]
    assert sum(analysis["partition_counts"].values()) == analysis["source_total_trade_count"] == 4
    assert analysis["partition_counts"] == {"accepted": 2, "quarantined": 1, "open_not_audited": 1}
    assert analysis["analyzed_closed_trade_count"] == 3
    assert analysis["quarantined_trade_ids"] == [141]
    assert analysis["accepted_trade_ids"] == [676, 677]
    assert Decimal(analysis["reconciled_subset_net_pnl_usdt"]) == 14
    assert Decimal(analysis["quarantined_reported_known_net_pnl_usdt_NOT_CERTIFIED"]) == 1234
    assert analysis["quarantined_pnl_included_in_reconciled_sum"] is False
    assert analysis["whole_source_fully_reconciled"] is False
    assert analysis["whole_source_reconciliation_coverage_pct"] == "50"
    assert "executed_quantity_imbalance" in analysis["quarantine_reason_counts_nonexclusive"]


def test_open_trades_are_not_closed_financial_evidence(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path, opened=True)
    result = audit(tmp_path, fixture)
    assert result["status"] == "OK"
    assert result["analysis"]["partition_counts"]["open_not_audited"] == 1
    assert result["analysis"]["whole_source_fully_reconciled"] is False
    assert result["analysis"]["economic_readiness"] == "NOT_ASSESSED"


@pytest.mark.parametrize(("field", "value"), [
    ("read_authorized", False), ("paper_scope", "A+B"), ("paper_scope", ["CONTROL_A", "TREATMENT_B"]),
    ("epoch_id", ["epoch-1", "epoch-2"]), ("cohort_id", ""), ("source_id", ""),
    ("account_scope_hash", "invalid"), ("schema_version", "old"),
    ("sqlite_path", "relative.sqlite"), ("source_profile_sha256", "0" * 64),
    ("closed_trade_ids", [676, 676]), ("closed_trade_ids", [True]),
    ("artifact_sha256", {"db": "0" * 64, "wal": None}),
])
def test_invalid_authorization_never_reads_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    fixture = fixture_source(tmp_path)
    fixture[3][field] = value
    fixture[2].write_text(json.dumps(fixture[3]), encoding="utf-8")

    def forbidden(**_: object) -> None:
        raise AssertionError("SQLite must not be queried without valid explicit authorization")

    monkeypatch.setattr(population, "read_authoritative_closed_trades", forbidden)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None


@pytest.mark.parametrize("ids", [[676], [676, 677, 999]])
def test_partial_or_missing_cohort_membership_blocks_all_metrics(tmp_path: Path, ids: list[int]) -> None:
    fixture = fixture_source(tmp_path)
    fixture[3]["closed_trade_ids"] = ids
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["reason"] == "CLOSED_COHORT_MEMBERSHIP_MISMATCH"
    assert result["analysis"] is None


@pytest.mark.parametrize("field", ["paper_scope", "epoch_id", "cohort_id", "source_id", "account_scope_hash"])
def test_observed_scope_contradictions_cannot_be_overridden_by_declaration(tmp_path: Path, field: str) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        # Column identifiers are selected solely from this test's constant parameter list.
        connection.execute(f'ALTER TABLE trades ADD COLUMN "{field}" TEXT')
        connection.execute(f'UPDATE trades SET "{field}"=? WHERE id=676', (fixture[3][field],))
        connection.execute(f'UPDATE trades SET "{field}"=? WHERE id=677', ("different-scope",))
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["reason"] == "SOURCE_SCOPE_CONTRADICTION"
    assert result["analysis"] is None


def test_matching_explicit_scope_columns_are_accepted(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("ALTER TABLE trades ADD COLUMN paper_scope TEXT")
        connection.execute("UPDATE trades SET paper_scope='CONTROL_A'")
    reseal(fixture)
    assert audit(tmp_path, fixture)["status"] == "OK"


@pytest.mark.parametrize("defect", ["orders_missing", "schema_incomplete", "invalid_state", "orphan", "duplicate_order"])
def test_incomplete_or_ambiguous_source_suppresses_financial_metrics(tmp_path: Path, defect: str) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        if defect == "orders_missing":
            connection.execute("DROP TABLE orders")
        elif defect == "schema_incomplete":
            connection.execute("ALTER TABLE trades RENAME COLUMN close_profit_abs TO missing_pnl")
        elif defect == "invalid_state":
            connection.execute("UPDATE trades SET is_open=2 WHERE id=676")
        elif defect == "orphan":
            connection.execute("UPDATE orders SET ft_trade_id=999 WHERE id=6760")
        else:
            connection.execute("INSERT INTO orders SELECT * FROM orders WHERE id=6760")
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None


def test_missing_authorized_source_does_not_fall_back_to_another_snapshot(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    prior = tmp_path / "old-snapshot.sqlite"
    fixture[1].rename(prior)
    before = sha(prior)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None
    assert sha(prior) == before
    assert not fixture[1].exists()


@pytest.mark.parametrize("role", ["db", "wal", "shm"])
def test_declared_artifact_hash_mismatch_is_blocked(tmp_path: Path, role: str) -> None:
    fixture = fixture_source(tmp_path)
    fixture[3]["artifact_sha256"][role] = "0" * 64
    fixture[2].write_text(json.dumps(fixture[3]), encoding="utf-8")
    assert audit(tmp_path, fixture)["reason"] == "SOURCE_HASH_MISMATCH"


def test_real_wal_shm_fixture_is_read_without_source_mutation(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    connection = sqlite3.connect(fixture[1])
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("UPDATE trades SET close_profit_abs=7 WHERE id=676")
        connection.commit()
        reseal(fixture)
        before = artifacts(fixture[1])
        assert before["wal"] is not None and before["shm"] is not None
        assert audit(tmp_path, fixture)["status"] == "OK"
        assert artifacts(fixture[1]) == before
    finally:
        connection.close()


@pytest.mark.parametrize("drift", ["db", "authorization", "profile"])
def test_drift_during_analysis_discards_previously_computed_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    fixture = fixture_source(tmp_path)
    original = population.reconcile_financial_lifecycle

    def changed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if drift == "db":
            with sqlite3.connect(fixture[1]) as connection:
                connection.execute("UPDATE trades SET close_profit_abs=999 WHERE id=676")
        elif drift == "authorization":
            fixture[2].write_text("{}", encoding="utf-8")
        else:
            fixture[0].write_bytes(fixture[0].read_bytes() + b"\n")
        return result

    monkeypatch.setattr(population, "reconcile_financial_lifecycle", changed)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None
    assert "DRIFT" in result["reason"]


def test_torn_copy_is_detected_even_if_original_hashes_remain_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_source(tmp_path)
    original = reader._copy_snapshot_artifacts

    def changed(source: Path, temporary: Path) -> Path:
        copied = original(source, temporary)
        with sqlite3.connect(copied) as connection:
            connection.execute("UPDATE trades SET close_profit_abs=999 WHERE id=676")
        return copied

    monkeypatch.setattr(reader, "_copy_snapshot_artifacts", changed)
    before = artifacts(fixture[1])
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None
    assert artifacts(fixture[1]) == before


@pytest.mark.parametrize("trade_id", [221, 234])
def test_f04_never_invents_new_or_automatic_historical_recovery(tmp_path: Path, trade_id: int) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("UPDATE trades SET id=?, close_rate=NULL WHERE id=676", (trade_id,))
        connection.execute("UPDATE orders SET ft_trade_id=? WHERE ft_trade_id=676", (trade_id,))
    fixture[3]["closed_trade_ids"] = [trade_id, 677]
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["analysis"]["quarantined_trade_ids"] == [trade_id]
    assert Decimal(result["analysis"]["reconciled_subset_net_pnl_usdt"]) == 7
    assert result["recovery_applied"] is False
    with sqlite3.connect(fixture[1]) as connection:
        assert connection.execute("SELECT close_rate FROM trades WHERE id=?", (trade_id,)).fetchone()[0] is None


def test_unavailable_quarantined_pnl_is_not_filled_with_zero(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path, mixed=True)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("UPDATE trades SET close_profit_abs=NULL WHERE id=141")
    reseal(fixture)
    analysis = audit(tmp_path, fixture)["analysis"]
    assert analysis["quarantined_reported_known_net_pnl_usdt_NOT_CERTIFIED"] is None
    assert analysis["quarantined_reported_pnl_unavailable_count"] == 1
    assert Decimal(analysis["reconciled_subset_net_pnl_usdt"]) == 14


def test_cli_stdout_only_and_explicit_inputs_required(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.iterdir()}
    command = [sys.executable, "-B", str(SCRIPT), "--project-root", str(tmp_path),
               "--sqlite-path", str(fixture[1]), "--source-profile", str(fixture[0]),
               "--source-authorization", str(fixture[2])]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["write_performed"] is False
    assert completed.stderr == ""
    assert {str(path): path.read_bytes() for path in tmp_path.iterdir()} == before
    missing = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True,
                             text=True, check=False, timeout=30)
    assert missing.returncode == 2
    assert "required" in missing.stderr
    blocked = subprocess.run(command[:-1] + [str(tmp_path / "missing-auth.json")],
                             capture_output=True, text=True, check=False, timeout=30)
    assert blocked.returncode == 2
    assert json.loads(blocked.stdout)["analysis"] is None


def test_decimal_sum_keeps_small_values_and_signed_losses() -> None:
    assert population._sum_pnl([Decimal("9" * 50), Decimal("0.00000001")]) == "9" * 50 + ".00000001"
    assert population._sum_pnl([Decimal("7"), Decimal("7"), Decimal("-13")]) == "1"
    assert population._sum_pnl([Decimal("7"), Decimal("-7")]) == "0"
    assert population._sum_pnl([]) is None


def test_all_quarantined_is_not_zero_certified_pnl(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("UPDATE trades SET close_rate=NULL")
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"]["reconciled_subset_trade_count"] == 0
    assert result["analysis"]["reconciled_subset_net_pnl_usdt"] is None
    assert result["analysis"]["partition_counts"]["quarantined"] == 2


def test_empty_closed_population_is_blocked(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("UPDATE trades SET is_open=1")
    fixture[3]["closed_trade_ids"] = []
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["reason"] == "CLOSED_POPULATION_EMPTY"
    assert result["analysis"] is None


@pytest.mark.parametrize("kind", ["corrupt", "duplicate_keys", "oversized", "relative"])
def test_invalid_input_is_blocked_without_fallback(tmp_path: Path, kind: str) -> None:
    fixture = fixture_source(tmp_path)
    if kind == "corrupt":
        fixture[1].write_bytes(b"not-a-sqlite-database")
        reseal(fixture)
    elif kind == "duplicate_keys":
        raw = fixture[2].read_text(encoding="utf-8")
        fixture[2].write_text(raw[:-1] + ', "read_authorized":true}', encoding="utf-8")
    elif kind == "oversized":
        fixture[2].write_bytes(b" " * 1_048_577)
    else:
        fixture = (fixture[0], Path("relative.sqlite"), fixture[2], fixture[3])
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None


def test_incoherent_time_interval_is_not_reported_as_complete(tmp_path: Path) -> None:
    fixture = fixture_source(tmp_path)
    with sqlite3.connect(fixture[1]) as connection:
        connection.execute("UPDATE trades SET close_date='2026-06-01 09:59:00' WHERE id=676")
    reseal(fixture)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"]["time_interval"]["start_utc"] is None
    assert result["analysis"]["time_interval"]["end_utc"] is None
    assert result["analysis"]["time_interval"]["invalid_timestamp_trade_count"] == 1


def test_permission_error_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_source(tmp_path)

    def denied(*_: Any, **__: Any) -> Any:
        raise PermissionError("fixture_private_message_must_not_leak")

    monkeypatch.setattr(population, "read_authoritative_closed_trades", denied)
    result = audit(tmp_path, fixture)
    assert result["status"] == "BLOCKED"
    assert result["analysis"] is None
    assert result["error_type"] == "PermissionError"
    assert "fixture_private_message_must_not_leak" not in json.dumps(result)


def test_profile_swap_before_loader_cannot_override_authorized_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_source(tmp_path)
    original = population.load_source_profile

    def changed(path: Path) -> Any:
        fixture[0].write_bytes(fixture[0].read_bytes() + b"\n")
        return original(path)

    monkeypatch.setattr(population, "load_source_profile", changed)
    result = audit(tmp_path, fixture)
    assert result["reason"] == "SOURCE_PROFILE_DRIFT"
    assert result["analysis"] is None


def test_dangling_sidecar_symlink_is_rejected_before_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_source(tmp_path)
    sidecar = Path(f"{fixture[1]}-wal")
    try:
        sidecar.symlink_to(tmp_path / "missing-fixture-target")
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")

    def forbidden(**_: object) -> None:
        raise AssertionError("linked source must not be queried")

    monkeypatch.setattr(population, "read_authoritative_closed_trades", forbidden)
    assert audit(tmp_path, fixture)["reason"] == "SOURCE_LINK_FORBIDDEN"
