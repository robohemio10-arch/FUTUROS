"""Seeded latency models with no wall-clock sleeps."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from smartcrypto.data.canonical_data_foundation_v2.contracts import stable_hash

from .contracts import ContractViolation, LatencyDistribution


@dataclass(frozen=True)
class LatencySpec:
    distribution: LatencyDistribution = LatencyDistribution.CONSTANT
    constant_ms: Decimal = Decimal("0")
    empirical_ms: tuple[Decimal, ...] = ()
    lognormal_mu: float = 0.0
    lognormal_sigma: float = 0.0
    gamma_shape: float = 1.0
    gamma_scale: float = 0.0

    def __post_init__(self) -> None:
        values = (self.constant_ms, *self.empirical_ms)
        if any(value < 0 for value in values):
            raise ContractViolation("negative_latency_forbidden")
        if self.lognormal_sigma < 0:
            raise ContractViolation("negative_lognormal_sigma")
        if self.gamma_shape <= 0 or self.gamma_scale < 0:
            raise ContractViolation("invalid_gamma_latency_parameters")
        if self.distribution == LatencyDistribution.EMPIRICAL:
            if not self.empirical_ms:
                raise ContractViolation("empirical_latency_fixture_required")

    def sample_ms(self, *, rng: random.Random, sample_index: int) -> Decimal:
        if self.distribution == LatencyDistribution.CONSTANT:
            value = self.constant_ms
        elif self.distribution == LatencyDistribution.EMPIRICAL:
            value = self.empirical_ms[sample_index % len(self.empirical_ms)]
        elif self.distribution == LatencyDistribution.LOGNORMAL:
            value = Decimal(
                str(rng.lognormvariate(self.lognormal_mu, self.lognormal_sigma))
            )
        elif self.distribution == LatencyDistribution.GAMMA:
            value = Decimal(
                str(rng.gammavariate(self.gamma_shape, self.gamma_scale))
            )
        else:
            raise ContractViolation("unsupported_latency_distribution")
        if value < 0:
            raise ContractViolation("negative_latency_sample")
        return value

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["distribution"] = self.distribution.value
        payload["constant_ms"] = str(self.constant_ms)
        payload["empirical_ms"] = [str(item) for item in self.empirical_ms]
        return payload


@dataclass(frozen=True)
class LatencyProfile:
    signal_to_submit: LatencySpec = LatencySpec()
    client_to_exchange: LatencySpec = LatencySpec()
    exchange_ack: LatencySpec = LatencySpec()
    market_data: LatencySpec = LatencySpec()
    cancel: LatencySpec = LatencySpec()
    reprice: LatencySpec = LatencySpec()
    jitter: LatencySpec = LatencySpec()

    @property
    def profile_hash(self) -> str:
        return stable_hash(self.to_dict())

    def sample(
        self,
        name: str,
        *,
        rng: random.Random,
        sample_index: int,
    ) -> Decimal:
        specs = {
            "signal_to_submit": self.signal_to_submit,
            "client_to_exchange": self.client_to_exchange,
            "exchange_ack": self.exchange_ack,
            "market_data": self.market_data,
            "cancel": self.cancel,
            "reprice": self.reprice,
            "jitter": self.jitter,
        }
        if name not in specs:
            raise ContractViolation("unknown_latency_component")
        return specs[name].sample_ms(rng=rng, sample_index=sample_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_to_submit": self.signal_to_submit.to_dict(),
            "client_to_exchange": self.client_to_exchange.to_dict(),
            "exchange_ack": self.exchange_ack.to_dict(),
            "market_data": self.market_data.to_dict(),
            "cancel": self.cancel.to_dict(),
            "reprice": self.reprice.to_dict(),
            "jitter": self.jitter.to_dict(),
        }
