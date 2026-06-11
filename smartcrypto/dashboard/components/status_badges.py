from __future__ import annotations

from typing import Any


STATUS_SYMBOLS = {
    "OK": "[OK]",
    "WARNING": "[WARNING]",
    "DEGRADED": "[DEGRADED]",
    "STALE": "[STALE]",
    "BLOCKED": "[BLOCKED]",
    "ERROR": "[ERROR]",
    "MISSING_REQUIRED": "[MISSING]",
    "MISSING_OPTIONAL": "[OPTIONAL]",
    "UNKNOWN": "[UNKNOWN]",
    "HARD_BLOCKED": "[HARD-BLOCKED]",
}


def normalize_status(value: Any) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    return status if status in STATUS_SYMBOLS else "UNKNOWN"


def render_status_badge(status: Any, *, ui: Any) -> str:
    normalized = normalize_status(status)
    label = f"{STATUS_SYMBOLS[normalized]} {normalized}"
    if normalized == "OK":
        ui.success(label)
    elif normalized in {"WARNING", "DEGRADED", "STALE", "MISSING_OPTIONAL"}:
        ui.warning(label)
    elif normalized in {"BLOCKED", "ERROR", "MISSING_REQUIRED", "HARD_BLOCKED"}:
        ui.error(label)
    else:
        ui.info(label)
    return normalized
