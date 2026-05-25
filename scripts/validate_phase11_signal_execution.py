from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_decisions(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main() -> None:
    signals = read_json(Path("data/freqtrade_signals.json"))
    decisions = read_decisions(Path("data/runtime/freqtrade_signal_decisions.jsonl"))
    db_status = read_json(Path("data/reports/phase11_freqtrade_db_status_report.json"))

    signal_rows = signals.get("signals", [])
    reason_counts = Counter(str(row.get("reason")) for row in decisions if isinstance(row, dict))
    accepted = [row for row in decisions if row.get("accepted") is True]
    entries = [row for row in decisions if str(row.get("event", "")).startswith("entry") or row.get("enter_long") or row.get("enter_short")]

    report = {
        "status": "ok",
        "signals_available": isinstance(signal_rows, list) and len(signal_rows) > 0,
        "signal_count": len(signal_rows) if isinstance(signal_rows, list) else 0,
        "signal_source": signals.get("source"),
        "model_version": signals.get("model_version"),
        "decision_rows": len(decisions),
        "accepted_decisions": len(accepted),
        "entry_events": len(entries),
        "decision_reason_counts": dict(reason_counts),
        "freqtrade_db": db_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if not report["signals_available"]:
        report["status"] = "blocked"
        report["reason"] = "no_active_signals"
    elif report["decision_rows"] == 0:
        report["status"] = "waiting"
        report["reason"] = "strategy_has_not_written_decisions_yet"
    elif reason_counts.get("no_signal_payload", 0) == report["decision_rows"]:
        report["status"] = "blocked"
        report["reason"] = "strategy_did_not_match_signal_payload_to_pairs"
    elif int(db_status.get("open_rows", 0) or 0) > 0 or int(db_status.get("closed_rows", 0) or 0) > 0:
        report["status"] = "ok"
        report["reason"] = "paper_trades_detected"
    else:
        report["status"] = "waiting"
        report["reason"] = "signals_available_waiting_for_entry_or_candle"

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase11_signal_execution_validation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
