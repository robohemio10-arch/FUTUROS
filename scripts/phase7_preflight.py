from __future__ import annotations

import importlib
import json
from pathlib import Path


def main() -> None:
    required_modules = ["pandas", "pyarrow", "openpyxl"]
    missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
    db_candidates = [
        Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
        Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    ]
    db_path = next((path for path in db_candidates if path.exists()), db_candidates[0])
    payload = {
        "status": "ok" if not missing_modules else "blocked",
        "missing_modules": missing_modules,
        "freqtrade_db_exists": db_path.exists(),
        "freqtrade_db_path": str(db_path),
        "note": "freqtrade_db_exists=false é aceitável antes do Freqtrade gerar o banco.",
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase7_preflight_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
