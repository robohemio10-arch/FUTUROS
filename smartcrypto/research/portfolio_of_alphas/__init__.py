"""W6 Portfolio of Alphas + Fleet read-only research layer."""

from .contracts import (
    AlphaHealthObservation,
    AlphaHealthStatus,
    AlphaPortfolioRequest,
    AlphaPortfolioSnapshot,
    AlphaSleeveDefinition,
    AlphaSleeveSnapshot,
    AlphaStrategyState,
    FleetControlRequest,
    FleetControlSnapshot,
    FleetDiscoveredService,
    FleetHealthStatus,
    FleetInstanceObservation,
    FleetInstanceState,
    FleetRuntimeMode,
    PortfolioStatus,
)
from .compose_discovery import discover_freqtrade_services_from_compose
from .fleet import build_fleet_control_snapshot
from .portfolio import build_portfolio_of_alphas

__all__ = [
    "AlphaHealthObservation",
    "AlphaHealthStatus",
    "AlphaPortfolioRequest",
    "AlphaPortfolioSnapshot",
    "AlphaSleeveDefinition",
    "AlphaSleeveSnapshot",
    "AlphaStrategyState",
    "FleetControlRequest",
    "FleetControlSnapshot",
    "FleetDiscoveredService",
    "FleetHealthStatus",
    "FleetInstanceObservation",
    "FleetInstanceState",
    "FleetRuntimeMode",
    "PortfolioStatus",
    "build_fleet_control_snapshot",
    "discover_freqtrade_services_from_compose",
    "build_portfolio_of_alphas",
]
