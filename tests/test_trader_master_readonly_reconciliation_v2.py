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
from smartcrypto.data.trader_master_fingerprint_v2.master_reconciliation import (
    SAFETY_FLAGS,
    build_trader_master_reconciliation_report,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile_trader_master_preview_v2.py"
PROFILE = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"
ACCOUNT_HASH = "c" * 64


def canonical_trade(order_id: str = "freqtrade-paper-1", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "venue": "binance",
        "market_type": "usdt-m_futures",
        "contract_type": "linear_perpetual",
        "settlement_currency": "USDT",
        "quantity_unit": "base_asset",
        "contract_size": "1",
        "account_scope_hash": ACCOUNT_HASH,
        "order_id_namespace": "freqtrade:paper:sqlite:trades.id:v1",
        "source_trade_id": None,
        "order_id": order_id,
        "source": "phase14_freqtrade_paper_closed_trades",
        "symbol": "BTCUSDT",
        "side": "long",
        "open_time": "2026-06-01T10:00:00Z",
        "close_time": "2026-06-01T10:05:00Z",
        "entry_price": "100",
        "exit_price": "110",
        "quantity": "1",
        "gross_pnl": "10",
        "trading_fee": "1",
        "funding_fee": "1",
        "net_pnl": "8",
        "epsilon_abs_fonte": "0.00000001",
    }
    row.update(overrides)
    return row


def prepare_project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / PROFILE.name).write_bytes(PROFILE.read_bytes())
    primary = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    replica = tmp_path / "data/trades/freqtrade_paper_closed_smartcrypto.csv"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    primary.parent.mkdir(parents=True)
    replica.parent.mkdir(parents=True, exist_ok=True)
    snapshot.parent.mkdir(parents=True)
    primary.write_text("fixture\n", encoding="utf-8")
    replica.write_bytes(primary.read_bytes())
    snapshot.write_bytes(b"sqlite-fixture")
    Path(f"{snapshot}-wal").write_bytes(b"")
    Path(f"{snapshot}-shm").write_bytes(b"shm")
    return tmp_path / "config" / PROFILE.name


def write_master(root: Path, rows: list[dict[str, Any]], name: str = "trades_master.parquet") -> Path:
    path = root / "data" / "trades" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(canonical_trade())
    frame = pd.DataFrame(rows, columns=columns) if not rows else pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    return path


def fake_bundle(
    incoming: list[dict[str, Any]],
    *,
    quarantined_ids: list[str] | None = None,
    raw_count: int | None = None,
) -> FreqtradePaperAdapterBundle:
    quarantined = quarantined_ids or []
    report = {
        "status": "blocked" if quarantined else "ok",
        "reason": "rows_quarantined_after_authoritative_reconciliation" if quarantined else "ok",
        "source_profile_id": "phase14_freqtrade_paper_closed_trades_v2",
        "source_file": "data/trades/inbox/freqtrade_paper_closed_trades.csv",
        "primary_source_sha256": "a" * 64,
        "snapshot_source_hashes_before": {"snapshot": {"sha256": "b" * 64}},
        "snapshot_source_hashes_after": {"snapshot": {"sha256": "b" * 64}},
        "snapshot_source_hashes_preserved": True,
        "source_status": "ok",
        "structural_errors": [],
        "raw_row_count": raw_count if raw_count is not None else len(incoming) + len(quarantined),
        "accepted_row_count": len(incoming),
        "quarantined_row_count": len(quarantined),
        "quarantined_order_ids": quarantined,
        "forensic_recovery_applied_count": 2,
        "blockers": [],
    }
    identity = {
        "source_profile_id": report["source_profile_id"],
        "paper_source_path": report["source_file"],
        "paper_source_hash": report["primary_source_sha256"],
        "raw_row_count": report["raw_row_count"],
        "accepted_row_count": len(incoming),
        "quarantined_row_count": len(quarantined),
        "quarantined_order_ids": quarantined,
        "forensic_recovery_applied_count": 2,
    }
    return FreqtradePaperAdapterBundle(
        report=report,
        accepted_canonical_records=tuple(incoming),
        quarantined_row_summaries=tuple(
            {"order_id": order_id, "status": "quarantined"} for order_id in quarantined
        ),
        batch_identity=identity,
    )


def preview(
    tmp_path: Path,
    incoming: list[dict[str, Any]],
    master_rows: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    profile = prepare_project(tmp_path)
    master = write_master(tmp_path, master_rows)
    bundle = kwargs.pop("bundle", fake_bundle(incoming))
    return build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        authoritative_sqlite_path="data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
        trader_master_path=master,
        adapter_builder=lambda **_: bundle,
        generated_at_utc="2026-07-13T00:00:00+00:00",
        **kwargs,
    )


def preview_raw_master(
    tmp_path: Path,
    incoming: list[dict[str, Any]],
    master_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = prepare_project(tmp_path)
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(master_rows).to_parquet(master, index=False)
    return build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        adapter_builder=lambda **_: fake_bundle(incoming),
        generated_at_utc="2026-07-13T00:00:00+00:00",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_paper_batch_is_derived_with_recovery_true(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    master = write_master(tmp_path, [])
    calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> FreqtradePaperAdapterBundle:
        calls.append(kwargs)
        return fake_bundle([canonical_trade()])

    build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        adapter_builder=builder,
    )
    assert calls[0]["apply_authoritative_forensic_recovery"] is True


def test_paper_batch_count_is_not_fixed_to_557(tmp_path: Path) -> None:
    rows = [canonical_trade(f"freqtrade-paper-{index}") for index in range(1, 4)]
    report = preview(tmp_path, rows, [], bundle=fake_bundle(rows, raw_count=6))
    assert report["paper_raw_row_count"] == 6
    assert report["paper_accepted_row_count"] == 3


def test_quarantined_rows_do_not_enter_reconciliation(tmp_path: Path) -> None:
    incoming = [canonical_trade()]
    quarantine = ["freqtrade-paper-141", "freqtrade-paper-258", "freqtrade-paper-561"]
    report = preview(tmp_path, incoming, [], bundle=fake_bundle(incoming, quarantined_ids=quarantine))
    assert len(report["reconciliation_results"]) == 1
    assert report["paper_quarantined_order_ids"] == quarantine


def test_paper_source_change_during_read_blocks(tmp_path: Path) -> None:
    calls = 0

    def snapshotter(paths: Any, root: Path) -> dict[str, dict[str, Any]]:
        nonlocal calls
        calls += 1
        return {"batch": {"sha256": "a" * 64 if calls == 1 else "b" * 64}}

    report = preview(tmp_path, [canonical_trade()], [], artifact_snapshotter=snapshotter)
    assert report["reason"] == "paper_batch_changed_during_reconciliation"


def test_missing_master_blocks(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path="data/trades/trades_master.parquet",
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["reason"] == "trader_master_missing"


def test_master_symlink_blocks(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    target = write_master(tmp_path, [])
    link = target.with_name("linked_master.parquet")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=link,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["reason"] == "trader_master_symlink_forbidden"


def test_master_outside_project_root_blocks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profile = prepare_project(root)
    outside = write_master(tmp_path, [], name="outside.parquet")
    report = build_trader_master_reconciliation_report(
        project_root=root,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=outside,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["reason"] == "trader_master_outside_project_root"


def test_master_non_parquet_extension_blocks(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    bad = tmp_path / "data/trades/trades_master.xlsx"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"fixture")
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=bad,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["reason"] == "trader_master_extension_invalid"


def test_master_temp_copy_is_used(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["trader_master_temp_copy_used"] is True


def test_master_hash_is_preserved(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["trader_master_sha256_before"] == report["trader_master_sha256_after"]
    assert report["trader_master_hash_preserved"] is True


def test_master_change_during_read_blocks(tmp_path: Path) -> None:
    report = preview(
        tmp_path,
        [canonical_trade()],
        [],
        after_master_read_hook=lambda path: path.write_bytes(path.read_bytes() + b"changed"),
    )
    assert report["reason"] == "trader_master_changed_during_reconciliation"


def test_no_xlsx_fallback_occurs(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    xlsx = tmp_path / "data/trades/trades_master.xlsx"
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    xlsx.write_bytes(b"not-used")
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["reason"] == "trader_master_missing"


def test_legacy_writers_are_not_called() -> None:
    source = (ROOT / "smartcrypto/data/trader_master_fingerprint_v2/master_reconciliation.py").read_text()
    assert "write_master(" not in source
    assert "import_trades_incrementally(" not in source
    assert "archive_files(" not in source


def test_legacy_build_dedup_key_is_not_authority() -> None:
    source = (ROOT / "smartcrypto/data/trader_master_fingerprint_v2/master_reconciliation.py").read_text()
    assert "build_dedup_key" not in source


def test_exact_fingerprint_duplicate_is_classified(tmp_path: Path) -> None:
    row = canonical_trade()
    report = preview(tmp_path, [row], [row])
    assert report["exact_fingerprint_duplicate_count"] == 1


def test_primary_identity_financial_conflict_is_classified(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade(net_pnl="7")], [canonical_trade()])
    assert report["primary_identity_financial_conflict_count"] == 1
    assert report["decision"] == "BLOCKED_BY_MASTER_IDENTITY_CONFLICTS"


def test_same_fingerprint_different_canonical_json_blocks(tmp_path: Path) -> None:
    def constant_hasher(_: bytes) -> str:
        return "f" * 64

    report = preview(
        tmp_path,
        [canonical_trade("freqtrade-paper-2", symbol="ETHUSDT")],
        [canonical_trade()],
        row_hasher=constant_hasher,
    )
    assert report["observed_fingerprint_collision_count"] == 1
    assert report["decision"] == "BLOCKED_BY_FINGERPRINT_COLLISION"


def test_duplicate_master_primary_identity_blocks(tmp_path: Path) -> None:
    rows = [canonical_trade(), canonical_trade(net_pnl="7")]
    report = preview(tmp_path, [canonical_trade()], rows)
    assert report["duplicate_master_primary_identity_count"] == 1
    assert report["decision"] == "BLOCKED_BY_MASTER_IDENTITY_CONFLICTS"


def test_identical_duplicate_master_fingerprint_is_reported(tmp_path: Path) -> None:
    row = canonical_trade()
    report = preview(tmp_path, [row], [row, row])
    assert report["duplicate_master_fingerprint_count"] == 1


def test_ambiguous_legacy_match_is_not_duplicate(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"order_id": "freqtrade-paper-1", "moeda": "BTCUSDT"}]).to_parquet(
        master, index=False
    )
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["ambiguous_legacy_identity_match_count"] == 1
    assert report["exact_fingerprint_duplicate_count"] == 0


def test_new_trade_candidate_is_classified(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["new_trade_candidate_count"] == 1
    assert report["decision"] == "READY_FOR_CONTROLLED_IMPORT_REVIEW"


def test_master_unverifiable_row_is_preserved(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    master = tmp_path / "data/trades/trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"order_id": "legacy-1", "pnl_fechado": "1 USDT"}]).to_parquet(
        master, index=False
    )
    report = build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert report["master_unverifiable_row_count"] == 1
    assert report["master_unverifiable_rows"][0]["classification"] == "master_row_unverifiable"


def test_incoming_unverifiable_row_blocks(tmp_path: Path) -> None:
    bad = canonical_trade(account_scope_hash=None)
    report = preview(tmp_path, [bad], [])
    assert report["incoming_row_unverifiable_count"] == 1
    assert report["decision"] == "BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS"


def test_financial_field_diff_is_deterministic(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade(net_pnl="7")], [canonical_trade()])
    assert report["conflict_results"][0]["financial_diff"] == [
        {
            "field": "net_pnl",
            "incoming_normalized_value": "7.00000000",
            "master_normalized_value": "8.00000000",
            "absolute_numeric_delta": "1.00000000",
            "material_conflict": True,
        }
    ]


def test_projected_row_count_uses_only_new_candidates(tmp_path: Path) -> None:
    incoming = [canonical_trade(), canonical_trade("freqtrade-paper-2")]
    report = preview(tmp_path, incoming, [canonical_trade()])
    assert report["projected_master_row_count_after_hypothetical_import"] == 2


def test_default_report_does_not_write(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports").exists()


def test_write_report_is_limited_to_data_reports(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [], write_report=True)
    assert report["write_performed"] is True
    files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*" ) if path.is_file())
    assert "data/reports/trader_master_readonly_reconciliation_v2.json" in files
    assert "data/reports/trader_master_readonly_reconciliation_v2.md" in files


def test_fingerprint_spec_is_not_modified_by_preview(tmp_path: Path) -> None:
    spec = ROOT / "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
    before = spec.read_bytes()
    preview(tmp_path, [canonical_trade()], [])
    assert spec.read_bytes() == before


def test_input_files_are_not_mutated(tmp_path: Path) -> None:
    profile = prepare_project(tmp_path)
    master = write_master(tmp_path, [])
    paths = [profile, master, tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"]
    before = {path: sha256(path) for path in paths}
    build_trader_master_reconciliation_report(
        project_root=tmp_path,
        source_profile_path=profile,
        account_scope_hash=ACCOUNT_HASH,
        trader_master_path=master,
        adapter_builder=lambda **_: fake_bundle([canonical_trade()]),
    )
    assert {path: sha256(path) for path in paths} == before


def test_cli_runs_without_pythonpath(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--source-profile",
            "missing.json",
            "--no-write",
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["status"] == "blocked"


def test_output_is_deterministic_except_generated_at(tmp_path: Path) -> None:
    first = preview(tmp_path / "first", [canonical_trade()], [])
    second = preview(tmp_path / "second", [canonical_trade()], [])
    for payload in (first, second):
        payload.pop("project_root", None)
        payload.pop("output_paths", None)
        payload.pop("generated_at_utc", None)
    assert first == second


def test_decision_never_executes_import(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["decision"] == "READY_FOR_CONTROLLED_IMPORT_REVIEW"
    assert report["import_requested"] is False
    assert report["import_performed"] is False


def test_safety_flags_disable_all_writers_orders_and_runtime(tmp_path: Path) -> None:
    report = preview(tmp_path, [canonical_trade()], [])
    assert report["safety_flags"] == SAFETY_FLAGS
    assert report["writes_trader_master"] is False
    assert report["writes_parquet"] is False
    assert report["writes_xlsx"] is False
    assert report["writes_csv"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_runtime"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_all_unverifiable_master_rows_zero_new_candidate_count(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1", "moeda": "BTCUSDT"}],
    )
    assert report["master_valid_fingerprint_row_count"] == 0
    assert report["master_unverifiable_row_count"] == 1
    assert report["new_trade_candidate_count"] == 0


def test_unmatched_incoming_is_blocked_by_unverifiable_master(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1", "moeda": "BTCUSDT"}],
    )
    assert report["incoming_blocked_by_unverifiable_master_count"] == 1
    assert report["reconciliation_results"][0]["classification"] == (
        "incoming_blocked_by_unverifiable_master"
    )


def test_unverifiable_master_sets_projected_count_to_none(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1"}],
    )
    assert report["projected_master_row_count_after_hypothetical_import"] is None


def test_unverifiable_master_marks_projection_not_calculable(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1"}],
    )
    assert report["projected_master_row_count_calculable"] is False


def test_blocked_incoming_is_never_import_eligible(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1"}],
    )
    assert report["reconciliation_results"][0]["import_eligible"] is False


def test_partial_verifiable_master_with_related_legacy_row_does_not_release(
    tmp_path: Path,
) -> None:
    incoming = canonical_trade("freqtrade-paper-2")
    report = preview_raw_master(
        tmp_path,
        [incoming],
        [canonical_trade("freqtrade-paper-1"), {"order_id": "freqtrade-paper-2"}],
    )
    assert report["master_valid_fingerprint_row_count"] == 1
    assert report["master_unverifiable_row_count"] == 1
    assert report["ambiguous_legacy_identity_match_count"] == 1
    assert report["new_trade_candidate_count"] == 0


def test_fully_verifiable_master_allows_new_candidate(tmp_path: Path) -> None:
    report = preview(
        tmp_path,
        [canonical_trade("freqtrade-paper-2")],
        [canonical_trade("freqtrade-paper-1")],
    )
    assert report["master_unverifiable_row_count"] == 0
    assert report["new_trade_candidate_count"] == 1
    assert report["reconciliation_results"][0]["import_eligible"] is True


def test_unverifiable_master_decision_remains_blocked(tmp_path: Path) -> None:
    report = preview_raw_master(
        tmp_path,
        [canonical_trade()],
        [{"order_id": "legacy-1"}],
    )
    assert report["decision"] == "BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS"
    assert report["status"] == "ok"
