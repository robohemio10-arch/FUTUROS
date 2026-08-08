"""Profit-first paper trading research."""

from __future__ import annotations

from typing import Any

from .contracts import KNOWN_CORRUPT_PAPER_TRADE_IDS, ProfitMaximizationResult
from .metrics import prepare_profit_dataset
from .optimizer import build_profit_maximization


def run_profit_maximization(*args: Any, **kwargs: Any) -> ProfitMaximizationResult:
    """Lazy-load I/O dependencies so pure optimizer tests remain lightweight."""

    from .runner import run_profit_maximization as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "KNOWN_CORRUPT_PAPER_TRADE_IDS",
    "ProfitMaximizationResult",
    "build_profit_maximization",
    "prepare_profit_dataset",
    "run_profit_maximization",
]
