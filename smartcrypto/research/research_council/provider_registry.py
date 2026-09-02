"""Nominal provider registry for research-only council adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class RegisteredProvider(Protocol):
    provider_id: str
    provider_type: str
    model_id: str
    model_version: str
    enabled: bool

    def invoke(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    def register(self, provider: RegisteredProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"provider_already_registered:{provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> RegisteredProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"provider_not_registered:{provider_id}") from exc

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
