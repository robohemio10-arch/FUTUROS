"""Institutional futures execution realism engine V2, research-only."""

from .contracts import (
    EventType,
    InputAuthority,
    LiquidityRole,
    MarginMode,
    MarketEvent,
    OrderIntent,
    OrderState,
    OrderType,
    QueueModel,
    SAFETY_FLAGS,
    Side,
    SlippageModel,
    TimeInForce,
)
from .costs import CostModel
from .engine import (
    EngineResult,
    EventDrivenExecutionEngine,
    ExecutionEngineConfig,
)
from .margin import (
    MaintenanceTier,
    MarginAccount,
    MarginEngine,
    Position,
)
from .pipeline import build_futures_execution_realism_report

__all__ = [
    "SAFETY_FLAGS",
    "CostModel",
    "EngineResult",
    "EventDrivenExecutionEngine",
    "EventType",
    "ExecutionEngineConfig",
    "InputAuthority",
    "LiquidityRole",
    "MaintenanceTier",
    "MarginAccount",
    "MarginEngine",
    "MarginMode",
    "MarketEvent",
    "OrderIntent",
    "OrderState",
    "OrderType",
    "Position",
    "QueueModel",
    "Side",
    "SlippageModel",
    "TimeInForce",
    "build_futures_execution_realism_report",
]
