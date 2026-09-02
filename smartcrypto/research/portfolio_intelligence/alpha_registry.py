"""Deterministic research-only alpha registry used by W5/W6."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .contracts import AlphaDefinition, AlphaRegistrySnapshot, stable_id


def build_alpha_registry(
    definitions: Iterable[AlphaDefinition],
    *,
    created_at_utc: datetime,
) -> AlphaRegistrySnapshot:
    ordered = tuple(sorted(definitions, key=lambda item: item.strategy_id))
    strategy_ids = [item.strategy_id for item in ordered]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("duplicate_strategy_id")
    canonical = [item.model_dump(mode="json") for item in ordered]
    return AlphaRegistrySnapshot(
        registry_id=stable_id("alpha-registry", canonical),
        created_at_utc=created_at_utc,
        definitions=ordered,
    )


def registered_strategy_ids(registry: AlphaRegistrySnapshot | None) -> frozenset[str]:
    if registry is None:
        return frozenset()
    return frozenset(item.strategy_id for item in registry.definitions)
