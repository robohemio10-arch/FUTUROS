from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    reports_dir = Path("data/reports")
    evidence_dir = Path("data/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "phase": 15,
        "name": "Controlled Paper Exit + Feedback Acceleration",
        "preflight": read_json(reports_dir / "phase15_preflight_report.json"),
        "exit_signal": read_json(reports_dir / "phase15_exit_signal_report.json"),
        "exit_flow": read_json(reports_dir / "phase15_exit_flow_report.json"),
        "phase14_closed_feedback": read_json(reports_dir / "phase14_closed_feedback_report.json"),
        "phase5_import": read_json(reports_dir / "phase5_import_report.json"),
        "phase5_rebuild": read_json(reports_dir / "phase5_rebuild_report.json"),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    summary_path = reports_dir / "phase15_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = evidence_dir / f"phase15_{stamp}.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir="data", base_dir="reports")
    print(json.dumps({"status": "ok", "summary": str(summary_path), "evidence": str(zip_path)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
