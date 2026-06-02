from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.model_decision_logger import log_ai_shadow_model_decisions, read_jsonl
from smartcrypto.ml.outcome_tracker import track_ai_shadow_outcomes


ROOT = Path(__file__).resolve().parents[1]


def load_cli(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def trainer_report() -> dict:
    return {
        "model_id": "ai_shadow_incremental_logistic_regression",
        "model_version": "shadow_v1",
        "promotion_status": "pending",
        "status": "ok",
        "feature_columns": ["feature_close", "feature_volume"],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def decision_rows() -> list[dict]:
    return [
        {
            "decision_id": "decision-1",
            "correlation_id": "order-1",
            "order_id": "order-1",
            "symbol": "BTCUSDT",
            "side": "long",
            "prediction": 1,
            "probability": 0.72,
            "confidence": 0.44,
            "threshold": 0.6,
            "action_shadow": "SHADOW_ENTRY",
            "reason": "probability_above_threshold",
            "feature_columns": ["feature_close", "feature_volume"],
            "feature_close": 100.0,
            "feature_volume": 12.0,
            "open_time_utc": "2026-01-01T00:00:00Z",
        }
    ]


def feedback_rows() -> list[dict]:
    return [
        {
            "order_id": "order-1",
            "moeda": "BTCUSDT",
            "fechar_side": "long",
            "horario_abertura": "2026-01-01T00:01:00Z",
            "horario_fechamento": "2026-01-01T00:05:00Z",
            "pnl_fechado": 2.5,
            "taxa_lucros_perdas_fechados_pct": 0.02,
        }
    ]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_decision_logger_blocks_missing_input(tmp_path: Path) -> None:
    report = log_ai_shadow_model_decisions(
        input_path=tmp_path / "missing.json",
        output_path=tmp_path / "reports" / "decisions.jsonl",
        report_path=tmp_path / "reports" / "logger.json",
        registry_path=None,
        trainer_report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_input"


def test_decision_logger_blocks_unsafe_flags(tmp_path: Path) -> None:
    source = tmp_path / "decisions.json"
    write_json(source, [{**trainer_report(), **decision_rows()[0], "live_trading_enabled": True}])

    report = log_ai_shadow_model_decisions(
        input_path=source,
        output_path=tmp_path / "reports" / "decisions.jsonl",
        report_path=tmp_path / "reports" / "logger.json",
        registry_path=None,
        trainer_report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_safety_flags"
    assert "unsafe_flag:live_trading_enabled=true" in report["blocking_errors"]


def test_decision_logger_requires_model_identity(tmp_path: Path) -> None:
    source = tmp_path / "decisions.json"
    write_json(source, decision_rows())

    report = log_ai_shadow_model_decisions(
        input_path=source,
        output_path=tmp_path / "reports" / "decisions.jsonl",
        report_path=tmp_path / "reports" / "logger.json",
        registry_path=None,
        trainer_report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_model_identity"


def test_decision_logger_writes_jsonl_append_only(tmp_path: Path) -> None:
    source = tmp_path / "decisions.json"
    trainer = tmp_path / "reports" / "trainer.json"
    output = tmp_path / "reports" / "ai_shadow_model_decisions.jsonl"
    write_json(source, decision_rows())
    write_json(trainer, trainer_report())

    first = log_ai_shadow_model_decisions(
        input_path=source,
        output_path=output,
        report_path=tmp_path / "reports" / "logger.json",
        registry_path=None,
        trainer_report_path=trainer,
    )
    second = log_ai_shadow_model_decisions(
        input_path=source,
        output_path=output,
        report_path=tmp_path / "reports" / "logger2.json",
        registry_path=None,
        trainer_report_path=trainer,
    )
    rows = read_jsonl(output)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert len(rows) == 2
    assert rows[0]["decision_id"] == "decision-1"
    assert rows[0]["model_id"] == "ai_shadow_incremental_logistic_regression"
    assert rows[0]["sends_orders"] is False
    assert rows[0]["changes_risk"] is False


def test_decision_logger_never_sends_orders_or_changes_risk(tmp_path: Path) -> None:
    source = tmp_path / "decisions.json"
    trainer = tmp_path / "trainer.json"
    write_json(source, decision_rows())
    write_json(trainer, trainer_report())

    report = log_ai_shadow_model_decisions(
        input_path=source,
        output_path=tmp_path / "reports" / "decisions.jsonl",
        report_path=tmp_path / "reports" / "logger.json",
        registry_path=None,
        trainer_report_path=trainer,
    )
    text = (ROOT / "smartcrypto" / "ml" / "model_decision_logger.py").read_text(encoding="utf-8")

    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API", "promote"]:
        assert forbidden not in text


def test_outcome_tracker_blocks_missing_decisions(tmp_path: Path) -> None:
    report = track_ai_shadow_outcomes(
        decisions_path=tmp_path / "missing.jsonl",
        feedback_path=None,
        microbatch_path=None,
        output_path=tmp_path / "reports" / "outcomes.jsonl",
        report_path=tmp_path / "reports" / "outcomes.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_decisions"


def test_outcome_tracker_matches_by_order_id(tmp_path: Path) -> None:
    decisions = tmp_path / "reports" / "decisions.jsonl"
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    output = tmp_path / "reports" / "outcomes.jsonl"
    write_jsonl(decisions, [logged_decision(order_id="order-1")])
    write_parquet(feedback, feedback_rows())

    report = track_ai_shadow_outcomes(
        decisions_path=decisions,
        feedback_path=feedback,
        microbatch_path=None,
        output_path=output,
        report_path=tmp_path / "reports" / "outcomes.json",
        strict=True,
    )
    rows = read_jsonl(output)

    assert report["status"] == "ok"
    assert report["matched_rows"] == 1
    assert rows[0]["matched_order_id"] == "order-1"
    assert rows[0]["pnl_fechado"] == 2.5
    assert rows[0]["target_profitable"] == 1


def test_outcome_tracker_matches_by_symbol_side_time_window(tmp_path: Path) -> None:
    decisions = tmp_path / "reports" / "decisions.jsonl"
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    output = tmp_path / "reports" / "outcomes.jsonl"
    write_jsonl(decisions, [logged_decision(order_id=None, correlation_id="corr-time", open_time="2026-01-01T00:00:30Z")])
    rows = feedback_rows()
    rows[0]["order_id"] = "different-order"
    write_parquet(feedback, rows)

    report = track_ai_shadow_outcomes(
        decisions_path=decisions,
        feedback_path=feedback,
        microbatch_path=None,
        output_path=output,
        report_path=tmp_path / "reports" / "outcomes.json",
        strict=True,
    )
    rows = read_jsonl(output)

    assert report["status"] == "ok"
    assert rows[0]["matched"] is True
    assert rows[0]["matched_order_id"] == "different-order"


def test_outcome_tracker_reports_no_matches(tmp_path: Path) -> None:
    decisions = tmp_path / "reports" / "decisions.jsonl"
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    output = tmp_path / "reports" / "outcomes.jsonl"
    write_jsonl(decisions, [logged_decision(symbol="ETHUSDT", side="short", order_id=None, correlation_id="corr")])
    write_parquet(feedback, feedback_rows())

    report = track_ai_shadow_outcomes(
        decisions_path=decisions,
        feedback_path=feedback,
        microbatch_path=None,
        output_path=output,
        report_path=tmp_path / "reports" / "outcomes.json",
    )

    assert report["status"] == "no_matches"
    assert report["matched_rows"] == 0
    assert read_jsonl(output)[0]["matched"] is False


def test_outcome_tracker_never_touches_training_dataset_or_trades_master(tmp_path: Path) -> None:
    decisions = tmp_path / "reports" / "decisions.jsonl"
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    training_dataset = tmp_path / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "trades" / "trades_master.xlsx"
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    trades_master.write_bytes(b"master")
    before = trades_master.read_bytes()
    write_jsonl(decisions, [logged_decision(order_id="order-1")])
    write_parquet(feedback, feedback_rows())

    report = track_ai_shadow_outcomes(
        decisions_path=decisions,
        feedback_path=feedback,
        microbatch_path=None,
        output_path=tmp_path / "reports" / "outcomes.jsonl",
        report_path=tmp_path / "reports" / "outcomes.json",
    )

    assert report["status"] == "ok"
    assert not training_dataset.exists()
    assert trades_master.read_bytes() == before


def test_cli_log_decisions_runs_successfully(tmp_path: Path, capsys) -> None:
    module = load_cli("log_ai_shadow_model_decisions.py")
    source = tmp_path / "decisions.json"
    trainer = tmp_path / "trainer.json"
    write_json(source, decision_rows())
    write_json(trainer, trainer_report())

    exit_code = module.main(
        [
            "--trainer-report",
            str(trainer),
            "--input",
            str(source),
            "--output",
            str(tmp_path / "reports" / "decisions.jsonl"),
            "--report",
            str(tmp_path / "reports" / "logger.json"),
            "--strict",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["logged_rows"] == 1


def test_cli_track_outcomes_runs_successfully(tmp_path: Path, capsys) -> None:
    module = load_cli("track_ai_shadow_outcomes.py")
    decisions = tmp_path / "reports" / "decisions.jsonl"
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    write_jsonl(decisions, [logged_decision(order_id="order-1")])
    write_parquet(feedback, feedback_rows())

    exit_code = module.main(
        [
            "--decisions",
            str(decisions),
            "--feedback",
            str(feedback),
            "--microbatch",
            str(tmp_path / "missing.parquet"),
            "--output",
            str(tmp_path / "reports" / "outcomes.jsonl"),
            "--report",
            str(tmp_path / "reports" / "outcomes.json"),
            "--strict",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["matched_rows"] == 1


def logged_decision(
    *,
    order_id: str | None,
    correlation_id: str = "order-1",
    symbol: str = "BTCUSDT",
    side: str = "long",
    open_time: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "decision_id": "decision-1",
        "correlation_id": correlation_id,
        "model_id": "ai_shadow_incremental_logistic_regression",
        "model_version": "shadow_v1",
        "symbol": symbol,
        "side": side,
        "action_shadow": "SHADOW_ENTRY",
        "order_id": order_id,
        "open_time_utc": open_time,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
