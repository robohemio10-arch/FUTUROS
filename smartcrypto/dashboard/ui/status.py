"""Status normalization for visual dashboard components."""

from typing import Any


_ALIASES = {
    "OK": "ok",
    "PASS": "ok",
    "PASSED": "ok",
    "HEALTHY": "ok",
    "ATIVA": "ok",
    "APROVADO": "ok",
    "AI_ACCEPT": "ok",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "DEGRADED": "warning",
    "STALE": "warning",
    "MONITORAR": "warning",
    "OBSERVAR": "warning",
    "MONITORING": "monitoring",
    "ERROR": "error",
    "FAILED": "error",
    "ERRO": "error",
    "BLOCKED": "blocked",
    "AI_REJECT": "blocked",
    "HARD_BLOCKED": "hard_blocked",
    "HARD-BLOCKED": "hard_blocked",
    "READONLY": "readonly",
    "READ-ONLY": "readonly",
    "READ_ONLY": "readonly",
    "PAPER": "paper",
    "SHADOW": "shadow",
    "DISABLED": "disabled",
    "UNKNOWN": "unknown",
    "MISSING": "unknown",
    "MISSING_REQUIRED": "unknown",
    "MISSING_OPTIONAL": "unknown",
    "NEUTRAL": "neutral",
    "PURPLE": "purple",
}

_LABELS = {
    "ok": "OK",
    "info": "INFO",
    "warning": "WARNING",
    "monitoring": "MONITORING",
    "error": "ERROR",
    "blocked": "BLOCKED",
    "hard_blocked": "HARD-BLOCKED",
    "readonly": "READ-ONLY",
    "paper": "PAPER",
    "shadow": "SHADOW",
    "disabled": "DISABLED",
    "unknown": "UNKNOWN",
    "neutral": "NEUTRAL",
    "purple": "AI / RESEARCH",
}


def normalize_status(value: Any) -> str:
    """Normalize heterogeneous snapshot statuses to visual status names."""

    candidate = str(value or "UNKNOWN").strip().upper()
    return _ALIASES.get(candidate, "unknown")


def status_to_css_class(status: str) -> str:
    """Return the CSS modifier class for a status."""

    return f"sfc-status-{normalize_status(status)}"


def status_to_label(status: str) -> str:
    """Return the institutional display label for a status."""

    normalized = normalize_status(status)
    return _LABELS[normalized]
