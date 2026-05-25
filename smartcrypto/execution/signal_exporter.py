from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class SignalExporter:
    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)

    def write(self, runtime_mode: str, model_version: str, signals: list[dict]) -> None:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "model_version": model_version,
            "runtime_mode": runtime_mode,
            "signals": signals,
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.output_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(self.output_path)
