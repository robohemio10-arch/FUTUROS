from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.learning.paper_autolearning.daily_foundation_runner import (
    build_paper_autolearning_foundation_report,
)
from smartcrypto.learning.paper_autolearning.feedback_store import build_feedback_events
from smartcrypto.learning.paper_autolearning.microbatch_builder import build_daily_microbatch
from smartcrypto.learning.paper_autolearning.outcome_schema import OUTCOME_EVENT_COLUMNS, SAFETY_FLAGS


def closed_trade(
    *,
    order_id: str = "1001",
    pnl: float = 12.5,
    close_time: str = "2026-07-01T12:10:00Z",
    symbol: str = "BTCUSDT",
    side: str = "long",
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "internal_order_id": "",
        "trade_id": f"trade_{order_id}" if order_id else "",
        "moeda": symbol,
        "fechar_side": side,
        "horario_abertura": "2026-07-01T12:00:00Z",
        "horario_fechamento": close_time,
        "preco_abertura": 100.0,
        "preco_fechamento": 101.0 if pnl >= 0 else 99.0,
        "quantity": 1.0,
        "notional": 100.0,
        "pnl_fechado": pnl,
        "taxa_lucros_perdas_fechados_pct": pnl,
        "trading_fee": 0.04,
        "funding_fee": 0.01,
        "leverage": 10,
        "margin_mode": "isolated",
        "liquidation_price": 80.0,
        "exit_reason": "roi" if pnl > 0 else "stop_loss",
    }


def write_closed_trades_csv(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_no_write_default_does_not_mutate_files(tmp_path: Path) -> None:
    write_closed_trades_csv(tmp_path, [closed_trade()])
    report = build_paper_autolearning_foundation_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "feedback" / "outcome_events.parquet").exists()
    assert not (tmp_path / "data" / "reports" / "paper_autolearning_foundation_summary.json").exists()


def test_closed_trade_positive_generates_outcome_event(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade(pnl=1.5)])

    assert result.valid_events[0]["label_win_loss"] == "win"
    assert result.valid_events[0]["label_sign"] == 1
    assert result.valid_events[0]["net_pnl"] == 1.5


def test_closed_trade_negative_generates_outcome_event(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade(pnl=-2.0)])

    assert result.valid_events[0]["label_win_loss"] == "loss"
    assert result.valid_events[0]["label_sign"] == -1


def test_closed_trade_breakeven_generates_outcome_event(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade(pnl=0.0)])

    assert result.valid_events[0]["label_win_loss"] == "breakeven"
    assert result.valid_events[0]["label_sign"] == 0


def test_dedup_order_id_first(tmp_path: Path) -> None:
    rows = [closed_trade(order_id="123", pnl=1.0), closed_trade(order_id="123", pnl=-99.0)]
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=rows)

    assert len(result.new_events) == 1
    assert len(result.duplicate_events) == 1
    assert result.new_events[0]["_dedup_key"] == "order_id::123"


def test_dedup_fallback_row_fingerprint(tmp_path: Path) -> None:
    row = closed_trade(order_id="", pnl=1.0)
    row["trade_id"] = ""
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[row, dict(row)])

    assert len(result.new_events) == 1
    assert len(result.duplicate_events) == 1
    assert result.new_events[0]["_dedup_key"].startswith("row_fingerprint::")


def test_open_trade_is_rejected(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade(close_time="")])

    assert not result.valid_events
    assert result.rejected_rows[0]["validation_errors"] == ["missing_close_time"]


def test_missing_close_time_is_rejected(tmp_path: Path) -> None:
    row = closed_trade()
    row.pop("horario_fechamento")
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[row])

    assert result.rejected_rows[0]["validation_errors"] == ["missing_close_time"]


def test_microbatch_uses_only_closed_trades(tmp_path: Path) -> None:
    result = build_feedback_events(
        project_root=tmp_path,
        closed_trade_rows=[closed_trade(order_id="1"), closed_trade(order_id="2", close_time="")],
    )
    microbatch = build_daily_microbatch(result.valid_events)

    assert microbatch["microbatch_rows"] == 1
    assert microbatch["microbatch"][0]["order_id"] == "1"


def test_microbatch_blocks_future_ret_columns(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade()])
    event = dict(result.valid_events[0], future_ret_1=0.01)
    microbatch = build_daily_microbatch([event])

    assert microbatch["status"] == "blocked"
    assert microbatch["reason"] == "future_ret_columns_detected"
    assert microbatch["lookahead_columns"] == ["future_ret_1"]


def test_microbatch_excludes_outcome_columns_from_features(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade()])
    microbatch = build_daily_microbatch(result.valid_events)

    assert microbatch["microbatch_rows"] == 1
    assert "net_pnl" not in microbatch["feature_columns"]
    assert not any(column.startswith("label_") for column in microbatch["feature_columns"])


def test_futures_perpetual_schema_contains_funding_leverage_margin_liquidation_fields(tmp_path: Path) -> None:
    result = build_feedback_events(project_root=tmp_path, closed_trade_rows=[closed_trade()])
    event = result.valid_events[0]

    for column in ["funding_fee", "leverage", "margin_mode", "liquidation_price"]:
        assert column in OUTCOME_EVENT_COLUMNS
        assert column in event
    assert event["market_type"] == "futures_perpetual"


def test_write_feedback_outputs_only_data_feedback_and_data_reports(tmp_path: Path) -> None:
    write_closed_trades_csv(tmp_path, [closed_trade()])
    report = build_paper_autolearning_foundation_report(project_root=tmp_path, write_feedback=True)

    assert report["write_performed"] is True
    assert report["writes_parquet"] is True
    assert (tmp_path / "data" / "feedback" / "paper_closed_trades_incremental.parquet").exists()
    assert (tmp_path / "data" / "feedback" / "outcome_events.parquet").exists()
    assert (tmp_path / "data" / "reports" / "paper_autolearning_foundation_summary.json").exists()
    assert not (tmp_path / "trades_master.xlsx").exists()


def test_train_smoke_does_not_promote_model(tmp_path: Path) -> None:
    write_closed_trades_csv(tmp_path, [closed_trade(order_id="1", pnl=1.0), closed_trade(order_id="2", pnl=-1.0)])
    report = build_paper_autolearning_foundation_report(
        project_root=tmp_path,
        write_feedback=True,
        train_smoke=True,
    )

    assert report["qlib_challenger_smoke_ran"] is True
    assert report["ai_shadow_challenger_smoke_ran"] is True
    assert report["qlib_challenger_trained"] is False
    assert report["ai_shadow_challenger_trained"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_master_update_is_never_performed_in_foundation(tmp_path: Path) -> None:
    write_closed_trades_csv(tmp_path, [closed_trade()])
    report = build_paper_autolearning_foundation_report(project_root=tmp_path, write_feedback=True)

    assert report["master_update_requested"] is False
    assert report["master_update_performed"] is False
    assert not (tmp_path / "trades_master.xlsx").exists()


def test_safety_flags_preserved(tmp_path: Path) -> None:
    report = build_paper_autolearning_foundation_report(
        project_root=tmp_path,
        closed_trade_rows=[closed_trade()],
    )

    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
        assert report["safety_flags"][key] is expected
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    source = write_closed_trades_csv(tmp_path, [closed_trade()])
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_autolearning_foundation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["closed_trades_loaded_count"] == 1
    assert payload["write_performed"] is False


def test_cli_write_feedback_json_executes(tmp_path: Path) -> None:
    source = write_closed_trades_csv(tmp_path, [closed_trade()])
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_autolearning_foundation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--write-feedback",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["write_performed"] is True
    assert payload["microbatch_rows"] == 1


def test_cli_train_smoke_json_executes(tmp_path: Path) -> None:
    source = write_closed_trades_csv(
        tmp_path,
        [closed_trade(order_id="1", pnl=1.0), closed_trade(order_id="2", pnl=-1.0)],
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_autolearning_foundation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--write-feedback",
            "--train-smoke",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["qlib_challenger_smoke_ran"] is True
    assert payload["ai_shadow_challenger_smoke_ran"] is True
    assert payload["model_promotion_performed"] is False
