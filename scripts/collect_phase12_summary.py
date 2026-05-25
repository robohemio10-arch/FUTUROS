from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path):
    if not path.exists():
        return {"exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "error": str(exc)}
    if isinstance(payload, dict):
        payload["exists"] = True
    return payload


def main() -> None:
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preflight": load(Path("data/reports/phase12_preflight_report.json")),
        "signal_pin": load(Path("data/reports/phase12_signal_pin_report.json")),
        "runtime": load(Path("data/reports/phase12_signal_runtime_report.json")),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    out = Path("data/reports/phase12_summary.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": "ok", "summary": str(out)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
