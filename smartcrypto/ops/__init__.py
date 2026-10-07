"""Operational helpers with optional data dependencies loaded on demand."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .paper_session import build_session_state, collect_evidence

__all__ = ["build_session_state", "collect_evidence"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(".paper_session", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
