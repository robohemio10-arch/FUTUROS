"""Offline provider protocol with bounded retry, cache, and circuit breaker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import ProviderAudit, ProviderStatus, canonical_sha256, require_utc

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class ProviderTimeoutError(RuntimeError):
    pass


class ProviderRateLimitError(RuntimeError):
    pass


class ProviderBackend(Protocol):
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


class DeterministicOfflineProvider:
    provider_id = "deterministic_offline"
    provider_type = "offline_replay"
    model_id = "structured_passthrough"
    model_version = "1"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def invoke(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        response = request.get("structured_response")
        if not isinstance(response, Mapping):
            raise ValueError("offline_structured_response_required")
        return dict(response)


@dataclass(frozen=True)
class ProviderExecutionPolicy:
    timeout_seconds: float
    max_retry_attempts: int
    retry_backoff_seconds: float
    circuit_breaker_failure_threshold: int
    circuit_breaker_cooldown_seconds: int
    cache_ttl_seconds: int


@dataclass(frozen=True)
class ProviderExecutionResult:
    response: BaseModel | None
    audit: ProviderAudit


@dataclass(frozen=True)
class _CacheEntry:
    response_payload: dict[str, Any]
    response_hash: str
    expires_at: datetime


class ProviderAdapter:
    def __init__(
        self,
        backend: ProviderBackend,
        policy: ProviderExecutionPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.backend = backend
        self.policy = policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper or (lambda _seconds: None)
        self._cache: dict[str, _CacheEntry] = {}
        self._consecutive_failures = 0
        self._circuit_opened_at: datetime | None = None

    def reset_circuit(self) -> None:
        self._consecutive_failures = 0
        self._circuit_opened_at = None

    def execute(
        self,
        request: Mapping[str, Any],
        response_model: type[ResponseModelT],
        *,
        request_time_utc: datetime,
    ) -> ProviderExecutionResult:
        audit_time = require_utc(request_time_utc)
        request_hash = canonical_sha256(dict(request))
        request_id = f"provider-request-{request_hash}"
        if not self.backend.enabled:
            return self._result(
                request_id=request_id,
                audit_time=audit_time,
                status=ProviderStatus.DISABLED,
                attempt_count=0,
                error_reason="provider_disabled",
            )
        if self._circuit_is_open():
            return self._result(
                request_id=request_id,
                audit_time=audit_time,
                status=ProviderStatus.CIRCUIT_OPEN,
                attempt_count=0,
                error_reason="provider_circuit_open",
            )

        cache_key = canonical_sha256(
            {
                "provider_id": self.backend.provider_id,
                "model_id": self.backend.model_id,
                "model_version": self.backend.model_version,
                "response_schema": response_model.__name__,
                "request_hash": request_hash,
            }
        )
        cached = self._cache.get(cache_key)
        now = require_utc(self._clock())
        if cached is not None and cached.expires_at > now:
            response = response_model.model_validate(cached.response_payload)
            return self._result(
                request_id=request_id,
                audit_time=audit_time,
                status=ProviderStatus.SUCCESS,
                attempt_count=0,
                cache_hit=True,
                response=response,
                response_hash=cached.response_hash,
            )
        if cached is not None:
            self._cache.pop(cache_key, None)

        attempts = 0
        max_attempts = 1 + self.policy.max_retry_attempts
        final_status = ProviderStatus.ERROR
        final_reason = "provider_error"
        while attempts < max_attempts:
            attempts += 1
            try:
                raw = dict(
                    self.backend.invoke(
                        request,
                        timeout_seconds=self.policy.timeout_seconds,
                    )
                )
                response = response_model.model_validate(raw)
            except ProviderTimeoutError:
                final_status = ProviderStatus.TIMEOUT
                final_reason = "provider_timeout"
                self._backoff_before_retry(attempts, max_attempts)
                continue
            except ProviderRateLimitError:
                final_status = ProviderStatus.RATE_LIMITED
                final_reason = "provider_rate_limited"
                self._backoff_before_retry(attempts, max_attempts)
                continue
            except ValidationError:
                final_status = ProviderStatus.INVALID_RESPONSE
                final_reason = "provider_response_schema_invalid"
                break
            except (TypeError, ValueError):
                final_status = ProviderStatus.ERROR
                final_reason = "provider_backend_error"
                break
            response_payload = response.model_dump(mode="json")
            response_hash = canonical_sha256(response_payload)
            self._cache[cache_key] = _CacheEntry(
                response_payload=response_payload,
                response_hash=response_hash,
                expires_at=now + timedelta(seconds=self.policy.cache_ttl_seconds),
            )
            self.reset_circuit()
            return self._result(
                request_id=request_id,
                audit_time=audit_time,
                status=ProviderStatus.SUCCESS,
                attempt_count=attempts,
                response=response,
                response_hash=response_hash,
            )

        self._record_failure(now)
        return self._result(
            request_id=request_id,
            audit_time=audit_time,
            status=final_status,
            attempt_count=attempts,
            error_reason=final_reason,
        )

    def _backoff_before_retry(self, attempts: int, max_attempts: int) -> None:
        if attempts >= max_attempts:
            return
        delay = self.policy.retry_backoff_seconds * attempts
        if delay > 0.0:
            self._sleeper(delay)

    def _circuit_is_open(self) -> bool:
        opened = self._circuit_opened_at
        if opened is None:
            return False
        now = require_utc(self._clock())
        cooldown = timedelta(seconds=self.policy.circuit_breaker_cooldown_seconds)
        if now - opened >= cooldown:
            self.reset_circuit()
            return False
        return True

    def _record_failure(self, now: datetime) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.policy.circuit_breaker_failure_threshold:
            self._circuit_opened_at = now

    def _result(
        self,
        *,
        request_id: str,
        audit_time: datetime,
        status: ProviderStatus,
        attempt_count: int,
        cache_hit: bool = False,
        response: BaseModel | None = None,
        response_hash: str | None = None,
        error_reason: str | None = None,
    ) -> ProviderExecutionResult:
        audit = ProviderAudit(
            provider_id=self.backend.provider_id,
            provider_type=self.backend.provider_type,
            model_id=self.backend.model_id,
            model_version=self.backend.model_version,
            request_id=request_id,
            request_started_at=audit_time,
            request_completed_at=audit_time,
            latency_ms=0,
            timeout_seconds=self.policy.timeout_seconds,
            attempt_count=attempt_count,
            cache_hit=cache_hit,
            status=status,
            response_hash=response_hash,
            error_reason=error_reason,
        )
        return ProviderExecutionResult(response=response, audit=audit)


def latest_evidence_payload(
    evidence: tuple[Any, ...],
    context_type: str,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    matching = [item for item in evidence if item.context_type == context_type]
    if not matching:
        return None, ()
    matching.sort(key=lambda item: (item.available_at_utc, item.event_id))
    latest = matching[-1]
    return dict(latest.payload), tuple(item.event_id for item in matching)


def execute_structured_agent(
    *,
    agent_id: str,
    context_type: str,
    evidence: tuple[Any, ...],
    response_model: type[ResponseModelT],
    adapter: ProviderAdapter,
    decision_time_utc: datetime,
) -> tuple[ProviderExecutionResult | None, tuple[str, ...]]:
    payload, evidence_ids = latest_evidence_payload(evidence, context_type)
    if payload is None:
        return None, evidence_ids
    allowed = set(response_model.model_fields)
    structured = {key: value for key, value in payload.items() if key in allowed}
    result = adapter.execute(
        {
            "agent_id": agent_id,
            "context_type": context_type,
            "evidence_ids": list(evidence_ids),
            "structured_response": structured,
        },
        response_model,
        request_time_utc=decision_time_utc,
    )
    return result, evidence_ids
