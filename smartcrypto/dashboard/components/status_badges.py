from __future__ import annotations

from typing import Any

from smartcrypto.dashboard.ui.status import normalize_status as normalize_visual_status
from smartcrypto.dashboard.ui.status import status_to_label


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
    visual_status = normalize_visual_status(normalized)
    label = status_to_label(visual_status)
    ui.markdown(
        f'<span class="sfc-status-pill sfc-status-{visual_status}">{label}</span>',
        unsafe_allow_html=True,
    )
    return normalized
