from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from scripts import build_ai_shadow_threshold_evaluation_input as builder_cli
from smartcrypto.ml.ai_shadow_financial_evaluation import evaluate_ai_shadow_financial_thresholds
from smartcrypto.ml.ai_shadow_threshold_input_builder import build_ai_shadow_threshold_evaluation_input


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows), encoding="utf-8")
    return path


def write_parquet(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def decisions_rows() -> list[dict]:
    return [
        {
            "order_id": "100",
            "trade_id": "wrong_trade",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T10:00:00Z",
            "probability_win": 0.82,
            "decision": "AI_ACCEPT",
            "model_id": "shadow",
            "model_version": "v1",
        },
        {
            "trade_id": "t-200",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time_utc": "2026-06-05T10:10:00Z",
            "confidence": 0.44,
            "decision": "AI_REJECT",
        },
        {
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T10:20:00Z",
            "score": 0.67,
            "decision": "SHADOW_ENTRY",
        },
        {
            "symbol": "ETHUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T10:30:00Z",
            "score": 0.51,
            "decision": "SHADOW_SKIP",
        },
    ]


def outcome_rows() -> list[dict]:
    return [
        {
            "order_id": "100",
            "trade_id": "different",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T10:59:00Z",
            "pnl_fechado": 5.0,
            "target_profitable": 1,
        },
        {
            "trade_id": "t-200",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time_utc": "2026-06-05T10:11:00Z",
            "pnl_fechado": -2.0,
            "target_profitable": 0,
        },
        {
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T10:21:00Z",
            "pnl_fechado": 1.5,
            "target_profitable": 1,
        },
        {
            "symbol": "ETHUSDT",
            "side": "long",
            "open_time_utc": "2026-06-05T12:30:00Z",
            "pnl_fechado": -9.0,
            "target_profitable": 0,
        },
    ]


def build(tmp_path: Path, *, decisions: list[dict] | None = None, outcomes: list[dict] | None = None, max_minutes: float = 15) -> tuple[dict, pd.DataFrame]:
    decisions_path = write_jsonl(tmp_path / "decisions.jsonl", decisions if decisions is not None else decisions_rows())
    outcomes_path = write_parquet(tmp_path / "outcomes.parquet", outcomes if outcomes is not None else outcome_rows())
    output = tmp_path / "threshold_input.parquet"
    report = build_ai_shadow_threshold_evaluation_input(
        decisions=decisions_path,
        outcomes=outcomes_path,
        output=output,
        report=tmp_path / "report.json",
        max_time_delta_minutes=max_minutes,
    )
    frame = pd.read_parquet(output) if output.exists() else pd.DataFrame()
    return report, frame


def write_sqlite_decisions(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        pd.DataFrame(rows).to_sql("ai_shadow_decisions", conn, index=False)
    return path


def embedded_decision_rows() -> list[dict]:
    return [
        {
            "trade_id": "embedded-1",
            "open_time_utc": "2026-06-05T10:00:00Z",
            "symbol": "BTCUSDT",
            "side": "long",
            "ai_score": 0.91,
            "ai_decision": "AI_ACCEPT",
            "raw_pnl_usdt": -50.0,
            "base_policy_pnl_usdt": 25.0,
            "shadow_filtered_pnl_usdt": 3.5,
            "sends_order": 0,
            "changes_risk": 0,
        },
        {
            "trade_id": "embedded-2",
            "open_time_utc": "2026-06-05T10:05:00Z",
            "symbol": "ETHUSDT",
            "side": "short",
            "ai_score": 0.31,
            "ai_decision": "AI_REJECT",
            "raw_pnl_usdt": 10.0,
            "base_policy_pnl_usdt": -2.0,
            "shadow_filtered_pnl_usdt": -1.25,
            "sends_order": 0,
            "changes_risk": 0,
        },
    ]


def test_builder_outputs_required_threshold_columns(tmp_path: Path) -> None:
    report, frame = build(tmp_path)

    assert report["status"] == "warning"
    assert {"matched", "probability_or_confidence", "decision"}.issubset(frame.columns)
    assert frame["matched"].dtype == bool
    assert pd.api.types.is_numeric_dtype(frame["probability_or_confidence"])
    assert frame["decision"].astype(str).str.len().min() > 0


def test_builder_matches_by_order_id_first(tmp_path: Path) -> None:
    report, frame = build(tmp_path)
    row = frame.loc[frame["order_id"].astype(str).eq("100")].iloc[0]

    assert row["matched"] is True or bool(row["matched"]) is True
    assert row["match_method"] == "order_id"
    assert row["pnl_fechado"] == 5.0


def test_builder_matches_by_trade_id_second(tmp_path: Path) -> None:
    report, frame = build(tmp_path)
    row = frame.loc[frame["trade_id"].astype(str).eq("t-200")].iloc[0]

    assert bool(row["matched"]) is True
    assert row["match_method"] == "trade_id"


def test_builder_matches_by_symbol_side_time_window(tmp_path: Path) -> None:
    report, frame = build(tmp_path)
    row = frame.loc[frame["decision"].eq("SHADOW_ENTRY")].iloc[0]

    assert bool(row["matched"]) is True
    assert row["match_method"] == "symbol_side_time_window"
    assert row["match_confidence"] > 0


def test_builder_does_not_match_outside_time_window(tmp_path: Path) -> None:
    report, frame = build(tmp_path, max_minutes=15)
    row = frame.loc[frame["decision"].eq("SHADOW_SKIP")].iloc[0]

    assert bool(row["matched"]) is False
    assert row["match_method"] == "unmatched"


def test_builder_blocks_missing_probability_without_faking_values(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key not in {"probability_win", "confidence", "score"}} for row in decisions_rows()]
    report, frame = build(tmp_path, decisions=rows)

    assert report["status"] == "blocked"
    assert "missing_probability" in report["reason"]
    assert frame.empty


def test_builder_blocks_missing_decision_without_faking_values(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key != "decision"} for row in decisions_rows()]
    report, frame = build(tmp_path, decisions=rows)

    assert report["status"] == "blocked"
    assert "missing_decision" in report["reason"]
    assert frame.empty


def test_builder_marks_unmatched_rows_transparently(tmp_path: Path) -> None:
    report, frame = build(tmp_path)

    assert report["unmatched_rows"] == 1
    assert report["missing_outcome_rows"] == 1
    assert frame.loc[frame["match_method"].eq("unmatched"), "matched"].eq(False).all()


def test_builder_reads_sqlite_decisions_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "decisions.sqlite"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(decisions_rows()).to_sql("ai_shadow_decisions", conn, index=False)
    before = db_path.read_bytes()
    outcomes_path = write_parquet(tmp_path / "outcomes.parquet", outcome_rows())
    output = tmp_path / "threshold_input.parquet"

    report = build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        outcomes=outcomes_path,
        output=output,
        report=tmp_path / "report.json",
    )

    assert report["input_decisions_rows"] == 4
    assert output.exists()
    assert db_path.read_bytes() == before


def test_builder_uses_embedded_sqlite_outcome_when_external_match_absent(tmp_path: Path) -> None:
    db_path = write_sqlite_decisions(tmp_path / "decisions.sqlite", embedded_decision_rows())
    output = tmp_path / "threshold_input.parquet"

    report = build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        output=output,
        report=tmp_path / "report.json",
    )
    frame = pd.read_parquet(output)

    assert report["status"] == "ok"
    assert report["matched_rows"] == 2
    assert report["embedded_matched_rows"] == 2
    assert frame["matched"].eq(True).all()
    assert frame["match_method"].eq("embedded_decision_outcome").all()
    assert frame["match_confidence"].eq(1.0).all()
    assert frame["source_outcome_path"].eq(str(db_path)).all()


def test_builder_prefers_shadow_filtered_pnl_for_embedded_outcome(tmp_path: Path) -> None:
    db_path = write_sqlite_decisions(tmp_path / "decisions.sqlite", embedded_decision_rows())
    output = tmp_path / "threshold_input.parquet"

    report = build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        output=output,
        report=tmp_path / "report.json",
    )
    frame = pd.read_parquet(output)
    row = frame.loc[frame["trade_id"].eq("embedded-1")].iloc[0]

    assert report["embedded_outcome_column_used"] == "shadow_filtered_pnl_usdt"
    assert row["pnl_usdt"] == 3.5
    assert row["pnl_fechado"] == 3.5


def test_builder_derives_target_profitable_from_embedded_pnl(tmp_path: Path) -> None:
    db_path = write_sqlite_decisions(tmp_path / "decisions.sqlite", embedded_decision_rows())
    output = tmp_path / "threshold_input.parquet"

    build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        output=output,
        report=tmp_path / "report.json",
    )
    frame = pd.read_parquet(output).set_index("trade_id")

    assert frame.loc["embedded-1", "target_profitable"] == 1
    assert frame.loc["embedded-2", "target_profitable"] == 0


def test_builder_reports_embedded_and_external_match_counts(tmp_path: Path) -> None:
    db_path = write_sqlite_decisions(tmp_path / "decisions.sqlite", embedded_decision_rows())
    outcomes_path = write_parquet(
        tmp_path / "outcomes.parquet",
        [
            {
                "trade_id": "embedded-1",
                "symbol": "BTCUSDT",
                "side": "long",
                "open_time_utc": "2026-06-05T10:00:00Z",
                "pnl_fechado": 8.0,
                "target_profitable": 1,
            }
        ],
    )

    report = build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        outcomes=outcomes_path,
        output=tmp_path / "threshold_input.parquet",
        report=tmp_path / "report.json",
    )

    assert report["external_matched_rows"] == 1
    assert report["embedded_outcome_rows"] == 2
    assert report["embedded_matched_rows"] == 1
    assert report["unmatched_reason_counts"] == {}


def test_builder_does_not_fake_embedded_outcome_when_financial_columns_missing(tmp_path: Path) -> None:
    rows = [
        {
            "trade_id": "no-outcome",
            "open_time_utc": "2026-06-05T10:00:00Z",
            "symbol": "BTCUSDT",
            "side": "long",
            "ai_score": 0.72,
            "ai_decision": "AI_ACCEPT",
        }
    ]
    db_path = write_sqlite_decisions(tmp_path / "decisions.sqlite", rows)
    output = tmp_path / "threshold_input.parquet"

    report = build_ai_shadow_threshold_evaluation_input(
        sqlite_decisions=db_path,
        output=output,
        report=tmp_path / "report.json",
    )
    frame = pd.read_parquet(output)

    assert report["status"] == "warning"
    assert report["matched_rows"] == 0
    assert report["embedded_outcome_rows"] == 0
    assert report["embedded_matched_rows"] == 0
    assert report["unmatched_reason_counts"] == {"missing_embedded_outcome": 1}
    assert bool(frame.iloc[0]["matched"]) is False
    assert pd.isna(frame.iloc[0]["pnl_usdt"])


def test_builder_report_contains_counts_hashes_and_safety_flags(tmp_path: Path) -> None:
    report, frame = build(tmp_path)

    assert report["input_decisions_rows"] == 4
    assert report["input_outcomes_rows"] == 4
    assert report["output_rows"] == 4
    assert report["matched_rows"] == 3
    assert report["output_hash"]
    assert report["source_hashes"]
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False


def test_cli_build_ai_shadow_threshold_evaluation_input_runs_successfully(tmp_path: Path, capsys) -> None:
    decisions_path = write_jsonl(tmp_path / "decisions.jsonl", decisions_rows())
    outcomes_path = write_parquet(tmp_path / "outcomes.parquet", outcome_rows())
    output_path = tmp_path / "threshold_input.parquet"
    rc = builder_cli.main(
        [
            "--decisions",
            str(decisions_path),
            "--outcomes",
            str(outcomes_path),
            "--output",
            str(output_path),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["output_rows"] == 4
    assert output_path.exists()


def test_financial_threshold_evaluation_accepts_builder_output(tmp_path: Path) -> None:
    report, frame = build(tmp_path)
    evaluation = evaluate_ai_shadow_financial_thresholds(
        input_path=tmp_path / "threshold_input.parquet",
        report_path=tmp_path / "evaluation.json",
        min_samples=1,
    )

    assert evaluation["status"] == "ok"
    assert evaluation["probability_column"] == "probability_or_confidence"
    assert evaluation["decision_column"] == "decision"


def test_does_not_touch_freqtrade_db_models_registry_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in protected}

    build(tmp_path / "builder")

    assert {path: path.read_text(encoding="utf-8") for path in protected} == before


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked = [
        Path("smartcrypto/ml/ai_shadow_threshold_input_builder.py"),
        Path("scripts/build_ai_shadow_threshold_evaluation_input.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
