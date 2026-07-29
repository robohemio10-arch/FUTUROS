from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import zipfile

from smartcrypto.runtime.integrity_traceability_v2 import (
    atomic_write_json,
    read_json_consistent,
)


REPORTS = [
    "data/reports/phase16_preflight_report.json",
    "data/reports/phase16_force_close_report.json",
    "data/reports/phase16_output_summary.json",
    "data/reports/phase14_closed_feedback_report.json",
    "data/reports/phase5_import_report.json",
    "data/reports/phase5_rebuild_report.json",
]


def read_json(path: Path):
    if not path.exists():
        return None
    return read_json_consistent(path)


def main() -> None:
    reports = {Path(item).name: read_json(Path(item)) for item in REPORTS}
    summary = {
        "status": "ok",
        "reports": reports,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    out = Path("data/reports/phase16_summary.json")
    atomic_write_json(out, summary, sort_keys=False)

    evidence_dir = Path("data/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = evidence_dir / f"phase16_{stamp}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in REPORTS + [
            "data/reports/phase16_summary.json",
            "data/trades/freqtrade_paper_trades_raw.parquet",
            "data/trades/inbox/freqtrade_paper_closed_trades.csv",
            "data/runtime/paper_exit_control.json",
        ]:
            path = Path(item)
            if path.exists():
                archive.write(path, arcname=item)

    print(json.dumps({"status": "ok", "summary": str(out), "evidence": str(zip_path)}, indent=2))


if __name__ == "__main__":
    main()
