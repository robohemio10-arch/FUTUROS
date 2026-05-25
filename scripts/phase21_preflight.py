from __future__ import annotations

import json
from pathlib import Path
import yaml

if __name__ == "__main__":
    config_path = Path("config/phase21_walkforward.yml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    candidates = config.get("paths", {}).get("dataset_candidates", [])
    existing = [path for path in candidates if Path(path).exists()]
    report = {"status": "ok" if existing else "blocked", "config_exists": config_path.exists(), "dataset_candidates": [{"path": path, "exists": Path(path).exists()} for path in candidates]}
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase21_preflight_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(2)
