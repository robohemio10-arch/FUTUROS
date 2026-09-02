"""Deterministic point-in-time Market Intelligence snapshot construction."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import ValidationError

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
    resolve_authorized_target,
)

from .contracts import (
    CORE_FEATURE_FAMILIES,
    FeatureFamilyHealth,
    FreshnessStatus,
    MarketEvent,
    MarketIntelligenceConfig,
    MarketIntelligenceRequest,
    MarketIntelligenceRunReport,
    MarketIntelligenceSnapshot,
    MarketIntelligenceStatus,
    SourceWatermark,
    canonical_sha256,
)
from .feature_builder import build_feature_families, feature_definitions
from .news_context import extract_research_council_context

_EVENT_FAMILY = {
    "agg_trade": "flow",
    "book_ticker": "spread",
    "mark_price": "basis_funding",
    "open_interest": "open_interest",
    "liquidation": "liquidations",
}


def load_market_intelligence_config(
    project_root: str | Path,
    config_path: str | Path = "config/research/market_intelligence.yaml",
) -> MarketIntelligenceConfig:
    root = Path(project_root).resolve()
    candidate = Path(config_path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    candidate = candidate.resolve(strict=False)
    if candidate.is_symlink():
        raise ValueError("market_intelligence_config_symlink_forbidden")
    if not candidate.is_file():
        raise ValueError("market_intelligence_config_missing")
    payload = yaml.safe_load(candidate.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("market_intelligence_config_root_must_be_mapping")
    return MarketIntelligenceConfig.model_validate(payload)


class MarketIntelligenceService:
    def __init__(self, config: MarketIntelligenceConfig) -> None:
        self.config = config

    def evaluate(
        self,
        request: MarketIntelligenceRequest,
        *,
        project_root: str | Path,
        write_report: bool = False,
        output_json: str | Path | None = None,
    ) -> MarketIntelligenceRunReport:
        unauthorized_sources = sorted(
            {event.source_id for event in request.events}
            - set(self.config.allowed_public_sources)
        )
        if unauthorized_sources:
            return blocked_report(
                "UNAUTHORIZED_PUBLIC_SOURCE:" + ",".join(unauthorized_sources),
                request_id=request.request_id,
                input_event_count=len(request.events),
                invalid_event_count=0,
                write_requested=write_report,
            )
        pit_errors = request.point_in_time_errors()
        if pit_errors:
            invalid_event_count = sum(
                bool(event.point_in_time_errors(request.decision_time_utc))
                for event in request.events
            )
            return blocked_report(
                "INVALID_POINT_IN_TIME:" + ";".join(pit_errors[:8]),
                request_id=request.request_id,
                input_event_count=len(request.events),
                invalid_event_count=invalid_event_count,
                write_requested=write_report,
            )
        try:
            research_context = extract_research_council_context(
                request.research_council_snapshot,
                symbol=request.symbol,
                decision_time_utc=request.decision_time_utc,
            )
            features = build_feature_families(
                request.events,
                decision_time_utc=request.decision_time_utc,
                config=self.config,
            )
        except (ValueError, ValidationError) as exc:
            return blocked_report(
                f"INVALID_MARKET_INTELLIGENCE_INPUT:{str(exc).splitlines()[0][:240]}",
                request_id=request.request_id,
                input_event_count=len(request.events),
                invalid_event_count=0,
                write_requested=write_report,
            )
        health = _family_health(request.events, request.decision_time_utc, self.config)
        watermarks = build_source_watermarks(request.events)
        enabled = tuple(self.config.enabled_feature_families)
        available = tuple(
            family
            for family in enabled
            if health[family].status in {FreshnessStatus.FRESH, FreshnessStatus.STALE}
            and bool(features.get(family))
        )
        missing = tuple(family for family in enabled if family not in available)
        coverage = len(available) / len(enabled) if enabled else 0.0
        status = "SUCCESS" if not missing else "PARTIAL"
        reason = (
            "all_enabled_feature_families_available"
            if not missing
            else "feature_families_missing_or_unavailable"
        )
        manifest = tuple(
            definition
            for definition in feature_definitions(self.config)
            if definition.feature_family in enabled
        )
        semantic_payload = {
            "schema_version": "market_intelligence_snapshot_v1",
            "exchange": request.exchange,
            "symbol": request.symbol,
            "decision_time_utc": request.decision_time_utc,
            "source_watermarks": [item.model_dump(mode="json") for item in watermarks],
            "features": {family: features.get(family) for family in enabled},
            "feature_family_statuses": {
                family: health[family].model_dump(mode="json") for family in enabled
            },
            "feature_manifest": [item.model_dump(mode="json") for item in manifest],
            "research_council_context": research_context,
        }
        snapshot_id = f"market-intelligence-{canonical_sha256(semantic_payload)}"
        snapshot = MarketIntelligenceSnapshot(
            snapshot_id=snapshot_id,
            status=status,
            reason=reason,
            exchange=request.exchange,
            symbol=request.symbol,
            decision_time_utc=request.decision_time_utc,
            created_at_utc=request.decision_time_utc,
            source_watermarks=watermarks,
            flow_features=features.get("flow") if "flow" in enabled else None,
            spread_features=features.get("spread") if "spread" in enabled else None,
            basis_funding_features=(
                features.get("basis_funding") if "basis_funding" in enabled else None
            ),
            open_interest_features=(
                features.get("open_interest") if "open_interest" in enabled else None
            ),
            liquidation_features=(
                features.get("liquidations") if "liquidations" in enabled else None
            ),
            research_council_context=research_context,
            feature_family_statuses={family: health[family] for family in enabled},
            feature_manifest=manifest,
            coverage=coverage,
            available_feature_families=available,
            missing_feature_families=missing,
        )
        write_performed = False
        output_paths: dict[str, str] = {}
        if write_report:
            try:
                persisted = persist_snapshot(
                    project_root=project_root,
                    snapshot=snapshot,
                    output_json=output_json,
                )
            except MarketIntelligencePersistenceError as exc:
                return blocked_report(
                    exc.reason,
                    request_id=request.request_id,
                    input_event_count=len(request.events),
                    invalid_event_count=0,
                    write_requested=True,
                    write_performed=exc.write_performed,
                    snapshot=snapshot,
                )
            write_performed = bool(persisted["write_performed"])
            output_paths = dict(persisted["output_paths"])
        return MarketIntelligenceRunReport(
            status=(
                MarketIntelligenceStatus.SUCCESS
                if snapshot.status == "SUCCESS"
                else MarketIntelligenceStatus.PARTIAL
            ),
            reason=snapshot.reason or "ok",
            request_id=request.request_id,
            input_event_count=len(request.events),
            valid_point_in_time_event_count=len(request.events),
            invalid_point_in_time_event_count=0,
            snapshot=snapshot,
            write_requested=write_report,
            write_performed=write_performed,
            output_paths=output_paths,
        )


class MarketIntelligencePersistenceError(RuntimeError):
    def __init__(self, reason: str, *, write_performed: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.write_performed = write_performed


def persist_snapshot(
    *,
    project_root: str | Path,
    snapshot: MarketIntelligenceSnapshot,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    research_root = root / "data" / "research" / "market_intelligence"
    policy = AtomicWritePolicy.restricted((research_root,), working_directory=root)
    try:
        target = (
            research_root / snapshot.snapshot_id / "market_intelligence_snapshot.json"
            if output_json is None
            else resolve_authorized_target(output_json, policy=policy)
        )
    except AtomicWriteError as exc:
        raise MarketIntelligencePersistenceError(exc.reason) from exc
    written = _write_once(target, snapshot.model_dump(mode="json"), policy=policy)
    return {
        "write_performed": written,
        "output_paths": {"snapshot": target.relative_to(root).as_posix()},
    }


def _write_once(
    target: Path,
    payload: dict[str, Any],
    *,
    policy: AtomicWritePolicy,
) -> bool:
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise MarketIntelligencePersistenceError("existing_output_not_regular_file")
        try:
            existing = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MarketIntelligencePersistenceError("existing_output_unreadable") from exc
        if existing == payload:
            return False
        raise MarketIntelligencePersistenceError("deterministic_output_conflict")
    try:
        result = atomic_write_json(
            target,
            payload,
            policy=policy,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (AtomicWriteError, OSError, ValueError) as exc:
        raise MarketIntelligencePersistenceError("market_intelligence_write_failed") from exc
    return result.write_performed

def blocked_report(
    reason: str,
    *,
    request_id: str | None = None,
    input_event_count: int = 0,
    invalid_event_count: int = 0,
    write_requested: bool = False,
    write_performed: bool = False,
    snapshot: MarketIntelligenceSnapshot | None = None,
) -> MarketIntelligenceRunReport:
    return MarketIntelligenceRunReport(
        status=MarketIntelligenceStatus.BLOCKED,
        reason=reason,
        request_id=request_id,
        input_event_count=input_event_count,
        valid_point_in_time_event_count=max(0, input_event_count - invalid_event_count),
        invalid_point_in_time_event_count=invalid_event_count,
        snapshot=snapshot,
        write_requested=write_requested,
        write_performed=write_performed,
    )


def build_source_watermarks(events: Iterable[MarketEvent]) -> tuple[SourceWatermark, ...]:
    grouped: dict[tuple[str, str], list[MarketEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.source_id, event.event_type)].append(event)
    watermarks: list[SourceWatermark] = []
    for (source_id, event_type), rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda item: (item.event_time_utc, item.event_id))
        hashes = [item.effective_hash() for item in ordered]
        watermarks.append(
            SourceWatermark(
                source_id=source_id,
                exchange=ordered[0].exchange,
                symbol=ordered[0].symbol,
                event_type=event_type,
                min_event_time_utc=min(item.event_time_utc for item in ordered),
                max_event_time_utc=max(item.event_time_utc for item in ordered),
                min_available_at_utc=min(item.available_at_utc for item in ordered),
                max_available_at_utc=max(item.available_at_utc for item in ordered),
                row_count=len(ordered),
                source_hash=canonical_sha256(hashes),
                loader_version="market_intelligence_event_loader_v1",
                schema_version="market_intelligence_event_v1",
            )
        )
    return tuple(watermarks)


def _family_health(
    events: Iterable[MarketEvent],
    decision_time_utc: datetime,
    config: MarketIntelligenceConfig,
) -> dict[str, FeatureFamilyHealth]:
    by_family: dict[str, list[MarketEvent]] = defaultdict(list)
    for event in events:
        by_family[_EVENT_FAMILY[event.event_type]].append(event)
    health: dict[str, FeatureFamilyHealth] = {}
    for family in CORE_FEATURE_FAMILIES:
        threshold = float(config.freshness_thresholds_seconds[family])
        rows = by_family.get(family, [])
        if not rows:
            status = (
                FreshnessStatus.MISSING
                if config.real_source_available.get(family, False)
                else FreshnessStatus.SOURCE_UNAVAILABLE
            )
            health[family] = FeatureFamilyHealth(
                family=family,
                status=status,
                max_age_seconds=threshold,
                event_count=0,
                reason=(
                    "no_causal_events_in_request"
                    if status is FreshnessStatus.MISSING
                    else "real_public_source_not_proven_in_current_repo"
                ),
            )
            continue
        latest_event = max(item.event_time_utc for item in rows)
        latest_available = max(item.available_at_utc for item in rows)
        age = max(0.0, (decision_time_utc - latest_available).total_seconds())
        status = FreshnessStatus.FRESH if age <= threshold else FreshnessStatus.STALE
        health[family] = FeatureFamilyHealth(
            family=family,
            status=status,
            latest_event_time_utc=latest_event,
            latest_available_at_utc=latest_available,
            age_seconds=age,
            max_age_seconds=threshold,
            event_count=len(rows),
            reason=(
                "within_freshness_threshold"
                if status is FreshnessStatus.FRESH
                else "freshness_threshold_exceeded"
            ),
        )
    return health
