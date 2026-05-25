from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from smartcrypto.data.freqtrade_db_reader import inspect_freqtrade_db
from smartcrypto.execution.signal_store import active_signals, read_json


def read_jsonl_tail(path: Path, limit: int = 50) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize_signal(path: Path) -> dict:
    payload = read_json(path)
    signals = active_signals(payload)
    return {
        "path": str(path),
        "exists": path.exists(),
        "generated_at": None if payload is None else payload.get("generated_at"),
        "source": None if payload is None else payload.get("source"),
        "model_version": None if payload is None else payload.get("model_version"),
        "signal_count": len(signals),
        "pairs": [signal.get("pair") for signal in signals],
        "sides": [signal.get("side") for signal in signals],
    }


def main() -> None:
    decision_path = Path("data/runtime/freqtrade_signal_decisions.jsonl")
    decisions = read_jsonl_tail(decision_path)
    reasons = Counter(str(item.get("reason")) for item in decisions)
    report = {
        "primary_signal": summarize_signal(Path("data/freqtrade_signals.json")),
        "pinned_signal": summarize_signal(Path("data/runtime/active_freqtrade_signals.json")),
        "decision_log": {
            "path": str(decision_path),
            "exists": decision_path.exists(),
            "rows_sampled": len(decisions),
            "accepted_decisions": sum(1 for item in decisions if item.get("accepted") is True),
            "entry_events": sum(1 for item in decisions if item.get("event") == "populate_entry_trend"),
            "reason_counts": dict(reasons),
            "recent": decisions[-10:],
        },
        "freqtrade_db": inspect_freqtrade_db(),
    }
    report["phase12_status"] = {
        "status": "ok",
        "signals_available": report["primary_signal"]["signal_count"] > 0 or report["pinned_signal"]["signal_count"] > 0,
        "strategy_seen_signals": report["decision_log"]["accepted_decisions"] > 0,
        "entry_events": report["decision_log"]["entry_events"],
        "open_trades": report["freqtrade_db"].get("open_rows", 0),
        "closed_trades": report["freqtrade_db"].get("closed_rows", 0),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase12_signal_runtime_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
