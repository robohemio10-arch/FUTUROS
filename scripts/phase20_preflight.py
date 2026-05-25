from __future__ import annotations

import json
from pathlib import Path

if __name__ == "__main__":
    required = ["config/risk_manager.yml", "smartcrypto/risk/risk_manager.py", "scripts/set_kill_switch.py", "scripts/inspect_phase20_risk.py"]
    paths = {item: {"exists": Path(item).exists()} for item in required}
    report = {"status": "ok" if all(v["exists"] for v in paths.values()) else "blocked", "paths": paths}
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase20_preflight_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(2)
