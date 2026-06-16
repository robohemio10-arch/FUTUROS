"""Status normalization and severity ordering for visual dashboard components."""

from __future__ import annotations

from typing import Any


_ALIASES = {
    "OK": "ok",
    "PASS": "ok",
    "PASSED": "ok",
    "HEALTHY": "ok",
    "ONLINE": "ok",
    "ATIVA": "ok",
    "ATIVO": "ok",
    "APROVADO": "ok",
    "APPROVED": "ok",
    "AI_ACCEPT": "ok",
    "INFO": "info",
    "INFORMATIONAL": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "YELLOW": "warning",
    "DEGRADED": "warning",
    "MONITORAR": "warning",
    "OBSERVAR": "warning",
    "MONITORING": "monitoring",
    "STALE": "stale",
    "CRITICAL_STALE": "critical",
    "ERROR": "error",
    "FAILED": "error",
    "FAIL": "error",
    "ERRO": "error",
    "RED": "error",
    "CRITICAL": "critical",
    "PANIC": "critical",
    "BLACK": "hard_blocked",
    "BLOCKED": "blocked",
    "MISSING_REQUIRED": "blocked",
    "AI_REJECT": "blocked",
    "NO_TRADE": "blocked",
    "HARD_BLOCKED": "hard_blocked",
    "HARD-BLOCKED": "hard_blocked",
    "READONLY": "readonly",
    "READ-ONLY": "readonly",
    "READ_ONLY": "readonly",
    "PAPER": "paper",
    "SHADOW": "shadow",
    "PAPER / SHADOW": "paper",
    "PAPER / SHADOW ONLY": "paper",
    "DISABLED": "disabled",
    "OFFLINE": "error",
    "UNKNOWN": "unknown",
    "MISSING": "unknown",
    "MISSING_OPTIONAL": "unknown",
    "FUTURE_SOURCE_PENDING": "planned",
    "PLANNED": "planned",
    "NEUTRAL": "neutral",
    "PURPLE": "purple",
}

_LABELS = {
    "hard_blocked": "HARD-BLOCKED",
    "blocked": "BLOCKED",
    "critical": "CRITICAL",
    "error": "ERROR",
    "warning": "WARNING",
    "monitoring": "MONITORING",
    "stale": "STALE",
    "unknown": "UNKNOWN",
    "planned": "PLANNED",
    "ok": "OK",
    "info": "INFO",
    "readonly": "READ-ONLY",
    "paper": "PAPER",
    "shadow": "SHADOW",
    "disabled": "DISABLED",
    "neutral": "NEUTRAL",
    "purple": "AI / RESEARCH",
}

_SEVERITY_RANK = {
    "hard_blocked": 900,
    "blocked": 800,
    "critical": 700,
    "error": 600,
    "warning": 500,
    "monitoring": 450,
    "stale": 400,
    "unknown": 300,
    "planned": 250,
    "disabled": 200,
    "neutral": 100,
    "info": 80,
    "readonly": 70,
    "paper": 60,
    "shadow": 60,
    "purple": 50,
    "ok": 0,
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


def status_severity_rank(status: Any) -> int:
    """Return a stable visual severity rank.

    Higher values are more severe. This is intentionally visual-only and does
    not authorize readiness, risk, orders, notifications, or runtime changes.
    """

    return _SEVERITY_RANK[normalize_status(status)]


def worst_status(*statuses: Any, default: str = "unknown") -> str:
    """Return the visually worst normalized status from a sequence."""

    candidates = [normalize_status(status) for status in statuses if status is not None]
    if not candidates:
        return normalize_status(default)
    return max(candidates, key=status_severity_rank)


def is_blocking_visual_status(status: Any) -> bool:
    """Return true when a status must be rendered as a hard visual blocker."""

    return normalize_status(status) in {"hard_blocked", "blocked", "critical", "error"}
