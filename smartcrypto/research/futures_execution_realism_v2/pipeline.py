"""No-write-by-default research runner and B01/B02 evidence integration."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from smartcrypto.data.canonical_data_foundation_v2.contracts import stable_hash
from smartcrypto.data.canonical_data_foundation_v2.manifest import (
    ExecutionManifest,
    build_execution_manifest,
    write_execution_manifest,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWritePolicy,
    atomic_write_json,
    atomic_write_text,
)

from .contracts import (
    EventType,
    InputAuthority,
    MarginMode,
    MarketEvent,
    OrderIntent,
    OrderType,
    QueueModel,
    SAFETY_FLAGS,
    Side,
    SlippageModel,
    TimeInForce,
)
from .costs import CostModel
from .engine import EventDrivenExecutionEngine, ExecutionEngineConfig
from .latency import LatencyProfile, LatencySpec
from .margin import MaintenanceTier, MarginAccount, MarginEngine
from .reporting import render_execution_markdown

DEFAULT_JSON = "data/reports/futures_execution_realism_engine_v2.json"
DEFAULT_MARKDOWN = "data/reports/futures_execution_realism_engine_v2.md"
DEFAULT_MANIFEST_ROOT = "data/reports/futures_execution_realism_engine_v2/manifests"
FIXTURE_TIME = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
Clock = Callable[[], datetime]


def build_futures_execution_realism_report(
    *,
    project_root: str | Path,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON,
    output_markdown: str | Path = DEFAULT_MARKDOWN,
    manifest_output_root: str | Path = DEFAULT_MANIFEST_ROOT,
    seed: int = 42,
    input_mode: str = "synthetic_fixture",
    command: str = "scripts/build_futures_execution_realism_engine_v2.py",
    arguments: Sequence[str] = (),
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Run the deterministic built-in fixture and optionally persist evidence."""

    root = Path(project_root).resolve()
    now = _utc_now(clock)
    events, intents, authority = build_synthetic_fixture(input_mode=input_mode)
    config = default_engine_config(seed=seed)
    cost_model = default_cost_model()
    margin_engine = default_margin_engine(cost_model)
    engine = EventDrivenExecutionEngine(
        config=config,
        cost_model=cost_model,
        margin_engine=margin_engine,
    )
    result = engine.run(
        events=events,
        intents=intents,
        input_authority=authority,
        initial_account=MarginAccount(
            wallet_balance=Decimal("10000"),
            available_balance=Decimal("10000"),
        ),
        correlations=None,
    )
    event_payload = [event.to_dict() for event in events]
    intent_payload = [intent.to_dict() for intent in intents]
    dataset_hash = stable_hash(
        {
            "events": event_payload,
            "intents": intent_payload,
            "authority": authority.to_dict(),
        }
    )
    dataset_manifest_hash = stable_hash(
        {
            "schema_version": "futures_execution_fixture_manifest_v2",
            "dataset_hash": dataset_hash,
            "fixture_only": authority.fixture_only,
            "authoritative": authority.authoritative,
            "row_count": len(events) + len(intents),
        }
    )
    git_state = _read_git_state(root)
    dependency_lock_hash = _optional_file_hash(root / "requirements-dev.lock")
    schema_hash = stable_hash(
        {
            "schema": "futures_execution_realism_engine_v2",
            "event_types": sorted(item.value for item in EventType),
        }
    )
    manifest = build_execution_manifest(
        execution_id=f"b03_{result.deterministic_result_hash[:24]}",
        execution_type="backtest",
        execution_started_at_utc=now.isoformat(),
        execution_completed_at_utc=now.isoformat(),
        project="SMART FUTUROS",
        branch=git_state["branch"],
        commit_sha=git_state["commit_sha"],
        dirty_worktree=git_state["dirty_worktree"],
        containerized=False,
        container_digest=None,
        runtime_environment={
            "execution_boundary": "research_only",
            "fixture_only": authority.fixture_only,
            "platform": platform.system(),
        },
        python_version=platform.python_version(),
        dependency_lock_hash=dependency_lock_hash,
        dataset_id="b03_synthetic_execution_fixture_v2",
        dataset_hash=dataset_hash,
        dataset_manifest_hash=dataset_manifest_hash,
        feature_contract_hash=None,
        target_store_hash=None,
        split_hash=None,
        cost_model_hash=cost_model.cost_model_hash,
        config_hash=config.config_hash,
        schema_hash=schema_hash,
        source_hashes={"synthetic_fixture": authority.source_hash or dataset_hash},
        seed=seed,
        command=command,
        arguments=arguments,
        row_count=len(events) + len(intents),
        status=result.status,
        blockers=tuple(result.blockers)
        + (("fixture_only_non_authoritative",) if authority.fixture_only else ()),
        warnings=result.warnings,
        safety_flags=SAFETY_FLAGS,
    )
    report = {
        "generated_at_utc": now.isoformat(),
        **result.to_dict(include_records=True),
        "schema_version": "futures_execution_realism_engine_report_v2",
        "input_mode": input_mode,
        "authoritative_input_rows": (
            len(events) + len(intents) if authority.authoritative else 0
        ),
        "quarantined_input_rows": (
            len(events) + len(intents) if authority.quarantined else 0
        ),
        "fixture_only_runs": 1 if authority.fixture_only else 0,
        "event_count": len(events),
        "order_count": len(result.orders),
        "fill_count": len(result.fills),
        "event_types_supported": [item.value for item in EventType],
        "order_types_supported": [item.value for item in OrderType],
        "time_in_force_supported": [item.value for item in TimeInForce],
        "queue_models_supported": [item.value for item in QueueModel],
        "slippage_models_supported": [item.value for item in SlippageModel],
        "engine_config": config.to_dict(),
        "cost_model": cost_model.to_dict(),
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": dataset_manifest_hash,
        "execution_manifest": manifest.to_dict(),
        "execution_manifest_content_hash": manifest.content_hash,
        "manifest_reproducible": _manifest_reproducible(manifest, now),
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_json": _display_path(output_json, root),
        "output_markdown": _display_path(output_markdown, root),
        "manifest_output_root": _display_path(manifest_output_root, root),
        "manifest_write_performed": False,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    if not write_report:
        return report

    reports_root = (root / "data" / "reports").resolve(strict=False)
    json_target = _authorized_report_target(output_json, root, reports_root)
    markdown_target = _authorized_report_target(
        output_markdown, root, reports_root
    )
    policy = AtomicWritePolicy.restricted(
        [reports_root],
        working_directory=root,
    )
    write_payload = {
        **report,
        "write_performed": True,
        "manifest_write_performed": True,
    }
    atomic_write_json(
        json_target,
        write_payload,
        policy=policy,
        allow_nan=False,
    )
    atomic_write_text(
        markdown_target,
        render_execution_markdown(write_payload),
        policy=policy,
    )
    manifest_write = write_execution_manifest(
        manifest=manifest,
        output_root=manifest_output_root,
        project_root=root,
    )
    return {
        **write_payload,
        "manifest_path": manifest_write["manifest_path"],
        "atomic_writer": "integrity_traceability_v2.atomic_writer",
    }


def build_synthetic_fixture(
    *,
    input_mode: str = "synthetic_fixture",
) -> tuple[tuple[MarketEvent, ...], tuple[OrderIntent, ...], InputAuthority]:
    """Build a small sanitized fixture; it can never be authoritative."""

    source_payload = {
        "fixture": "b03_synthetic_execution_fixture_v2",
        "base_time": FIXTURE_TIME.isoformat(),
    }
    source_hash = stable_hash(source_payload)
    authority = InputAuthority(
        dataset_class="synthetic_execution_fixture",
        lineage_status=(
            "PERMANENT_QUARANTINE"
            if input_mode == "legacy_quarantined"
            else "VERIFIED"
        ),
        candle_status=(
            "PERMANENT_QUARANTINE"
            if input_mode == "legacy_quarantined"
            else "VERIFIED"
        ),
        fixture_only=input_mode == "synthetic_fixture",
        legacy_research_non_authoritative=input_mode == "legacy_quarantined",
        source_hash=source_hash,
    )
    snapshots = (
        _event(
            EventType.BOOK_SNAPSHOT,
            FIXTURE_TIME,
            1,
            source_hash,
            {
                "bids": [["99.5", "2"], ["99.0", "4"]],
                "asks": [["100.5", "1"], ["101.0", "4"]],
            },
        ),
        _event(
            EventType.MARK_PRICE,
            FIXTURE_TIME + timedelta(seconds=1),
            2,
            source_hash,
            {"mark_price": "100"},
        ),
        _event(
            EventType.FUNDING_RATE,
            FIXTURE_TIME + timedelta(seconds=2),
            3,
            source_hash,
            {"funding_rate": "0.0001", "mark_price": "100"},
        ),
        _event(
            EventType.BOOK_SNAPSHOT,
            FIXTURE_TIME + timedelta(seconds=10),
            4,
            source_hash,
            {
                "bids": [["100.0", "4"], ["99.5", "4"]],
                "asks": [["101.0", "4"], ["101.5", "4"]],
            },
        ),
        _event(
            EventType.MARK_PRICE,
            FIXTURE_TIME + timedelta(seconds=11),
            5,
            source_hash,
            {"mark_price": "100.5"},
        ),
    )
    intents = (
        OrderIntent(
            symbol="BTCUSDT",
            side=Side.BUY,
            quantity=Decimal("1.5"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,
            submit_time_utc=FIXTURE_TIME + timedelta(milliseconds=500),
            client_intent_id="fixture_open",
        ),
        OrderIntent(
            symbol="BTCUSDT",
            side=Side.SELL,
            quantity=Decimal("1.5"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,
            submit_time_utc=FIXTURE_TIME + timedelta(seconds=10, milliseconds=500),
            reduce_only=True,
            client_intent_id="fixture_close",
        ),
    )
    return snapshots, intents, authority


def default_engine_config(*, seed: int = 42) -> ExecutionEngineConfig:
    latency = LatencyProfile(
        signal_to_submit=LatencySpec(constant_ms=Decimal("2")),
        client_to_exchange=LatencySpec(constant_ms=Decimal("3")),
        exchange_ack=LatencySpec(constant_ms=Decimal("1")),
        market_data=LatencySpec(constant_ms=Decimal("1")),
        cancel=LatencySpec(constant_ms=Decimal("5")),
        reprice=LatencySpec(constant_ms=Decimal("5")),
        jitter=LatencySpec(constant_ms=Decimal("0")),
    )
    return ExecutionEngineConfig(
        seed=seed,
        latency=latency,
        queue_model=QueueModel.PESSIMISTIC,
        contract_size=Decimal("1"),
        leverage=Decimal("5"),
        margin_mode=MarginMode.ISOLATED,
        stale_book_after_ms=5_000,
        order_timeout_ms=30_000,
    )


def default_cost_model() -> CostModel:
    return CostModel(
        maker_fee_bps=Decimal("2"),
        taker_fee_bps=Decimal("4"),
        slippage_model=SlippageModel.CONSERVATIVE_HYBRID,
        fixed_slippage_bps=Decimal("1"),
        square_root_impact_coefficient=Decimal("0.1"),
        liquidation_penalty_bps=Decimal("50"),
    )


def default_margin_engine(cost_model: CostModel) -> MarginEngine:
    return MarginEngine(
        tiers=(
            MaintenanceTier(
                notional_cap=Decimal("50000"),
                maintenance_margin_rate=Decimal("0.005"),
            ),
            MaintenanceTier(
                notional_cap=Decimal("500000"),
                maintenance_margin_rate=Decimal("0.01"),
            ),
        ),
        cost_model=cost_model,
    )


def _event(
    event_type: EventType,
    event_time: datetime,
    sequence: int,
    source_hash: str,
    payload: Mapping[str, Any],
) -> MarketEvent:
    receive_time = event_time + timedelta(milliseconds=1)
    return MarketEvent.create(
        event_type=event_type,
        symbol="BTCUSDT",
        event_time_utc=event_time,
        receive_time_utc=receive_time,
        sequence=sequence,
        source="b03_synthetic_fixture",
        source_hash=source_hash,
        payload=payload,
    )


def _read_git_state(root: Path) -> dict[str, Any]:
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current") or "unresolved"
    status = _git(root, "status", "--porcelain", allow_empty=True)
    return {
        "commit_sha": commit if len(commit) == 40 else None,
        "branch": branch,
        "dirty_worktree": bool(status),
    }


def _git(
    root: Path,
    *arguments: str,
    allow_empty: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = completed.stdout.strip()
    return value if value or allow_empty else ""


def _optional_file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_reproducible(
    manifest: ExecutionManifest,
    now: datetime,
) -> bool:
    replay = replace(
        manifest,
        envelope={
            "execution_id": manifest.envelope["execution_id"],
            "execution_started_at_utc": (now + timedelta(seconds=1)).isoformat(),
            "execution_completed_at_utc": (now + timedelta(seconds=2)).isoformat(),
        },
    )
    return replay.content_hash == manifest.content_hash


def _utc_now(clock: Clock | None) -> datetime:
    value = clock() if clock is not None else datetime.now(tz=UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock_must_return_timezone_aware_datetime")
    return value.astimezone(UTC)


def _authorized_report_target(
    value: str | Path,
    root: Path,
    reports_root: Path,
) -> Path:
    requested = Path(value)
    target = requested if requested.is_absolute() else root / requested
    target = target.resolve(strict=False)
    try:
        target.relative_to(reports_root)
    except ValueError as exc:
        raise ValueError("report_output_outside_data_reports") from exc
    return target


def _display_path(value: str | Path, root: Path) -> str:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()
