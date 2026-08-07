from __future__ import annotations

from pathlib import Path

import pandas as pd

from smartcrypto.data.paper_trade_lifecycle import normalize_closed_trades
from smartcrypto.learning.paper_autolearning.feedback_store import (
    normalize_closed_trade_row,
    write_feedback_outputs,
)
from smartcrypto.learning.paper_autolearning.lineage_reconciliation import (
    build_lineage_reconciliation,
    reconcile_feedback_lineage_files,
)


def freqtrade_closed_frame(*, trade_id: int = 653, exit_reason: str = "roi", pnl: float = 2.00896232) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": trade_id,
                "pair": "ETH/USDT:USDT",
                "is_short": 1,
                "leverage": 2.0,
                "open_date": "2026-08-07 12:10:07.543195",
                "close_date": "2026-08-07 14:05:01.729000",
                "open_rate": 100.0,
                "close_rate": 96.0,
                "amount": 0.5,
                "close_profit_abs": pnl,
                "close_profit": 0.0407552242082023,
                "fee_open_cost": 0.01,
                "fee_close_cost": 0.01,
                "enter_tag": "smartcrypto_short",
                "exit_reason": exit_reason,
            }
        ]
    )


def phase14_source_row(*, trade_id: int = 653, exit_reason: str = "roi", pnl: float = 2.00896232) -> dict[str, object]:
    normalized = normalize_closed_trades(
        freqtrade_closed_frame(trade_id=trade_id, exit_reason=exit_reason, pnl=pnl)
    )
    return dict(normalized.iloc[0].to_dict())


def outcome_from_source(row: dict[str, object]) -> dict[str, object]:
    return normalize_closed_trade_row(
        row,
        source_file="synthetic.csv",
        source_sha256=None,
        ingestion_run_id="test",
        source_row_index=1,
        created_at_utc="2026-08-07T17:00:00+00:00",
    )


def legacy_outcome(row: dict[str, object]) -> dict[str, object]:
    event = outcome_from_source(row)
    event["trade_id"] = ""
    event["exit_reason"] = None
    event["roi_hit"] = False
    event["stoploss_hit"] = False
    event["forced_exit"] = False
    event["liquidation_flag"] = False
    return event


def test_phase14_preserves_native_trade_id_and_exit_reason() -> None:
    normalized = normalize_closed_trades(freqtrade_closed_frame())

    assert normalized.loc[0, "order_id"] == "freqtrade-paper-653"
    assert normalized.loc[0, "trade_id"] == "653"
    assert normalized.loc[0, "exit_reason"] == "roi"


def test_phase14_empty_contract_contains_lineage_columns() -> None:
    normalized = normalize_closed_trades(pd.DataFrame())

    assert "trade_id" in normalized.columns
    assert "exit_reason" in normalized.columns


def test_autolearning_derives_roi_classification_from_phase14() -> None:
    event = outcome_from_source(phase14_source_row(exit_reason="roi"))

    assert event["trade_id"] == "653"
    assert event["exit_reason"] == "roi"
    assert event["roi_hit"] is True
    assert event["stoploss_hit"] is False


def test_autolearning_derives_stoploss_classification_from_phase14() -> None:
    event = outcome_from_source(phase14_source_row(trade_id=651, exit_reason="stop_loss", pnl=-0.84))

    assert event["trade_id"] == "651"
    assert event["exit_reason"] == "stop_loss"
    assert event["roi_hit"] is False
    assert event["stoploss_hit"] is True


def test_incremental_feedback_store_preserves_lineage_and_exit_flags(tmp_path: Path) -> None:
    event = outcome_from_source(phase14_source_row())
    outcome_path = tmp_path / "data" / "feedback" / "outcome_events.parquet"
    feedback_path = tmp_path / "data" / "feedback" / "paper_closed_trades_incremental.parquet"

    result = write_feedback_outputs(
        feedback_store_path=feedback_path,
        outcome_events_path=outcome_path,
        existing_events=[],
        new_events=[event],
    )

    assert result == {"outcome_events_rows": 1, "feedback_rows": 1}
    feedback = pd.read_parquet(feedback_path)
    assert feedback.loc[0, "trade_id"] == "653"
    assert feedback.loc[0, "exit_reason"] == "roi"
    assert bool(feedback.loc[0, "roi_hit"]) is True
    assert bool(feedback.loc[0, "stoploss_hit"]) is False


def test_reconciliation_enriches_legacy_event_without_changing_identity_or_row_count() -> None:
    source = phase14_source_row()
    existing = legacy_outcome(source)
    original_event_id = existing["event_id"]
    original_order_id = existing["order_id"]
    original_fingerprint = existing["row_fingerprint"]

    result = build_lineage_reconciliation(existing_events=[existing], source_rows=[source])

    assert result.status == "ok"
    assert result.update_count == 1
    assert len(result.reconciled_events) == 1
    enriched = result.reconciled_events[0]
    assert enriched["event_id"] == original_event_id
    assert enriched["order_id"] == original_order_id
    assert enriched["row_fingerprint"] == original_fingerprint
    assert enriched["trade_id"] == "653"
    assert enriched["exit_reason"] == "roi"
    assert enriched["roi_hit"] is True
    assert enriched["stoploss_hit"] is False


def test_reconciliation_second_pass_is_idempotent() -> None:
    source = phase14_source_row()
    first = build_lineage_reconciliation(existing_events=[legacy_outcome(source)], source_rows=[source])
    second = build_lineage_reconciliation(existing_events=first.reconciled_events, source_rows=[source])

    assert first.status == "ok"
    assert first.update_count == 1
    assert second.status == "ok"
    assert second.update_count == 0
    assert second.reconciled_events == first.reconciled_events


def test_reconciliation_blocks_trade_id_identity_conflict() -> None:
    source = phase14_source_row()
    existing = legacy_outcome(source)
    existing["trade_id"] = "999"

    result = build_lineage_reconciliation(existing_events=[existing], source_rows=[source])

    assert result.status == "blocked"
    assert result.reason == "economic_or_identity_conflict_detected"
    assert result.update_count == 0
    assert result.reconciled_events == [existing]
    assert any(item["field"] == "trade_id" for item in result.conflicts)


def test_reconciliation_blocks_economic_conflict_fail_closed() -> None:
    source = phase14_source_row(pnl=2.0)
    existing = legacy_outcome(source)
    existing["net_pnl"] = -20.0

    result = build_lineage_reconciliation(existing_events=[existing], source_rows=[source])

    assert result.status == "blocked"
    assert result.update_count == 0
    assert result.reconciled_events == [existing]
    assert any(item["field"] == "net_pnl" for item in result.conflicts)


def test_reconciliation_blocks_duplicate_source_order_identity() -> None:
    source = phase14_source_row()
    existing = legacy_outcome(source)

    result = build_lineage_reconciliation(existing_events=[existing], source_rows=[source, dict(source)])

    assert result.status == "blocked"
    assert result.reason == "duplicate_order_identity_detected"
    assert result.update_count == 0


def test_file_reconciliation_preview_is_read_only(tmp_path: Path) -> None:
    source = phase14_source_row()
    source_path = tmp_path / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([source]).to_csv(source_path, index=False)

    outcome_path = tmp_path / "data" / "feedback" / "outcome_events.parquet"
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([legacy_outcome(source)]).to_parquet(outcome_path, index=False)
    before = outcome_path.read_bytes()

    report = reconcile_feedback_lineage_files(project_root=tmp_path, write=False)

    assert report["status"] == "ok"
    assert report["update_count"] == 1
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert outcome_path.read_bytes() == before
    assert not (tmp_path / "data" / "feedback" / "paper_closed_trades_incremental.parquet").exists()


def test_file_reconciliation_write_is_restricted_and_idempotent_in_tmp_data_feedback(tmp_path: Path) -> None:
    source = phase14_source_row()
    source_path = tmp_path / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([source]).to_csv(source_path, index=False)

    outcome_path = tmp_path / "data" / "feedback" / "outcome_events.parquet"
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([legacy_outcome(source)]).to_parquet(outcome_path, index=False)

    first = reconcile_feedback_lineage_files(project_root=tmp_path, write=True)
    second = reconcile_feedback_lineage_files(project_root=tmp_path, write=True)

    assert first["status"] == "ok"
    assert first["write_performed"] is True
    assert first["update_count"] == 1
    assert first["row_count_invariant"] is True
    assert second["status"] == "ok"
    assert second["write_performed"] is True
    assert second["update_count"] == 0
    outcome = pd.read_parquet(outcome_path)
    feedback = pd.read_parquet(tmp_path / "data" / "feedback" / "paper_closed_trades_incremental.parquet")
    assert len(outcome) == 1
    assert len(feedback) == 1
    assert outcome.loc[0, "trade_id"] == "653"
    assert feedback.loc[0, "trade_id"] == "653"
    assert feedback.loc[0, "exit_reason"] == "roi"


def test_write_outside_data_feedback_is_blocked(tmp_path: Path) -> None:
    source = phase14_source_row()
    source_path = tmp_path / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([source]).to_csv(source_path, index=False)

    outcome_path = tmp_path / "data" / "feedback" / "outcome_events.parquet"
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([legacy_outcome(source)]).to_parquet(outcome_path, index=False)

    report = reconcile_feedback_lineage_files(
        project_root=tmp_path,
        feedback_store_path=tmp_path / "outside.parquet",
        write=True,
    )

    assert report["status"] == "blocked"
    assert report["write_performed"] is False
    assert any("write_path_outside_data_feedback" in blocker for blocker in report["blockers"])
