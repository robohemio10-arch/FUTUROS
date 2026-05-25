from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


def registry_payload(
    *,
    status: str,
    model_name: str,
    model_path: str | None,
    training_report_path: str | None,
    walk_forward_report_path: str | None,
    production_enabled: bool,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "model_name": model_name,
        "model_path": model_path,
        "training_report_path": training_report_path,
        "walk_forward_report_path": walk_forward_report_path,
        "production_enabled": production_enabled,
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
