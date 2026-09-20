from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from smartcrypto.research.canonical_treatment.economic_monitor import (
    build_monitor_report,
)
from smartcrypto.research.canonical_treatment.publisher import (
    _hash,
    build_treatment_artifact,
)


def _control(op_id: str, op_sha: str, signal_id: str) -> dict:
    return {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "short",
        "signal_id": signal_id,
        "candidate_id": "candidate-1",
        "correlation_id": "corr-1",
        "risk_approved": True,
        "valid_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "decision_ledger": {
            "decision_event_id": op_id,
            "decision_payload_sha256": op_sha,
        },
    }


def _v3(op_id: str, op_sha: str, signal_id: str, shadow: str) -> dict:
    event = "v3-shadow-decision-test-1"
    payload_sha = "b" * 64
    body = {
        "schema_version": "qlib_v3_operational_decision_crosswalk_v1",
        "signal_id": signal_id,
        "operational_decision_event_id": op_id,
        "operational_decision_payload_sha256": op_sha,
        "v3_decision_event_id": event,
        "v3_decision_payload_sha256": payload_sha,
    }
    decision = {
        "event_id": event,
        "signal_id": signal_id,
        "payload_sha256": payload_sha,
        "ai_shadow_decision": shadow,
        "qlib_score": 0.9,
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "short",
    }
    return {
        "signals": [
            {
                "signal_id": signal_id,
                "decision_event_id": event,
                "decision": decision,
                "operational_crosswalk": body | {
                    "crosswalk_sha256": _hash(body)
                },
            }
        ]
    }


def test_publisher_allow_and_abstain(tmp_path: Path) -> None:
    op_id = "decision-event:test-1"
    op_sha = "a" * 64
    signal_id = "signal:test:1"
    control = {"signals": [_control(op_id, op_sha, signal_id)]}
    control_path = tmp_path / "control.json"
    control_path.write_text(json.dumps(control), encoding="utf-8")
    evidence_path = tmp_path / "evidence.json"
    output = tmp_path / "active.json"
    report = tmp_path / "report.json"
    ledger = tmp_path / "ledger.json"

    evidence_path.write_text(
        json.dumps(_v3(op_id, op_sha, signal_id, "ALLOW")),
        encoding="utf-8",
    )
    result = build_treatment_artifact(
        control_signals_path=control_path,
        v3_evidence_path=evidence_path,
        output_path=output,
        report_path=report,
        ledger_path=ledger,
    )
    assert result["status"] == "ok"
    assert result["selected_signal_count"] == 1
    assert len(json.loads(output.read_text())["signals"]) == 1

    ledger.unlink()
    evidence_path.write_text(
        json.dumps(_v3(op_id, op_sha, signal_id, "ABSTAIN")),
        encoding="utf-8",
    )
    result = build_treatment_artifact(
        control_signals_path=control_path,
        v3_evidence_path=evidence_path,
        output_path=output,
        report_path=report,
        ledger_path=ledger,
    )
    assert result["status"] == "ok"
    assert result["selected_signal_count"] == 0
    assert json.loads(output.read_text())["signals"] == []


def test_bad_crosswalk_fails_closed(tmp_path: Path) -> None:
    op_id = "decision-event:test-1"
    op_sha = "a" * 64
    signal_id = "signal:test:1"
    control_path = tmp_path / "control.json"
    control_path.write_text(
        json.dumps({"signals": [_control(op_id, op_sha, signal_id)]}),
        encoding="utf-8",
    )
    evidence = _v3(op_id, op_sha, signal_id, "ALLOW")
    evidence["signals"][0]["operational_crosswalk"]["crosswalk_sha256"] = "0" * 64
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    output = tmp_path / "active.json"
    result = build_treatment_artifact(
        control_signals_path=control_path,
        v3_evidence_path=evidence_path,
        output_path=output,
        report_path=tmp_path / "report.json",
        ledger_path=tmp_path / "ledger.json",
    )
    assert result["status"] == "blocked"
    assert json.loads(output.read_text())["signals"] == []


def _db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                is_open INTEGER NOT NULL,
                open_date TEXT,
                close_date TEXT,
                enter_tag TEXT,
                close_profit_abs REAL,
                stake_amount REAL
            )
            """
        )
        con.executemany(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def test_monitor_paired_uplift(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    event = "decision-event:paired-1"
    ledger = {
        "schema_version": "canonical_treatment_decision_ledger_v1",
        "experiment_id": "canonical-treatment-paper-v1",
        "started_at_utc": (now - timedelta(hours=2)).isoformat(),
        "rows": [
            {
                "operational_decision_event_id": event,
                "status": "scored",
                "selected": True,
                "signal_id": "signal:test:3",
                "valid_until": (now - timedelta(hours=1)).isoformat(),
                "ai_shadow_decision": "ALLOW",
                "qlib_score": 0.9,
            }
        ],
    }
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    opened = (now - timedelta(minutes=50)).isoformat()
    closed = (now - timedelta(minutes=20)).isoformat()
    tag = f"smartcrypto_short|decision_event_id={event}"

    control_db = tmp_path / "control.sqlite"
    treatment_db = tmp_path / "treatment.sqlite"
    _db(control_db, [(1, 0, opened, closed, tag, -2.0, 50.0)])
    _db(treatment_db, [(1, 0, opened, closed, tag, 3.0, 50.0)])

    report = build_monitor_report(
        ledger_path=ledger_path,
        control_db=control_db,
        treatment_db=treatment_db,
        output_path=tmp_path / "monitor.json",
    )
    assert report["status"] == "ok"
    assert report["sample"]["financially_resolved_decisions"] == 1
    assert report["paired_uplift"]["delta_net_pnl_total"] == 5.0
    assert report["live_trading_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_treatment_freqtrade_config_is_paper_only() -> None:
    path = Path("freqtrade/user_data/config.paper.canonical-treatment.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["bot_name"] == "smartcrypto-paper-canonical-treatment"
    assert payload["max_open_trades"] == 2
    assert payload["stake_amount"] == 50
    assert "api_server" not in payload
    assert payload["exchange"]["key"] == ""
    assert payload["exchange"]["secret"] == ""
