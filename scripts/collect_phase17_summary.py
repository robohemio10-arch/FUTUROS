from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from smartcrypto.runtime.integrity_traceability_v2 import (
    ConsistentReadError,
    atomic_write_json,
    read_json_consistent,
)


def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    try:
        payload = read_json_consistent(p)
        payload.setdefault("exists", True)
        return payload
    except (ConsistentReadError, TypeError) as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    summary = {
        "status": "ok",
        "phase": "phase17_post_feedback_reset_next_cycle",
        "preflight": read_json("data/reports/phase17_preflight_report.json"),
        "reset": read_json("data/reports/phase17_reset_report.json"),
        "cycle_state": read_json("data/reports/phase17_cycle_state_report.json"),
        "phase13_signal_report": read_json("data/reports/phase13_signal_producer_report.json"),
        "phase14_feedback_report": read_json("data/reports/phase14_closed_feedback_report.json"),
        "phase5_import_report": read_json("data/reports/phase5_import_report.json"),
        "phase5_rebuild_report": read_json("data/reports/phase5_rebuild_report.json"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    out = Path("data/reports/phase17_summary.json")
    atomic_write_json(out, summary, sort_keys=False)
    print(json.dumps({"status": "ok", "summary": str(out)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
