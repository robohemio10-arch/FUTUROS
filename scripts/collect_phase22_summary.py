from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["exists"] = True
        return data
    except Exception as exc:
        return {"exists": True, "error": str(exc)}

def main() -> None:
    summary = {
        "status": "ok",
        "phase": "phase22_historical_market_backfill",
        "preflight": read_json("data/reports/phase22_preflight_report.json"),
        "download": read_json("data/reports/phase22_download_report.json"),
        "features": read_json("data/reports/phase22_features_report.json"),
        "outputs": read_json("data/reports/phase22_output_summary.json"),
        "phase5_rebuild": read_json("data/reports/phase5_rebuild_report.json"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports/phase22_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
