from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import shutil
import zipfile


REPORTS = [
    "data/reports/phase11_preflight_report.json",
    "data/reports/phase11_signal_guard_report.json",
    "data/reports/phase11_freqtrade_db_status_report.json",
    "data/reports/phase11_signal_execution_validation_report.json",
    "data/reports/phase8_qlib_signal_export_report.json",
    "data/reports/phase10_output_summary.json",
]


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"exists": True, "invalid_json": True}
    if isinstance(payload, dict):
        payload["exists"] = True
        return payload
    return {"exists": True, "payload": payload}


def sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    reports = {Path(path).stem: read_json(Path(path)) for path in REPORTS}
    artifacts = {
        "signals": "data/freqtrade_signals.json",
        "qlib_predictions": "data/predictions/latest_qlib_predictions.parquet",
        "qlib_model": "data/models/qlib_market_model.joblib",
        "decision_log": "data/runtime/freqtrade_signal_decisions.jsonl",
        "freqtrade_db": "freqtrade/user_data/tradesv3.paper.sqlite",
    }

    summary = {
        "status": "ok",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reports": reports,
        "artifacts": {
            key: {
                "path": value,
                "exists": Path(value).exists(),
                "size_bytes": Path(value).stat().st_size if Path(value).exists() else None,
                "sha256": sha256(Path(value)),
            }
            for key, value in artifacts.items()
        },
    }

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    summary_path = Path("data/reports/phase11_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    evidence_dir = Path("data/evidence") / f"phase11_{run_id}"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    for report in REPORTS + [str(summary_path)]:
        src = Path(report)
        if src.exists():
            shutil.copy2(src, evidence_dir / src.name)

    for key, value in artifacts.items():
        src = Path(value)
        if src.exists() and src.is_file() and src.stat().st_size < 10_000_000:
            shutil.copy2(src, evidence_dir / src.name)

    zip_path = evidence_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in evidence_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(evidence_dir.parent))

    print(json.dumps({"status": "ok", "summary": str(summary_path), "evidence": str(zip_path)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
