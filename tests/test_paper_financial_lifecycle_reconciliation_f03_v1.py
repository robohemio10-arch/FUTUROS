from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite import (
    read_authoritative_closed_trades,
)
from smartcrypto.data.trader_master_fingerprint_v2.financial_lifecycle import (
    TradeResult,
    reconcile_financial_lifecycle,
)
from smartcrypto.data.trader_master_fingerprint_v2.freqtrade_adapter import (
    build_freqtrade_paper_closed_trades_adapter_bundle,
    reconcile_and_adapt_records,
)
from smartcrypto.data.trader_master_fingerprint_v2.quarantine_recovery import (
    apply_authoritative_recoveries,
    build_authoritative_recovery_map,
)
from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile
from tests.test_freqtrade_paper_authoritative_batch_closeout_v2 import _forensic_report
from tests.test_freqtrade_paper_closed_trades_adapter_v2 import (
    ACCOUNT_HASH,
    csv_row,
    execution_rows,
    sqlite_row,
    write_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_source_profile(ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json")
EPSILON = Decimal(PROFILE.financial_contract.epsilon_abs_fonte)


def reconcile(trade: dict[str, Any], orders: list[dict[str, Any]] | None = None) -> TradeResult:
    return reconcile_financial_lifecycle(
        [trade], execution_rows([trade]) if orders is None else orders, profile=PROFILE,
    )["trade_results"][0]


def metric(result: TradeResult, name: str) -> Decimal:
    value = result["financial_metrics"][name]
    assert value is not None
    return Decimal(value)


@pytest.mark.parametrize("short", [0, 1])
def test_long_short_conservation_costs_funding_and_decimal(short: int) -> None:
    trade = sqlite_row(is_short=short, close_rate=90 if short else 110)
    result = reconcile(trade)
    assert result["status"] == "accepted"
    assert metric(result, "gross_pnl") == 10
    assert metric(result, "effective_open_fee") == 1
    assert metric(result, "effective_close_fee") == 1
    assert metric(result, "normalized_funding_cost") == 1
    assert metric(result, "reconstructed_net_pnl") == 7


@pytest.mark.parametrize("trade_id", [141, 258, 561, 653, 669])
def test_historical_double_exit_including_cancelled_fill_remains_blocked(trade_id: int) -> None:
    trade = sqlite_row(id=trade_id)
    orders = execution_rows([trade])
    extra = dict(orders[1], id=trade_id * 10 + 2, order_id=f"second-exit-{trade_id}")
    if trade_id in {653, 669}:
        orders[1]["status"] = "canceled"
    if trade_id == 258:
        trade["close_date"] = "2026-06-01 09:59:00"
    orders.append(extra)
    before = copy.deepcopy((trade, orders))
    result = reconcile(trade, orders)
    assert result["status"] == "quarantined"
    assert Decimal(result["exit_quantity"]) == 2
    assert "executed_quantity_imbalance" in result["reasons"]
    if trade_id == 258:
        assert "trade_close_before_open" in result["reasons"]
    assert result["root_cause"] == "ROOT_CAUSE_UNPROVEN"
    assert (trade, orders) == before


@pytest.mark.parametrize("trade_id", [221, 234])
def test_historical_missing_price_only_existing_recovery_is_eligible(trade_id: int) -> None:
    trade = sqlite_row(
        id=trade_id, close_rate=None, leverage=1, fee_open_cost=0,
        fee_close_cost=0, funding_fees=0, close_profit_abs=10, realized_profit=10,
    )
    orders = execution_rows([trade])
    before = copy.deepcopy((trade, orders))
    assert "close_rate_missing_or_invalid" in reconcile(trade, orders)["reasons"]
    recovery = build_authoritative_recovery_map(_forensic_report(), epsilon=EPSILON)
    recovered, metadata = apply_authoritative_recoveries([trade], {trade_id: recovery[trade_id]})
    assert metadata["applied_count"] == 1
    assert reconcile(recovered[0], orders)["status"] == "accepted"
    # Historical recovery does not waive an additional current lifecycle violation.
    orders[1]["order_filled_date"] = "2026-06-01 10:06:00"
    assert reconcile(recovered[0], orders)["status"] == "quarantined"
    orders[1]["order_filled_date"] = before[1][1]["order_filled_date"]
    assert (trade, orders) == before
    assert trade["close_rate"] is None


def test_676_zero_fill_cancel_and_legitimate_retry_are_accepted() -> None:
    trade = sqlite_row(id=676)
    orders = execution_rows([trade])
    orders.append(dict(
        orders[1], id=6762, order_id="cancelled-zero-fill", status="canceled",
        filled=0, remaining=1, average=None, order_filled_date=None,
    ))
    result = reconcile(trade, orders)
    assert result["status"] == "accepted"
    assert result["executed_order_row_ids"] == [6760, 6761]


@pytest.mark.parametrize("short", [0, 1])
def test_cancelled_partial_fill_and_retry_are_both_accounted(short: int) -> None:
    trade = sqlite_row(is_short=short, close_rate=90 if short else 110)
    orders = execution_rows([trade])
    partial = dict(orders[1], status="cancelled", filled="0.4", remaining="0.6")
    retry = dict(orders[1], id=1232, order_id="retry", amount="0.6", filled="0.6")
    result = reconcile(trade, [orders[0], partial, retry])
    assert result["status"] == "accepted"
    assert Decimal(result["exit_quantity"]) == 1


def test_weighted_partial_fills_and_multiple_entries() -> None:
    trade = sqlite_row(open_rate=104, close_rate=116, close_profit_abs=9, realized_profit=9)
    entry, exit_ = execution_rows([trade])
    orders = [
        dict(entry, average=100, amount="0.5", filled="0.5"),
        dict(entry, id=1232, order_id="entry-2", average=108, amount="0.5", filled="0.5"),
        dict(exit_, average=110, amount="0.4", filled="0.4"),
        dict(exit_, id=1233, order_id="exit-2", average=120, amount="0.6", filled="0.6"),
    ]
    assert reconcile(trade, orders)["status"] == "accepted"


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("close_rate", None, "close_rate_missing_or_invalid"),
    ("close_rate", 111, "close_rate_executed_average_mismatch"),
    ("funding_fees", None, "funding_fees_missing_or_invalid"),
    ("fee_open_cost", -1, "fee_open_cost_missing_or_invalid"),
    ("fee_close_cost", None, "fee_close_cost_missing_or_invalid"),
    ("fee_open_currency", "BTC", "fee_open_currency_invalid"),
    ("realized_profit", 6, "realized_close_profit_divergence"),
    ("close_profit_abs", 6, "executed_financial_residual_above_epsilon"),
    ("is_short", "garbage", "trade_side_invalid"),
    ("is_open", 1, "trade_not_proven_closed"),
    ("amount", "NaN", "amount_missing_or_invalid"),
])
def test_trade_failures(field: str, value: object, reason: str) -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    trade[field] = value
    result = reconcile(trade, orders)
    assert result["status"] == "quarantined"
    assert reason in result["reasons"]


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("average", None, "average_missing_or_invalid"),
    ("filled", None, "filled_missing_or_invalid"),
    ("filled", 2, "order_amount_filled_remaining_inconsistent"),
    ("ft_is_open", 1, "closed_trade_has_pending_order"),
    ("status", "open", "closed_trade_has_pending_order"),
    ("status", "unknown", "order_state_invalid"),
    ("ft_pair", "ETH/USDT:USDT", "order_trade_pair_mismatch"),
    ("symbol", "ETH/USDT:USDT", "order_symbol_pair_contradiction"),
    ("ft_order_side", "buy", "order_side_invalid_or_contradictory"),
    ("order_filled_date", None, "order_filled_date_missing_or_invalid"),
    ("order_filled_date", "2026-06-01 10:06:00", "fill_after_trade_close"),
    ("order_filled_date", "2026-06-01 09:59:00", "fill_before_order_submission"),
    ("order_update_date", "2026-06-01 10:04:00", "fill_after_order_update"),
    ("ft_fee_base", "0.001", "base_fee_inventory_semantics_unproven"),
])
def test_execution_failures(field: str, value: object, reason: str) -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    orders[1][field] = value
    result = reconcile(trade, orders)
    assert result["status"] == "quarantined"
    assert reason in result["reasons"]


def test_exact_fill_ordering_never_uses_approximate_timestamps() -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    orders[1]["order_date"] = trade["open_date"]
    orders[1]["order_filled_date"] = trade["open_date"]
    assert "exit_before_proven_entry_inventory" in reconcile(trade, orders)["reasons"]


@pytest.mark.parametrize("kind", ["trade", "order", "exchange", "parent", "malformed"])
def test_ambiguous_identity_blocks_instead_of_collapsing_duplicates(kind: str) -> None:
    trade = sqlite_row()
    trades = [trade]
    orders = execution_rows(trades)
    if kind == "trade":
        trades.append(copy.deepcopy(trade))
    elif kind == "order":
        orders.append(copy.deepcopy(orders[0]))
    elif kind == "exchange":
        orders[1]["order_id"] = orders[0]["order_id"]
    elif kind == "parent":
        orders[1]["ft_trade_id"] = 999
    else:
        orders[0]["id"] = True
    result = reconcile_financial_lifecycle(trades, orders, profile=PROFILE)
    assert result["status"] == "blocked"
    assert result["accepted_count"] == 0
    assert result["structural_errors"]


def test_order_funding_crosscheck_and_epsilon() -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    orders[0]["funding_fee"] = 0
    orders[1]["funding_fee"] = -1
    assert reconcile(trade, orders)["status"] == "accepted"
    orders[1]["funding_fee"] = 0
    assert "order_trade_funding_divergence" in reconcile(trade, orders)["reasons"]
    for error, accepted in ((EPSILON, True), (EPSILON * 2, False)):
        shifted = sqlite_row(close_profit_abs=Decimal(7) + error, realized_profit=Decimal(7) + error)
        assert (reconcile(shifted)["status"] == "accepted") is accepted


def test_deterministic_order_invariance_and_no_mutation() -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    before = copy.deepcopy((trade, orders))
    first = reconcile_financial_lifecycle([trade], orders, profile=PROFILE)
    second = reconcile_financial_lifecycle([trade], list(reversed(orders)), profile=PROFILE)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert (trade, orders) == before
    assert first["write_performed"] is False


def test_adapter_does_not_deliver_quarantined_lifecycle_to_master(tmp_path: Path) -> None:
    profile, primary, replica, snapshot = write_fixture(tmp_path)
    with sqlite3.connect(snapshot) as connection:
        connection.execute("UPDATE orders SET filled=2, amount=2 WHERE side='sell'")
    sources = (profile, primary, replica, snapshot)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    bundle = build_freqtrade_paper_closed_trades_adapter_bundle(
        project_root=tmp_path, source_profile_path=profile, account_scope_hash=ACCOUNT_HASH,
    )
    assert bundle.report["status"] == "blocked"
    assert bundle.accepted_canonical_records == ()
    assert bundle.report["quarantined_row_count"] == 1
    assert bundle.report["runtime_quarantine_status"] == "blocked"
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources] == before


@pytest.mark.parametrize("invalid", ["missing", "corrupt", "missing_orders", "incomplete_orders", "orphan_order"])
def test_sqlite_source_fail_closed(tmp_path: Path, invalid: str) -> None:
    profile, _, _, snapshot = write_fixture(tmp_path)
    if invalid == "missing":
        snapshot.unlink()
    elif invalid == "corrupt":
        snapshot.write_bytes(b"not-a-sqlite-database")
    elif invalid == "orphan_order":
        with sqlite3.connect(snapshot) as connection:
            connection.execute("UPDATE orders SET ft_trade_id=999 WHERE side='sell'")
    else:
        with sqlite3.connect(snapshot) as connection:
            connection.execute("DROP TABLE orders")
            if invalid == "incomplete_orders":
                connection.execute("CREATE TABLE orders (id INTEGER)")
    before = snapshot.read_bytes() if snapshot.exists() else None
    result = read_authoritative_closed_trades(
        project_root=tmp_path, snapshot_path=snapshot,
        profile=load_source_profile(profile), include_orders=True,
    )
    assert result["status"] == "blocked"
    assert (snapshot.read_bytes() if snapshot.exists() else None) == before


def test_missing_orders_and_missing_prices_never_get_defaulted() -> None:
    trade = sqlite_row(close_rate=None, close_rate_requested=110)
    result = reconcile(trade, [])
    assert result["status"] == "quarantined"
    assert "executed_entry_missing" in result["reasons"]
    assert "close_rate_missing_or_invalid" in result["reasons"]
    assert result["weighted_exit_price"] is None


def test_nullable_order_funding_is_unavailable_not_fabricated_zero() -> None:
    trade = sqlite_row()
    orders = execution_rows([trade])
    orders[0]["funding_fee"] = None
    orders[1]["funding_fee"] = -1
    result = reconcile(trade, orders)
    assert result["status"] == "accepted"
    assert result["funding_order_crosscheck"] == "unavailable_authoritative_trade_column_only"
    assert metric(result, "normalized_funding_cost") == 1
    orders[1]["funding_fee"] = "invalid"
    assert "funding_fee_missing_or_invalid" in reconcile(trade, orders)["reasons"]


@pytest.mark.parametrize("short", [0, 1])
def test_stoploss_fill_is_accounted_without_interfering_with_protection(short: int) -> None:
    trade = sqlite_row(is_short=short, close_rate=90 if short else 110)
    orders = execution_rows([trade])
    orders[1]["ft_order_side"] = "stoploss"
    assert reconcile(trade, orders)["status"] == "accepted"


def test_inconsistent_current_row_does_not_disable_proven_healthy_rows() -> None:
    good = sqlite_row(id=676)
    bad = sqlite_row(id=141)
    orders = execution_rows([good, bad])
    orders[-1].update(amount=2, filled=2)
    result = reconcile_financial_lifecycle([good, bad], orders, profile=PROFILE)
    assert result["status"] == "blocked"
    assert result["accepted_count"] == 1
    assert result["quarantined_trade_ids"] == [141]
    assert result["historical_classification_changed"] is False


@pytest.mark.parametrize("failure_call", [1, 2])
def test_unreadable_sqlite_hashing_is_sanitized_and_never_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int,
) -> None:
    import smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite as reader

    profile, _, _, snapshot = write_fixture(tmp_path)
    original = reader.snapshot_artifact_hashes
    calls = 0

    def denied(path: Path, root: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise PermissionError("fixture_sensitive_message")
        return original(path, root)

    monkeypatch.setattr(reader, "snapshot_artifact_hashes", denied)
    result = reader.read_authoritative_closed_trades(
        project_root=tmp_path, snapshot_path=snapshot,
        profile=load_source_profile(profile), include_orders=True,
    )
    assert result["status"] == "blocked"
    assert result["rows"] == []
    assert result.get("orders", []) == []
    assert "fixture_sensitive_message" not in json.dumps(result)


def test_snapshot_changed_during_copy_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite as reader

    profile, _, _, snapshot = write_fixture(tmp_path)
    original = reader._copy_snapshot_artifacts

    def changed(path: Path, temporary: Path) -> Path:
        copied = original(path, temporary)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE trades SET close_rate=999")
        return copied

    monkeypatch.setattr(reader, "_copy_snapshot_artifacts", changed)
    result = reader.read_authoritative_closed_trades(
        project_root=tmp_path, snapshot_path=snapshot,
        profile=load_source_profile(profile), include_orders=True,
    )
    assert result["reason"] == "authoritative_sqlite_source_hash_changed"
    assert result["rows"] == []
    assert result["orders"] == []


@pytest.mark.parametrize("bad_id", ["123.0", True, None, "not-an-id"])
def test_adapter_invalid_sqlite_identity_does_not_raise_or_cast_to_valid_id(bad_id: object) -> None:
    result = reconcile_and_adapt_records(
        [csv_row()], [sqlite_row(id=bad_id)], profile=PROFILE, account_scope_hash=ACCOUNT_HASH,
    )
    assert "invalid_sqlite_trade_ids" in result["structural_errors"]
    assert result["canonical_records"] == []


@pytest.mark.parametrize("short", [0, 1])
def test_funding_revenue_retains_its_authoritative_sign(short: int) -> None:
    trade = sqlite_row(
        is_short=short, close_rate=90 if short else 110, funding_fees=1,
        close_profit_abs=9, realized_profit=9,
    )
    result = reconcile(trade)
    assert result["status"] == "accepted"
    assert metric(result, "normalized_funding_cost") == -1
    assert metric(result, "reconstructed_net_pnl") == 9
